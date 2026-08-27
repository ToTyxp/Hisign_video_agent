"""Technical-only media verification and hashing."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Sequence

from surveillance_video_agent.adapters.base import sanitize_error_text


def technical_check(path: Path) -> dict[str, Any]:
    """Run ffprobe, video-stream, and first/middle/last decode checks only."""

    probe = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        timeout_seconds=30,
    )
    result: dict[str, Any] = {
        "ffprobe_returncode": probe.returncode,
        "video_stream_present": False,
        "decode": [],
        "technical_passed": False,
    }
    if probe.returncode != 0:
        result["ffprobe_error"] = sanitize_error_text(probe.stderr or probe.stdout, max_length=500)
        return result
    try:
        document = json.loads(probe.stdout)
        streams = document.get("streams", [])
        videos = [item for item in streams if item.get("codec_type") == "video"]
        duration = float((document.get("format") or {}).get("duration") or 0)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
        result["ffprobe_error"] = sanitize_error_text(str(error), max_length=500)
        return result
    result["video_stream_present"] = bool(videos)
    result["video_streams"] = [
        {key: item.get(key) for key in ("codec_name", "width", "height")} for item in videos
    ]
    result["duration_seconds"] = duration
    if not videos or duration <= 0:
        return result
    positions = (
        ("first", min(0.1, duration / 2)),
        ("middle", duration / 2),
        ("last", max(0.1, duration - 0.5)),
    )
    for label, seconds in positions:
        decoded = _run_command(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                f"{seconds:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            timeout_seconds=30,
        )
        result["decode"].append(
            {
                "point": label,
                "seconds": round(seconds, 3),
                "returncode": decoded.returncode,
                "error": (
                    sanitize_error_text(decoded.stderr or decoded.stdout, max_length=500)
                    if decoded.returncode
                    else None
                ),
            }
        )
    result["technical_passed"] = all(
        (
            result["ffprobe_returncode"] == 0,
            result["video_stream_present"],
            all(item["returncode"] == 0 for item in result["decode"]),
        )
    )
    if result["technical_passed"]:
        result["sha256"] = sha256_file(path)
        result["bytes"] = path.stat().st_size
    return result


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _run_command(args: Sequence[str], *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=124,
            stdout=_timeout_text(error.stdout),
            stderr=_timeout_text(error.stderr),
        )


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
