"""Stable, platform-neutral contracts for discovery and download adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


SUPPORTED_PLATFORMS = frozenset({"youtube", "dailymotion", "peertube"})
SUPPORTED_QUERY_LANGUAGES = frozenset({"en", "es", "fr"})
DEFAULT_NETWORK_CONFIG = "default"
MAX_SEARCH_RESULTS = 20
DEFAULT_MAX_HEIGHT = 1080
DEFAULT_MAX_FILESIZE_BYTES = 2 * 1024 * 1024 * 1024


class AdapterErrorKind(str, Enum):
    """Stable error categories consumed by orchestration and audit layers."""

    NETWORK = "network"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    PRIVATE = "private"
    UNSUPPORTED = "unsupported"
    RESOURCE_LIMIT = "resource_limit"
    TOOL_ERROR = "tool_error"
    TIMEOUT = "timeout"


class AdapterError(RuntimeError):
    """An adapter failure carrying a non-platform-specific error category."""

    def __init__(
        self,
        kind: AdapterErrorKind,
        message: str,
        *,
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.returncode = returncode


def make_candidate_key(platform: str, source_id: str) -> str:
    """Build the only valid v2 candidate identity."""

    _validate_platform(platform)
    _validate_text("source_id", source_id)
    if ":" in source_id:
        raise ValueError("source_id must not contain ':'")
    return f"{platform}:{source_id}"


# Compatibility name for early adapter implementations. New code should prefer
# the imperative ``make_candidate_key`` public name.
candidate_key_for = make_candidate_key


def _validate_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} must not contain control characters")


def _validate_platform(platform: str) -> None:
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported platform: {platform!r}")


def _validate_network_config(network_config: str) -> None:
    if network_config != DEFAULT_NETWORK_CONFIG:
        raise ValueError("v1 network_config must be 'default'")


def _validate_candidate_identity(platform: str, source_id: str, candidate_key: str) -> None:
    expected = make_candidate_key(platform, source_id)
    if candidate_key != expected:
        raise ValueError(f"candidate_key must be {expected!r}")


@dataclass(frozen=True, slots=True)
class SearchRequest:
    platform: str
    query: str
    lang: str
    query_pack_version: str
    network_config: str
    limit: int
    request_id: str
    run_id: str

    def __post_init__(self) -> None:
        _validate_platform(self.platform)
        _validate_text("query", self.query)
        if self.lang not in SUPPORTED_QUERY_LANGUAGES:
            raise ValueError(f"unsupported query language: {self.lang!r}")
        _validate_text("query_pack_version", self.query_pack_version)
        _validate_network_config(self.network_config)
        if isinstance(self.limit, bool) or not 1 <= self.limit <= MAX_SEARCH_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_RESULTS}")
        _validate_text("request_id", self.request_id)
        _validate_text("run_id", self.run_id)


@dataclass(frozen=True, slots=True)
class SearchHit:
    platform: str
    source_id: str
    candidate_key: str
    source_url: str
    position: int
    query: str
    lang: str
    query_pack_version: str
    title: str | None = None
    uploader: str | None = None
    duration_seconds: float | None = None
    raw_summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_candidate_identity(self.platform, self.source_id, self.candidate_key)
        _validate_text("source_url", self.source_url)
        if isinstance(self.position, bool) or self.position < 1:
            raise ValueError("position must be positive")


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    platform: str
    source_id: str
    candidate_key: str
    source_url: str
    network_config: str
    request_id: str
    run_id: str
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        _validate_candidate_identity(self.platform, self.source_id, self.candidate_key)
        _validate_text("source_url", self.source_url)
        _validate_network_config(self.network_config)
        _validate_text("request_id", self.request_id)
        _validate_text("run_id", self.run_id)
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ProbeResult:
    platform: str
    source_id: str
    candidate_key: str
    source_url: str
    canonical_url: str
    title: str | None = None
    video_description: str | None = None
    tags: tuple[str, ...] = ()
    uploader: str | None = None
    uploader_id: str | None = None
    channel: str | None = None
    playlist: str | None = None
    duration_seconds: float | None = None
    upload_date: str | None = None
    availability: str | None = None
    filesize_approx: int | None = None
    width: int | None = None
    height: int | None = None
    is_live: bool | None = None
    live_status: str | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_candidate_identity(self.platform, self.source_id, self.candidate_key)
        _validate_text("source_url", self.source_url)
        _validate_text("canonical_url", self.canonical_url)


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    platform: str
    source_id: str
    candidate_key: str
    source_url: str
    managed_root: Path
    output_dir: Path
    network_config: str
    request_id: str
    run_id: str
    max_height: int = DEFAULT_MAX_HEIGHT
    max_filesize_bytes: int = DEFAULT_MAX_FILESIZE_BYTES
    timeout_seconds: float = 1800.0

    def __post_init__(self) -> None:
        _validate_candidate_identity(self.platform, self.source_id, self.candidate_key)
        _validate_text("source_url", self.source_url)
        _validate_network_config(self.network_config)
        _validate_text("request_id", self.request_id)
        _validate_text("run_id", self.run_id)
        object.__setattr__(self, "managed_root", Path(self.managed_root))
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        if not self.managed_root.is_absolute() or not self.output_dir.is_absolute():
            raise ValueError("managed_root and output_dir must be absolute paths")
        if self.max_height <= 0 or self.max_height > DEFAULT_MAX_HEIGHT:
            raise ValueError(f"max_height must be between 1 and {DEFAULT_MAX_HEIGHT}")
        if self.max_filesize_bytes <= 0 or self.max_filesize_bytes > DEFAULT_MAX_FILESIZE_BYTES:
            raise ValueError(
                f"max_filesize_bytes must be between 1 and {DEFAULT_MAX_FILESIZE_BYTES}"
            )
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class DownloadResult:
    platform: str
    source_id: str
    candidate_key: str
    source_url: str
    success: bool
    file_path: Path | None = None
    bytes_downloaded: int | None = None
    returncode: int | None = None
    error_kind: AdapterErrorKind | None = None
    error_message: str | None = None
    raw_summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_candidate_identity(self.platform, self.source_id, self.candidate_key)
        _validate_text("source_url", self.source_url)
        if self.file_path is not None:
            object.__setattr__(self, "file_path", Path(self.file_path))
        if self.success and (self.file_path is None or self.error_kind is not None):
            raise ValueError("successful download requires file_path and no error_kind")
        if not self.success and self.error_kind is None:
            raise ValueError("failed download requires error_kind")
