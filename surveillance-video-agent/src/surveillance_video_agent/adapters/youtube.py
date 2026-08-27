"""YouTube adapter backed by an isolated yt-dlp subprocess."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from surveillance_video_agent.adapters.base import (
    CommandResult,
    CommandRunner,
    PlatformAdapter,
    classify_command_error,
    ensure_child_path,
    ensure_managed_output_dir,
    sanitize_error_text,
    sanitize_metadata,
    validate_https_url,
)
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


_YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_METADATA_TIMEOUT_SECONDS = 60.0


class YouTubeAdapter(PlatformAdapter):
    """Synchronous YouTube search, probe, and single-item download adapter."""

    platform = "youtube"

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        executable: str = "yt-dlp",
    ) -> None:
        super().__init__(runner, executable=executable)

    def search(self, request: SearchRequest) -> list[SearchHit]:
        self.validate_request_platform(request.platform)
        result = self.run_command(
            [
                *self._common_args(),
                "--flat-playlist",
                "--dump-single-json",
                "--skip-download",
                "--",
                f"ytsearch{request.limit}:{request.query}",
            ],
            timeout_seconds=_METADATA_TIMEOUT_SECONDS,
        )
        payload = self._successful_json(result, operation="search")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise AdapterError(AdapterErrorKind.TOOL_ERROR, "yt-dlp search response has no entries")

        hits: list[SearchHit] = []
        for entry in entries:
            if len(hits) >= request.limit:
                break
            if not isinstance(entry, Mapping):
                continue
            source_id = _optional_string(entry.get("id"))
            if source_id is None or not _SOURCE_ID_RE.fullmatch(source_id):
                continue
            source_url = _canonical_url(source_id)
            hits.append(
                SearchHit(
                    platform=self.platform,
                    source_id=source_id,
                    candidate_key=make_candidate_key(self.platform, source_id),
                    source_url=source_url,
                    position=len(hits) + 1,
                    query=request.query,
                    lang=request.lang,
                    query_pack_version=request.query_pack_version,
                    title=_optional_string(entry.get("title")),
                    uploader=_first_string(entry, "uploader", "channel"),
                    duration_seconds=_optional_float(entry.get("duration")),
                    raw_summary=sanitize_metadata(
                        {
                            "id": source_id,
                            "title": entry.get("title"),
                            "uploader": entry.get("uploader"),
                            "uploader_id": entry.get("uploader_id"),
                            "channel": entry.get("channel"),
                            "channel_id": entry.get("channel_id"),
                            "duration": entry.get("duration"),
                            "availability": entry.get("availability"),
                            "live_status": entry.get("live_status"),
                        }
                    ),
                )
            )
        return hits

    def probe(self, request: ProbeRequest) -> ProbeResult:
        self.validate_request_platform(request.platform)
        source_url = _validated_source_url(request.source_url, request.source_id)
        result = self.run_command(
            [
                *self._common_args(),
                "--dump-single-json",
                "--skip-download",
                "--",
                source_url,
            ],
            timeout_seconds=request.timeout_seconds,
        )
        payload = self._successful_json(result, operation="probe")
        returned_id = _optional_string(payload.get("id"))
        if returned_id != request.source_id:
            raise AdapterError(
                AdapterErrorKind.TOOL_ERROR,
                "yt-dlp probe returned a different source id",
            )

        live_status = _optional_string(payload.get("live_status"))
        is_live = payload.get("is_live") if isinstance(payload.get("is_live"), bool) else None
        if is_live is None and live_status is not None:
            is_live = live_status in {"is_live", "is_upcoming", "post_live"}

        return ProbeResult(
            platform=self.platform,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            canonical_url=_canonical_url(request.source_id),
            title=_optional_string(payload.get("title")),
            video_description=_optional_string(payload.get("description"), preserve_empty=True),
            tags=_string_tuple(payload.get("tags")),
            uploader=_first_string(payload, "uploader", "channel"),
            uploader_id=_first_string(payload, "uploader_id", "channel_id"),
            channel=_optional_string(payload.get("channel")),
            playlist=_first_string(payload, "playlist", "playlist_title"),
            duration_seconds=_optional_float(payload.get("duration")),
            upload_date=_optional_string(payload.get("upload_date")),
            availability=_optional_string(payload.get("availability")),
            filesize_approx=_first_positive_int(payload, "filesize", "filesize_approx"),
            width=_optional_positive_int(payload.get("width")),
            height=_optional_positive_int(payload.get("height")),
            is_live=is_live,
            live_status=live_status,
            # Keep a bounded audit summary. Large format tables, request headers,
            # and signed stream URLs must never enter the candidate database.
            raw_metadata=sanitize_metadata(
                {
                    "id": payload.get("id"),
                    "extractor": payload.get("extractor"),
                    "extractor_key": payload.get("extractor_key"),
                    "title": payload.get("title"),
                    "uploader": payload.get("uploader"),
                    "uploader_id": payload.get("uploader_id"),
                    "channel": payload.get("channel"),
                    "channel_id": payload.get("channel_id"),
                    "playlist": payload.get("playlist"),
                    "playlist_id": payload.get("playlist_id"),
                    "duration": payload.get("duration"),
                    "timestamp": payload.get("timestamp"),
                    "upload_date": payload.get("upload_date"),
                    "availability": payload.get("availability"),
                    "live_status": payload.get("live_status"),
                    "filesize": payload.get("filesize"),
                    "filesize_approx": payload.get("filesize_approx"),
                    "width": payload.get("width"),
                    "height": payload.get("height"),
                }
            ),
        )

    def download(self, request: DownloadRequest) -> DownloadResult:
        self.validate_request_platform(request.platform)
        source_url = _validated_source_url(request.source_url, request.source_id)
        output_dir = ensure_managed_output_dir(request.managed_root, request.output_dir)
        output_stem = f"youtube-{request.source_id}"
        output_template = output_dir / f"{output_stem}.%(ext)s"
        result = self.run_command(
            [
                *self._common_args(),
                "--no-progress",
                "--max-filesize",
                str(request.max_filesize_bytes),
                "--format",
                f"bestvideo[height<={request.max_height}]+bestaudio/best[height<={request.max_height}]",
                "--output",
                str(output_template),
                "--print",
                "after_move:filepath",
                "--",
                source_url,
            ],
            timeout_seconds=request.timeout_seconds,
            cwd=output_dir,
        )
        if result.returncode != 0 or result.timed_out:
            return self._failed_download(request, result)

        try:
            file_path = _resolve_downloaded_file(result.stdout, output_dir, output_stem)
        except (OSError, ValueError) as error:
            return DownloadResult(
                platform=self.platform,
                source_id=request.source_id,
                candidate_key=request.candidate_key,
                source_url=request.source_url,
                success=False,
                returncode=result.returncode,
                error_kind=AdapterErrorKind.TOOL_ERROR,
                error_message=sanitize_error_text(str(error)),
            )

        bytes_downloaded = file_path.stat().st_size
        if bytes_downloaded > request.max_filesize_bytes:
            return DownloadResult(
                platform=self.platform,
                source_id=request.source_id,
                candidate_key=request.candidate_key,
                source_url=request.source_url,
                success=False,
                file_path=file_path,
                bytes_downloaded=bytes_downloaded,
                returncode=result.returncode,
                error_kind=AdapterErrorKind.RESOURCE_LIMIT,
                error_message="downloaded file exceeds max_filesize_bytes",
            )

        return DownloadResult(
            platform=self.platform,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            success=True,
            file_path=file_path,
            bytes_downloaded=bytes_downloaded,
            returncode=result.returncode,
            raw_summary={"stdout": sanitize_error_text(result.stdout)},
        )

    def _common_args(self) -> list[str]:
        return [
            self.executable,
            "--ignore-config",
            "--no-plugin-dirs",
            "--force-ipv4",
            "--js-runtimes",
            "node",
            "--socket-timeout",
            "30",
            "--sleep-requests",
            "1.0",
            "--extractor-retries",
            "5",
            "--retries",
            "5",
            "--fragment-retries",
            "5",
            "--retry-sleep",
            "http:exp=2:20",
            "--retry-sleep",
            "extractor:exp=2:20",
            "--retry-sleep",
            "fragment:exp=1:10",
            "--concurrent-fragments",
            "1",
            "--no-playlist",
            "--no-write-subs",
            "--no-write-auto-subs",
        ]

    def _successful_json(self, result: CommandResult, *, operation: str) -> Mapping[str, Any]:
        if result.returncode != 0 or result.timed_out:
            kind = classify_command_error(result)
            message = sanitize_error_text(result.stderr or result.stdout or f"{operation} failed")
            raise AdapterError(kind, message, returncode=result.returncode)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AdapterError(
                AdapterErrorKind.TOOL_ERROR,
                f"yt-dlp {operation} returned invalid JSON",
                returncode=result.returncode,
            ) from error
        if not isinstance(payload, Mapping):
            raise AdapterError(
                AdapterErrorKind.TOOL_ERROR,
                f"yt-dlp {operation} returned a non-object JSON value",
                returncode=result.returncode,
            )
        return payload

    def _failed_download(self, request: DownloadRequest, result: CommandResult) -> DownloadResult:
        return DownloadResult(
            platform=self.platform,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            success=False,
            returncode=result.returncode,
            error_kind=classify_command_error(result),
            error_message=sanitize_error_text(result.stderr or result.stdout or "download failed"),
        )


def _validated_source_url(source_url: str, expected_source_id: str) -> str:
    if not _SOURCE_ID_RE.fullmatch(expected_source_id):
        raise ValueError("invalid YouTube source_id")
    validate_https_url(source_url, allowed_hosts=_YOUTUBE_HOSTS)
    parsed = urlsplit(source_url)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if hostname not in _YOUTUBE_HOSTS:
        raise ValueError(f"source_url host is not an approved YouTube host: {hostname!r}")
    found_id: str | None = None
    if hostname == "youtu.be" or hostname.endswith(".youtu.be"):
        found_id = parsed.path.strip("/").split("/", 1)[0] or None
    else:
        query_id = parse_qs(parsed.query).get("v", [None])[0]
        path_parts = [part for part in parsed.path.split("/") if part]
        if query_id:
            found_id = query_id
        elif len(path_parts) >= 2 and path_parts[0] in {"embed", "live", "shorts"}:
            found_id = path_parts[1]
    if found_id != expected_source_id:
        raise ValueError("source_url does not identify the requested YouTube source_id")
    return _canonical_url(expected_source_id)


def _canonical_url(source_id: str) -> str:
    if not _SOURCE_ID_RE.fullmatch(source_id):
        raise ValueError("invalid YouTube source_id")
    return f"https://www.youtube.com/watch?v={source_id}"


def _resolve_downloaded_file(stdout: str, output_dir: Path, output_stem: str) -> Path:
    printed_paths = [Path(line.strip()) for line in stdout.splitlines() if line.strip()]
    for printed_path in reversed(printed_paths):
        candidate = printed_path if printed_path.is_absolute() else output_dir / printed_path
        if candidate.is_file():
            return ensure_child_path(output_dir, candidate)

    fallback = [
        path
        for path in output_dir.glob(f"{output_stem}.*")
        if path.is_file() and path.suffix not in {".part", ".ytdl"}
    ]
    if len(fallback) != 1:
        raise ValueError("yt-dlp did not produce exactly one downloadable media file")
    return ensure_child_path(output_dir, fallback[0])


def _optional_string(value: Any, *, preserve_empty: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    if preserve_empty:
        return value
    return value if value else None


def _first_string(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _optional_string(payload.get(key))
        if value is not None:
            return value
    return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _optional_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None


def _first_positive_int(payload: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _optional_positive_int(payload.get(key))
        if value is not None:
            return value
    return None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, str))
