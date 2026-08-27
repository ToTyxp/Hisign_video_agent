"""Real API smoke for seven versioned subtype query vectors; no candidate data."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence

from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.qwen_embedding import (
    QWEN_SCHEMA,
    DashScopeQwenEmbeddingProvider,
    EmbeddingProviderError,
)
from surveillance_video_agent.semantic_queries import SemanticQueryVectorService
from surveillance_video_agent.smoke import network_environment_summary
from surveillance_video_agent.vector_index import QdrantVectorIndex


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUERY_PACKS = (
    ROOT
    / "query-packs/demand_action_v1/demand_action_v1.qp.v1.2.0.json",
    ROOT
    / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.2.0.json",
)


def run_semantic_query_smoke(
    *,
    provider: Any | None = None,
    query_pack_paths: Sequence[Path] = DEFAULT_QUERY_PACKS,
    keep_temp: bool = False,
    environment=None,
) -> dict[str, Any]:
    active_provider = provider or DashScopeQwenEmbeddingProvider()
    temp_root = Path(tempfile.mkdtemp(prefix="sva-semantic-query-smoke-")).resolve()
    run_id = "semantic-query-smoke-" + str(uuid.uuid4())
    report: dict[str, Any] = {
        "ok": False,
        "run_id": run_id,
        "model": active_provider.model_id,
        "provider": active_provider.provider_id,
        "schema_version": QWEN_SCHEMA.version,
        "candidate_metadata_sent": False,
        "query_pack_count": len(query_pack_paths),
        "network_environment": network_environment_summary(environment),
        "temp_root": str(temp_root),
        "temp_cleaned": False,
    }
    database: CandidateDatabase | None = None
    index: QdrantVectorIndex | None = None
    try:
        database = CandidateDatabase(temp_root / "candidates.sqlite3")
        database.initialize()
        database.create_run(run_id, "semantic-query-smoke")
        index = QdrantVectorIndex(temp_root / "qdrant")
        service = SemanticQueryVectorService(
            database,
            index,
            active_provider,
            QWEN_SCHEMA,
        )
        campaigns = []
        for path in query_pack_paths:
            first = service.prepare(run_id=run_id, query_pack_path=Path(path))
            second = service.prepare(run_id=run_id, query_pack_path=Path(path))
            campaigns.append(
                {
                    "campaign_id": first.campaign_id,
                    "query_pack_version": first.query_pack_version,
                    "subtypes": sorted(first.vectors),
                    "generated_count": first.generated_count,
                    "first_cached_count": first.cached_count,
                    "second_generated_count": second.generated_count,
                    "second_cached_count": second.cached_count,
                    "dimensions": sorted({len(vector) for vector in first.vectors.values()}),
                }
            )
        ready = int(
            database.connection.execute(
                "SELECT COUNT(*) FROM subtype_semantic_queries WHERE index_status = 'ready'"
            ).fetchone()[0]
        )
        calls = int(
            database.connection.execute(
                "SELECT COUNT(*) FROM embedding_calls WHERE status = 'succeeded'"
            ).fetchone()[0]
        )
        report["campaigns"] = campaigns
        report["ready_query_vectors"] = ready
        report["successful_embedding_calls"] = calls
        report["cache_reuse_passed"] = all(
            item["second_generated_count"] == 0
            and item["second_cached_count"] == item["generated_count"]
            for item in campaigns
        )
        report["ok"] = (
            ready == 7
            and calls == len(query_pack_paths)
            and report["cache_reuse_passed"]
            and all(item["dimensions"] == [1024] for item in campaigns)
        )
    finally:
        if index is not None:
            index.close()
        if database is not None:
            database.close()
        if not keep_temp:
            shutil.rmtree(temp_root)
            report["temp_cleaned"] = True
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-temp", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_semantic_query_smoke(keep_temp=args.keep_temp)
    except EmbeddingProviderError as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_kind": error.kind.value,
                    "error": error.message,
                    "status_code": error.status_code,
                },
                ensure_ascii=False,
            )
        )
        return 2
    except (ValueError, RuntimeError) as error:
        print(
            json.dumps(
                {"ok": False, "error_kind": "smoke", "error": str(error)},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
