"""Dailymotion adapter backed by yt-dlp's maintained extractors.

The adapter deliberately accepts only canonical Dailymotion video identities.
Search may discover a video through Dailymotion's search extractor, but probe and
download never follow arbitrary embed, playlist, or third-party URLs.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote_plus, urlsplit

from surveillance_video_agent.contracts import (
    AdapterError,
    AdapterErrorKind,
    DownloadRequest,
    DownloadResult,
    ProbeRequest,
    ProbeResult,
    SearchHit,
    SearchRequest,
    make_candidate_key,
)

from .base import (
    CommandResult,
    PlatformAdapter,
    classify_command_error,
    ensure_child_path,
    ensure_managed_output_dir,
    sanitize_error_text,
    sanitize_metadata,
)


_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9]{1,128}$")
_VIDEO_PATH_RE = re.compile(r"^/video/(?P<id>[A-Za-z0-9]{1,128})(?:_[^/]*)?/?$")
_SHORT_PATH_RE = re.compile(r"^/(?P<id>[A-Za-z0-9]{1,128})/?$")
_ALLOWED_HOSTS = frozenset({"dailymotion.com", "www.dailymotion.com", "dai.ly"})
_SEARCH_TIMEOUT_SECONDS = 60.0


class DailymotionAdapter(PlatformAdapter):
    """Synchronous Dailymotion discovery, metadata, and download adapter."""

    platform = "dailymotion"

    def search(self, request: SearchRequest) -> list[SearchHit]:
        self.validate_request_platform(request.platform)
        search_url = (
            "https://www.dailymotion.com/search/"
            f"{quote_plus(request.query, safe='')}/videos"
        )
        result = self.run_command(
            [
                *self._safe_yt_dlp_prefix(),
                "--flat-playlist",
                "--playlist-end",
                str(request.limit),
                "--dump-single-json",
                "--",
                search_url,
            ],
            timeout_seconds=_SEARCH_TIMEOUT_SECONDS,
        )
        payload = self._json_payload(result, operation="Dailymotion search")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise AdapterError(
                AdapterErrorKind.TOOL_ERROR,
                "Dailymotion search did not return an entries list",
                returncode=result.returncode,
            )

        hits: list[SearchHit] = []
        seen_ids: set[str] = set()
        for entry in entries:
            if len(hits) >= request.limit:
                break
            if not isinstance(entry, Mapping):
                continue
            source_id = _optional_text(entry.get("id"))
            if source_id is None or not _SOURCE_ID_RE.fullmatch(source_id):
                continue
            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)
            canonical_url = _canonical_url(source_id)
            hits.append(
                SearchHit(
                    platform=self.platform,
                    source_id=source_id,
                    candidate_key=make_candidate_key(self.platform, source_id),
                    source_url=canonical_url,
                    position=len(hits) + 1,
                    query=request.query,
                    lang=request.lang,
                    query_pack_version=request.query_pack_version,
                    title=_optional_text(entry.get("title")),
                    uploader=_optional_text(entry.get("uploader")),
                    duration_seconds=_optional_number(entry.get("duration")),
                    raw_summary=sanitize_metadata(
                        {
                            "extractor": entry.get("extractor"),
                            "extractor_key": entry.get("extractor_key"),
                            "id": source_id,
                            "title": entry.get("title"),
                            "uploader": entry.get("uploader"),
                            "duration": entry.get("duration"),
                            "availability": entry.get("availability"),
                        }
                    ),
                )
            )
        return hits

    def probe(self, request: ProbeRequest) -> ProbeResult:
        self.validate_request_platform(request.platform)
        canonical_url = _validate_identity(
            request.source_url,
            request.source_id,
            request.candidate_key,
        )
        result = self.run_command(
            [
                *self._safe_yt_dlp_prefix(),
                "--no-playlist",
                "--skip-download",
                "--dump-single-json",
                "--",
                canonical_url,
            ],
            timeout_seconds=request.timeout_seconds,
        )
        metadata = self._json_payload(result, operation="Dailymotion probe")
        returned_id = _optional_text(metadata.get("id"))
        if returned_id != request.source_id:
            raise AdapterError(
                AdapterErrorKind.TOOL_ERROR,
                "Dailymotion probe returned a different video identity",
                returncode=result.returncode,
            )

        raw_metadata = sanitize_metadata(
            {
                "id": metadata.get("id"),
                "extractor": metadata.get("extractor"),
                "extractor_key": metadata.get("extractor_key"),
                "title": metadata.get("title"),
                "description": metadata.get("description"),
                "tags": metadata.get("tags"),
                "uploader": metadata.get("uploader"),
                "uploader_id": metadata.get("uploader_id"),
                "channel": metadata.get("channel"),
                "channel_id": metadata.get("channel_id"),
                "playlist": metadata.get("playlist"),
                "playlist_id": metadata.get("playlist_id"),
                "duration": metadata.get("duration"),
                "timestamp": metadata.get("timestamp"),
                "upload_date": metadata.get("upload_date"),
                "availability": metadata.get("availability"),
                "live_status": metadata.get("live_status"),
                "filesize": metadata.get("filesize"),
                "filesize_approx": metadata.get("filesize_approx"),
                "width": metadata.get("width"),
                "height": metadata.get("height"),
                # These fields exercise the central redactor if a future yt-dlp
                # version exposes them at the top level.
                "http_headers": metadata.get("http_headers"),
                "url": metadata.get("url"),
            }
        )
        live_status = _optional_text(metadata.get("live_status"))
        is_live = metadata.get("is_live") if isinstance(metadata.get("is_live"), bool) else None
        if is_live is None and live_status is not None:
            is_live = live_status in {"is_live", "is_upcoming", "post_live"}

        return ProbeResult(
            platform=self.platform,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            canonical_url=canonical_url,
            title=_optional_text(metadata.get("title")),
            # The complete platform description is preserved; it is not
            # summarized or truncated by the adapter.
            video_description=_optional_text(metadata.get("description"), allow_empty=True),
            tags=_text_tuple(metadata.get("tags")),
            uploader=_optional_text(metadata.get("uploader")),
            uploader_id=_optional_text(metadata.get("uploader_id")),
            channel=_optional_text(metadata.get("channel")),
            playlist=_optional_text(metadata.get("playlist")),
            duration_seconds=_optional_number(metadata.get("duration")),
            upload_date=_normalize_upload_date(
                metadata.get("upload_date"), metadata.get("timestamp")
            ),
            availability=_optional_text(metadata.get("availability")),
            filesize_approx=_first_positive_int(
                metadata, "filesize", "filesize_approx"
            ),
            width=_optional_int(metadata.get("width")),
            height=_optional_int(metadata.get("height")),
            is_live=is_live,
            live_status=live_status,
            raw_metadata=raw_metadata,
        )

    def download(self, request: DownloadRequest) -> DownloadResult:
        self.validate_request_platform(request.platform)
        canonical_url = _validate_identity(
            request.source_url,
            request.source_id,
            request.candidate_key,
        )
        output_dir = ensure_managed_output_dir(request.managed_root, request.output_dir)
        output_template = output_dir / f"dailymotion_{request.source_id}.%(ext)s"

        result = self.run_command(
            [
                *self._safe_yt_dlp_prefix(),
                "--no-playlist",
                "--no-progress",
                "--no-write-thumbnail",
                "--no-write-info-json",
                "--no-write-comments",
                "--no-write-playlist-metafiles",
                "--format",
                (
                    f"bestvideo[height<={request.max_height}]+bestaudio/"
                    f"best[height<={request.max_height}]"
                ),
                "--max-filesize",
                str(request.max_filesize_bytes),
                "--output",
                str(output_template),
                "--print",
                "after_move:filepath",
                "--",
                canonical_url,
            ],
            timeout_seconds=request.timeout_seconds,
            cwd=output_dir,
        )
        if result.returncode != 0 or result.timed_out:
            return _download_failure(request, result)

        output_path = _printed_output_path(result.stdout)
        if output_path is None:
            return _download_failure(
                request,
                result,
                override_kind=AdapterErrorKind.TOOL_ERROR,
                override_message="yt-dlp did not report the final Dailymotion output path",
            )
        if not output_path.is_absolute():
            output_path = output_dir / output_path
        try:
            output_path = ensure_child_path(output_dir, output_path)
        except (FileNotFoundError, ValueError) as error:
            return _download_failure(
                request,
                result,
                override_kind=AdapterErrorKind.TOOL_ERROR,
                override_message=str(error),
            )
        if not output_path.is_file() or output_path.is_symlink():
            return _download_failure(
                request,
                result,
                override_kind=AdapterErrorKind.TOOL_ERROR,
                override_message="yt-dlp output is not a regular managed file",
            )

        byte_count = output_path.stat().st_size
        if byte_count > request.max_filesize_bytes:
            return DownloadResult(
                platform=self.platform,
                source_id=request.source_id,
                candidate_key=request.candidate_key,
                source_url=request.source_url,
                success=False,
                file_path=output_path,
                bytes_downloaded=byte_count,
                returncode=result.returncode,
                error_kind=AdapterErrorKind.RESOURCE_LIMIT,
                error_message="download exceeded the configured file-size limit",
                raw_summary={"canonical_url": canonical_url},
            )

        return DownloadResult(
            platform=self.platform,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            success=True,
            file_path=output_path,
            bytes_downloaded=byte_count,
            returncode=result.returncode,
            raw_summary={"canonical_url": canonical_url},
        )

    def _safe_yt_dlp_prefix(self) -> list[str]:
        return [
            self.executable,
            "--ignore-config",
            "--no-plugin-dirs",
            "--no-write-subs",
            "--no-write-auto-subs",
        ]

    def _json_payload(self, result: CommandResult, *, operation: str) -> dict[str, Any]:
        if result.returncode != 0 or result.timed_out:
            kind = classify_command_error(result)
            message = sanitize_error_text(result.stderr or result.stdout or f"{operation} failed")
            raise AdapterError(kind, message, returncode=result.returncode)
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise AdapterError(
                AdapterErrorKind.TOOL_ERROR,
                f"{operation} returned invalid JSON",
                returncode=result.returncode,
            ) from error
        if not isinstance(payload, dict):
            raise AdapterError(
                AdapterErrorKind.TOOL_ERROR,
                f"{operation} returned a non-object JSON value",
                returncode=result.returncode,
            )
        return payload


def _validate_identity(source_url: str, source_id: str, candidate_key: str) -> str:
    if not _SOURCE_ID_RE.fullmatch(source_id):
        raise ValueError("invalid Dailymotion source_id")
    if candidate_key != make_candidate_key("dailymotion", source_id):
        raise ValueError("candidate_key does not match Dailymotion source_id")

    parsed = urlsplit(source_url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise ValueError("Dailymotion source_url must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Dailymotion source_url must not include user information")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Dailymotion source_url has an invalid port") from error
    if port not in (None, 443):
        raise ValueError("Dailymotion source_url must not use a non-HTTPS port")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname not in _ALLOWED_HOSTS:
        raise ValueError("Dailymotion source_url host is not allowed")
    if parsed.query or parsed.fragment:
        raise ValueError("Dailymotion source_url must not include query or fragment data")

    matcher = _SHORT_PATH_RE if hostname == "dai.ly" else _VIDEO_PATH_RE
    match = matcher.fullmatch(parsed.path)
    if match is None or match.group("id") != source_id:
        raise ValueError("Dailymotion source_url does not match source_id")
    return _canonical_url(source_id)


def _canonical_url(source_id: str) -> str:
    return f"https://www.dailymotion.com/video/{source_id}"


def _optional_text(value: Any, *, allow_empty: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    if not allow_empty and not value.strip():
        return None
    return value


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def _first_positive_int(metadata: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _optional_int(metadata.get(key))
        if value is not None:
            return value
    return None


def _text_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item.strip())


def _normalize_upload_date(upload_date: Any, timestamp: Any) -> str | None:
    if isinstance(upload_date, str) and re.fullmatch(r"\d{8}", upload_date):
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    if isinstance(upload_date, str) and upload_date.strip():
        return upload_date
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _printed_output_path(stdout: str) -> Path | None:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    return Path(lines[-1]) if lines else None


def _download_failure(
    request: DownloadRequest,
    result: CommandResult,
    *,
    override_kind: AdapterErrorKind | None = None,
    override_message: str | None = None,
) -> DownloadResult:
    kind = override_kind or classify_command_error(result)
    message = override_message or result.stderr or result.stdout or "Dailymotion download failed"
    return DownloadResult(
        platform="dailymotion",
        source_id=request.source_id,
        candidate_key=request.candidate_key,
        source_url=request.source_url,
        success=False,
        returncode=result.returncode,
        error_kind=kind,
        error_message=sanitize_error_text(message),
        raw_summary={},
    )
