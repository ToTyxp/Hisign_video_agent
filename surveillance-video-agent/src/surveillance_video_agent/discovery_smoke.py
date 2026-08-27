"""Bounded online smoke for the persisted three-platform discovery workflow."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from surveillance_video_agent.adapters import (
    DailymotionAdapter,
    PeerTubeAdapter,
    PlatformAdapter,
    YouTubeAdapter,
)
from surveillance_video_agent.adapters.base import sanitize_error_text
from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.discovery import (
    DiscoveryConfig,
    DiscoveryService,
)
from surveillance_video_agent.scoring import load_scoring_bundle
from surveillance_video_agent.smoke import load_frozen_query, network_environment_summary


@dataclass(frozen=True, slots=True)
class DiscoverySmokeConfig:
    query_pack_path: Path
    scoring_policy_path: Path
    scoring_query_pack_paths: tuple[Path, ...]
    query_id: str
    peertube_instance_hosts: tuple[str, ...]
    search_limit: int = 3
    probe_limit: int = 9
    max_requests_per_platform: int = 2
    probe_timeout_seconds: float = 45.0
    keep_temp: bool = False
    run_id: str = "online-discovery-smoke"

    def __post_init__(self) -> None:
        object.__setattr__(self, "query_pack_path", Path(self.query_pack_path).resolve())
        object.__setattr__(
            self, "scoring_policy_path", Path(self.scoring_policy_path).resolve()
        )
        object.__setattr__(
            self,
            "scoring_query_pack_paths",
            tuple(Path(path).resolve() for path in self.scoring_query_pack_paths),
        )
        if not self.query_id or not self.run_id:
            raise ValueError("query_id and run_id are required")
        if not self.scoring_query_pack_paths:
            raise ValueError("at least one scoring query pack is required")
        if self.query_pack_path not in self.scoring_query_pack_paths:
            raise ValueError("selected query pack must be part of the scoring bundle")
        if not self.peertube_instance_hosts:
            raise ValueError("online discovery smoke requires a PeerTube allowlist")
        if not 1 <= self.search_limit <= 3:
            raise ValueError("online discovery search_limit must be between 1 and 3")
        if not 1 <= self.probe_limit <= 9:
            raise ValueError("online discovery probe_limit must be between 1 and 9")
        if not 1 <= self.max_requests_per_platform <= 2:
            raise ValueError("max_requests_per_platform must be 1 or 2")
        if self.probe_timeout_seconds <= 0:
            raise ValueError("probe_timeout_seconds must be positive")


def build_adapters(config: DiscoverySmokeConfig) -> dict[str, PlatformAdapter]:
    return {
        "youtube": YouTubeAdapter(),
        "dailymotion": DailymotionAdapter(),
        "peertube": PeerTubeAdapter(
            allowed_instance_hosts=config.peertube_instance_hosts
        ),
    }


def run_discovery_smoke(
    config: DiscoverySmokeConfig,
    *,
    adapters: Mapping[str, PlatformAdapter] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run a no-download discovery smoke in an automatically cleaned temp root."""

    query = load_frozen_query(config.query_pack_path, config.query_id)
    scoring = load_scoring_bundle(
        config.scoring_policy_path,
        config.scoring_query_pack_paths,
    )
    active_adapters = dict(adapters or build_adapters(config))
    temp_root = Path(tempfile.mkdtemp(prefix="sva-discovery-smoke-")).resolve()
    report: dict[str, Any] = {
        "ok": False,
        "run_id": config.run_id,
        "query_pack_version": query.query_pack_version,
        "query_id": query.query_id,
        "query": query.query,
        "lang": query.lang,
        "network_config": "default",
        "network_environment": network_environment_summary(environment),
        "peertube_allowlist": list(config.peertube_instance_hosts),
        "download_attempted": False,
        "temp_root": str(temp_root),
        "temp_cleaned": False,
    }
    database: CandidateDatabase | None = None
    try:
        database = CandidateDatabase(temp_root / "candidates.sqlite3")
        database.initialize()
        database.register_frozen_query_pack(config.query_pack_path)
        database.create_run(
            config.run_id,
            "online-discovery-smoke",
            config={
                "query_pack_version": query.query_pack_version,
                "query_id": query.query_id,
                "search_limit": config.search_limit,
                "probe_limit": config.probe_limit,
                "network_config": "default",
            },
        )
        service = DiscoveryService(database, active_adapters, scoring)
        workflow_config = DiscoveryConfig(
            campaign_id=query.campaign_id,
            query_pack_version=query.query_pack_version,
            query_ids=(query.query_id,),
            per_query_limit=config.search_limit,
            probe_limit=config.probe_limit,
            max_requests_per_platform=config.max_requests_per_platform,
            probe_timeout_seconds=config.probe_timeout_seconds,
        )
        discovery = service.discover(run_id=config.run_id, config=workflow_config)
        qualification = service.qualify(run_id=config.run_id, config=workflow_config)
        report["discovery"] = asdict(discovery)
        report["qualification"] = asdict(qualification)
        report["platforms"] = _platform_evidence(database, config.run_id, query.query_id)
        report["database"] = _database_evidence(database, config.run_id)
        report["ok"] = all(item["ok"] for item in report["platforms"])
        with database.transaction() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?, finished_at = ?, result_json = ?
                WHERE run_id = ?
                """,
                (
                    "completed" if report["ok"] else "failed",
                    utc_now(),
                    json.dumps(
                        {"ok": report["ok"]},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    config.run_id,
                ),
            )
    finally:
        if database is not None:
            database.close()
        if not config.keep_temp:
            shutil.rmtree(temp_root)
            report["temp_cleaned"] = True
    return report


def _platform_evidence(
    database: CandidateDatabase,
    run_id: str,
    query_id: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for platform in ("youtube", "dailymotion", "peertube"):
        calls = database.connection.execute(
            """
            SELECT operation, status, COUNT(*) AS count
            FROM adapter_calls
            WHERE run_id = ? AND platform = ?
            GROUP BY operation, status
            """,
            (run_id, platform),
        ).fetchall()
        call_counts = {
            f"{row['operation']}_{row['status']}": int(row["count"])
            for row in calls
        }
        failures = database.connection.execute(
            """
            SELECT operation, error_kind, COUNT(*) AS count
            FROM adapter_calls
            WHERE run_id = ? AND platform = ? AND status = 'failed'
            GROUP BY operation, error_kind
            ORDER BY operation, error_kind
            """,
            (run_id, platform),
        ).fetchall()
        discovered = int(
            database.connection.execute(
                """
                SELECT COUNT(DISTINCT c.candidate_key)
                FROM candidates c
                JOIN candidate_discoveries d
                  ON d.candidate_key = c.candidate_key
                WHERE c.platform = ? AND d.query_id = ?
                """,
                (platform, query_id),
            ).fetchone()[0]
        )
        qualified = int(
            database.connection.execute(
                """
                SELECT COUNT(*) FROM candidates
                WHERE platform = ? AND status = 'source_qualified'
                """,
                (platform,),
            ).fetchone()[0]
        )
        search_ok = call_counts.get("search_succeeded", 0) == 1
        probe_ok = call_counts.get("probe_succeeded", 0) >= 1
        result.append(
            {
                "platform": platform,
                "ok": search_ok and discovered >= 1 and probe_ok,
                "discovered_candidates": discovered,
                "source_qualified_candidates": qualified,
                "calls": call_counts,
                "failures": [
                    {
                        "operation": row["operation"],
                        "error_kind": row["error_kind"],
                        "count": int(row["count"]),
                    }
                    for row in failures
                ],
            }
        )
    return result


def _database_evidence(database: CandidateDatabase, run_id: str) -> dict[str, int]:
    def count(table: str) -> int:
        allowed = {
            "candidates",
            "candidate_discoveries",
            "probe_selections",
            "adapter_calls",
            "candidate_task_scores",
            "state_transitions",
        }
        if table not in allowed:
            raise ValueError("unsupported evidence table")
        return int(database.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    return {
        "candidates": count("candidates"),
        "discoveries": count("candidate_discoveries"),
        "probe_selections": count("probe_selections"),
        "adapter_calls": count("adapter_calls"),
        "task_scores": count("candidate_task_scores"),
        "state_transitions": count("state_transitions"),
        "failed_adapter_calls": int(
            database.connection.execute(
                """
                SELECT COUNT(*) FROM adapter_calls
                WHERE run_id = ? AND status = 'failed'
                """,
                (run_id,),
            ).fetchone()[0]
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query-pack", required=True, type=Path)
    parser.add_argument("--scoring-policy", required=True, type=Path)
    parser.add_argument(
        "--scoring-query-pack", required=True, action="append", type=Path
    )
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--peertube-instance", required=True, action="append")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--probe-limit", type=int, default=9)
    parser.add_argument("--probe-timeout", type=float, default=45.0)
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--run-id", default="online-discovery-smoke")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_discovery_smoke(
            DiscoverySmokeConfig(
                query_pack_path=args.query_pack,
                scoring_policy_path=args.scoring_policy,
                scoring_query_pack_paths=tuple(args.scoring_query_pack),
                query_id=args.query_id,
                peertube_instance_hosts=tuple(args.peertube_instance),
                search_limit=args.limit,
                probe_limit=args.probe_limit,
                probe_timeout_seconds=args.probe_timeout,
                keep_temp=args.keep_temp,
                run_id=args.run_id,
            )
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": sanitize_error_text(
                        str(error) or type(error).__name__, max_length=500
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
