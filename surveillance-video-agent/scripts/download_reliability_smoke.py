#!/usr/bin/env python3
"""Online, non-publishing smoke for the project-pinned yt-dlp adapters."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path

from surveillance_video_agent.adapters import DailymotionAdapter, YouTubeAdapter
from surveillance_video_agent.contracts import DownloadRequest, make_candidate_key
from surveillance_video_agent.technical import technical_check


ADAPTERS = {
    "youtube": YouTubeAdapter,
    "dailymotion": DailymotionAdapter,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        action="append",
        required=True,
        help="platform,source_id,canonical_https_url",
    )
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--backoff-seconds", type=float, default=20)
    args = parser.parse_args()
    if not 1 <= args.attempts <= 3 or args.backoff_seconds < 0:
        raise ValueError("smoke attempts must be 1-3 with non-negative backoff")
    cases = [_parse_case(value) for value in args.case]
    results = []
    with tempfile.TemporaryDirectory(prefix="surveillance-download-smoke-") as temporary:
        root = Path(temporary).resolve()
        for index, (platform, source_id, url) in enumerate(cases, 1):
            adapter = ADAPTERS[platform]()
            candidate_key = make_candidate_key(platform, source_id)
            request = DownloadRequest(
                    platform=platform,
                    source_id=source_id,
                    candidate_key=candidate_key,
                    source_url=url,
                    managed_root=root,
                    output_dir=root / f"case-{index}",
                    network_config="default",
                    request_id=str(uuid.uuid4()),
                    run_id="online-download-reliability-smoke",
                    max_height=1080,
                    timeout_seconds=1200,
                )
            attempt_results = []
            for attempt in range(1, args.attempts + 1):
                result = adapter.download(request)
                attempt_results.append(
                    {
                        "attempt": attempt,
                        "success": result.success,
                        "error_kind": result.error_kind.value if result.error_kind else None,
                    }
                )
                if result.success or attempt == args.attempts:
                    break
                if result.error_kind is None or result.error_kind.value not in {
                    "network", "rate_limited", "timeout"
                }:
                    break
                time.sleep(args.backoff_seconds * (2 ** (attempt - 1)))
            check = technical_check(result.file_path) if result.success else None
            results.append(
                {
                    "candidate_key": candidate_key,
                    "success": result.success,
                    "error_kind": result.error_kind.value if result.error_kind else None,
                    "error_message": result.error_message,
                    "technical_passed": bool(check and check.get("technical_passed")),
                    "duration_seconds": check.get("duration_seconds") if check else None,
                    "resolution": (
                        check.get("video_streams", [{}])[0]
                        if check and check.get("video_streams")
                        else None
                    ),
                    "yt_dlp_executable": adapter.executable,
                    "attempts": attempt_results,
                }
            )
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if all(item["success"] and item["technical_passed"] for item in results) else 1


def _parse_case(value: str) -> tuple[str, str, str]:
    parts = value.split(",", 2)
    if len(parts) != 3 or parts[0] not in ADAPTERS:
        raise ValueError("--case must be platform,source_id,canonical_https_url")
    return parts[0], parts[1], parts[2]


if __name__ == "__main__":
    raise SystemExit(main())
