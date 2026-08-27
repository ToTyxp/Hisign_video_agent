"""Reproducible, bounded online smoke runner for platform adapters."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from surveillance_video_agent.adapters import (
    DailymotionAdapter,
    PeerTubeAdapter,
    PlatformAdapter,
    YouTubeAdapter,
)
from surveillance_video_agent.adapters.base import sanitize_error_text
from surveillance_video_agent.contracts import (
    AdapterError,
    DownloadRequest,
    ProbeRequest,
    ProbeResult,
    SearchRequest,
)
from surveillance_video_agent.technical import technical_check


_PLATFORMS = ("youtube", "dailymotion", "peertube")
_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@dataclass(frozen=True, slots=True)
class FrozenQuery:
    campaign_id: str
    query_pack_version: str
    query_id: str
    query: str
    lang: str


@dataclass(frozen=True, slots=True)
class SmokeConfig:
    query_pack_path: Path
    query_id: str
    platforms: tuple[str, ...] = _PLATFORMS
    peertube_instance_hosts: tuple[str, ...] = ()
    search_limit: int = 3
    candidate_index: int = 1
    enable_download: bool = False
    max_height: int = 360
    max_filesize_bytes: int = 50 * 1024 * 1024
    probe_timeout_seconds: float = 45.0
    download_timeout_seconds: float = 120.0
    keep_temp: bool = False
    run_id: str = "online-smoke"

    def __post_init__(self) -> None:
        object.__setattr__(self, "query_pack_path", Path(self.query_pack_path).resolve())
        if not self.query_id:
            raise ValueError("query_id is required")
        if not self.platforms or any(item not in _PLATFORMS for item in self.platforms):
            raise ValueError("platforms must be a non-empty subset of supported platforms")
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("platforms must not contain duplicates")
        if not 1 <= self.search_limit <= 3:
            raise ValueError("online smoke search_limit must be between 1 and 3")
        if not 1 <= self.candidate_index <= self.search_limit:
            raise ValueError("candidate_index must be between 1 and search_limit")
        if not 1 <= self.max_height <= 1080:
            raise ValueError("max_height must be between 1 and 1080")
        if not 1 <= self.max_filesize_bytes <= 2 * 1024 * 1024 * 1024:
            raise ValueError("max_filesize_bytes exceeds the v1 boundary")
        if self.probe_timeout_seconds <= 0 or self.download_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")


def load_frozen_query(path: Path, query_id: str) -> FrozenQuery:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"query pack not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"query pack is not valid JSON: {path}") from error
    if not isinstance(document, dict) or document.get("status") != "frozen":
        raise ValueError("online smoke requires a frozen query pack")
    if document.get("network_config") != "default":
        raise ValueError("online smoke only supports network_config=default")
    version = document.get("query_pack_version")
    campaign_id = document.get("campaign_id")
    if not isinstance(version, str) or not version or not isinstance(campaign_id, str):
        raise ValueError("query pack is missing version or campaign identity")
    queries = document.get("queries")
    if not isinstance(queries, list):
        raise ValueError("query pack is missing queries")
    for item in queries:
        if not isinstance(item, dict) or item.get("query_id") != query_id:
            continue
        query = item.get("query")
        lang = item.get("lang")
        if not isinstance(query, str) or not query or lang not in {"en", "es", "fr"}:
            raise ValueError("selected query is incomplete")
        return FrozenQuery(campaign_id, version, query_id, query, lang)
    raise ValueError(f"query_id not found in frozen pack: {query_id}")


def network_environment_summary(
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Describe inherited routing without persisting proxy addresses."""

    values = environment if environment is not None else os.environ
    configured = sorted(key for key in _PROXY_ENV_KEYS if values.get(key))
    return {
        "mode": "inherited_process_environment",
        "proxy_environment_keys": configured,
        "application_proxy_override": False,
    }


def build_adapters(config: SmokeConfig) -> dict[str, PlatformAdapter]:
    adapters: dict[str, PlatformAdapter] = {
        "youtube": YouTubeAdapter(),
        "dailymotion": DailymotionAdapter(),
        "peertube": PeerTubeAdapter(
            allowed_instance_hosts=config.peertube_instance_hosts
        ),
    }
    return {name: adapters[name] for name in config.platforms}


def run_smoke(
    config: SmokeConfig,
    *,
    adapters: Mapping[str, PlatformAdapter] | None = None,
    technical_checker: Callable[[Path], dict[str, Any]] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run bounded platform checks; downloads are globally serial by construction."""

    network_environment = network_environment_summary(environment)
    frozen_query = load_frozen_query(config.query_pack_path, config.query_id)
    active_adapters = dict(adapters or build_adapters(config))
    missing = [name for name in config.platforms if name not in active_adapters]
    if missing:
        raise ValueError(f"missing adapters: {', '.join(missing)}")
    checker = technical_checker or technical_check
    temp_root = Path(tempfile.mkdtemp(prefix="sva-smoke-")).resolve()
    report: dict[str, Any] = {
        "run_id": config.run_id,
        "query_pack_version": frozen_query.query_pack_version,
        "query_id": frozen_query.query_id,
        "query": frozen_query.query,
        "lang": frozen_query.lang,
        "network_config": "default",
        "candidate_index": config.candidate_index,
        "network_environment": network_environment,
        "peertube_allowlist": list(config.peertube_instance_hosts),
        "temp_root": str(temp_root),
        "temp_cleaned": False,
        "platforms": [],
    }
    try:
        for platform in config.platforms:
            report["platforms"].append(
                _run_platform(
                    platform,
                    active_adapters[platform],
                    frozen_query,
                    config,
                    temp_root,
                    checker,
                )
            )
    finally:
        if not config.keep_temp:
            shutil.rmtree(temp_root)
            report["temp_cleaned"] = True
    return report


def _run_platform(
    platform: str,
    adapter: PlatformAdapter,
    frozen_query: FrozenQuery,
    config: SmokeConfig,
    temp_root: Path,
    technical_checker: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "platform": platform,
        "search": None,
        "probe": None,
        "download": {"attempted": False},
        "technical": None,
    }
    try:
        hits = adapter.search(
            SearchRequest(
                platform=platform,
                query=frozen_query.query,
                lang=frozen_query.lang,
                query_pack_version=frozen_query.query_pack_version,
                network_config="default",
                limit=config.search_limit,
                request_id=f"{config.run_id}:search:{platform}",
                run_id=config.run_id,
            )
        )
        item["search"] = {"ok": True, "hit_count": len(hits)}
    except Exception as error:  # adapters intentionally isolate platform failures
        item["search"] = {"ok": False, "failure": _failure(error)}
        return item
    if len(hits) < config.candidate_index:
        item["probe"] = {
            "ok": False,
            "failure": {
                "kind": "no_candidate",
                "message": "search returned fewer approved candidates than candidate_index",
            },
        }
        return item
    hit = hits[config.candidate_index - 1]
    item["candidate"] = {
        "candidate_key": hit.candidate_key,
        "url": hit.source_url,
        "search_duration_seconds": hit.duration_seconds,
    }
    try:
        probe = adapter.probe(
            ProbeRequest(
                platform=platform,
                source_id=hit.source_id,
                candidate_key=hit.candidate_key,
                source_url=hit.source_url,
                network_config="default",
                request_id=f"{config.run_id}:probe:{platform}",
                run_id=config.run_id,
                timeout_seconds=config.probe_timeout_seconds,
            )
        )
        item["probe"] = _probe_summary(probe)
    except Exception as error:
        item["probe"] = {"ok": False, "failure": _failure(error)}
        return item
    reasons = _download_ineligibility(probe, config.max_filesize_bytes)
    if not config.enable_download:
        item["download"]["reason"] = "download_disabled"
        return item
    if reasons:
        item["download"]["reason"] = "probe_not_download_eligible"
        item["download"]["eligibility_failures"] = reasons
        return item
    try:
        result = adapter.download(
            DownloadRequest(
                platform=platform,
                source_id=hit.source_id,
                candidate_key=hit.candidate_key,
                source_url=hit.source_url,
                managed_root=temp_root,
                output_dir=temp_root / platform,
                network_config="default",
                request_id=f"{config.run_id}:download:{platform}",
                run_id=config.run_id,
                max_height=config.max_height,
                max_filesize_bytes=config.max_filesize_bytes,
                timeout_seconds=config.download_timeout_seconds,
            )
        )
        item["download"] = {
            "attempted": True,
            "success": result.success,
            "bytes_downloaded": result.bytes_downloaded,
            "file_path": str(result.file_path) if result.file_path else None,
            "returncode": result.returncode,
            "error_kind": result.error_kind.value if result.error_kind else None,
            "error_message": result.error_message,
        }
        if result.success and result.file_path is not None:
            item["technical"] = technical_checker(result.file_path)
    except Exception as error:
        item["download"] = {"attempted": True, "success": False, "failure": _failure(error)}
    return item


def _probe_summary(probe: ProbeResult) -> dict[str, Any]:
    return {
        "ok": True,
        "duration_seconds": probe.duration_seconds,
        "availability": probe.availability,
        "filesize_approx": probe.filesize_approx,
        "width": probe.width,
        "height": probe.height,
        "is_live": probe.is_live,
        "live_status": probe.live_status,
    }


def _download_ineligibility(probe: ProbeResult, max_bytes: int) -> list[str]:
    failures: list[str] = []
    if probe.duration_seconds is None or not 10 <= probe.duration_seconds <= 900:
        failures.append("duration_not_10_to_900_seconds")
    if probe.availability not in {None, "public"}:
        failures.append("availability_explicitly_nonpublic")
    if probe.is_live is True:
        failures.append("candidate_is_live")
    if probe.filesize_approx is not None and not 0 < probe.filesize_approx <= max_bytes:
        failures.append("filesize_over_smoke_limit")
    return failures


def _failure(error: Exception) -> dict[str, Any]:
    if isinstance(error, AdapterError):
        return {
            "kind": error.kind.value,
            "message": sanitize_error_text(error.message, max_length=500),
            "returncode": error.returncode,
        }
    return {
        "kind": "request" if isinstance(error, ValueError) else "exception",
        "message": sanitize_error_text(str(error) or type(error).__name__, max_length=500),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query-pack", required=True, type=Path)
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--platform", action="append", choices=_PLATFORMS)
    parser.add_argument("--peertube-instance", action="append", default=[])
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--candidate-index", type=int, default=1)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--max-height", type=int, default=360)
    parser.add_argument("--max-mib", type=int, default=50)
    parser.add_argument("--probe-timeout", type=float, default=45.0)
    parser.add_argument("--download-timeout", type=float, default=120.0)
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--run-id", default="online-smoke")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = SmokeConfig(
            query_pack_path=args.query_pack,
            query_id=args.query_id,
            platforms=tuple(args.platform or _PLATFORMS),
            peertube_instance_hosts=tuple(args.peertube_instance),
            search_limit=args.limit,
            candidate_index=args.candidate_index,
            enable_download=args.download,
            max_height=args.max_height,
            max_filesize_bytes=args.max_mib * 1024 * 1024,
            probe_timeout_seconds=args.probe_timeout,
            download_timeout_seconds=args.download_timeout,
            keep_temp=args.keep_temp,
            run_id=args.run_id,
        )
        report = run_smoke(config)
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
