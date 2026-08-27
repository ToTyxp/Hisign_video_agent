"""Adapter interfaces, subprocess isolation, and shared safety helpers."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from surveillance_video_agent.contracts import (
    AdapterErrorKind,
    DownloadRequest,
    DownloadResult,
    ProbeRequest,
    ProbeResult,
    SearchHit,
    SearchRequest,
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class CommandRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: ...


class SubprocessCommandRunner:
    """Execute argv directly. Shell interpretation is always disabled."""

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        child_env["YTDLP_NO_PLUGINS"] = "1"
        try:
            completed = subprocess.run(
                [str(argument) for argument in args],
                cwd=str(cwd) if cwd is not None else None,
                env=child_env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            return CommandResult(
                returncode=124,
                stdout=_timeout_text(error.stdout),
                stderr=_timeout_text(error.stderr),
                timed_out=True,
            )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class BasePlatformAdapter(ABC):
    """Synchronous, stateless platform adapter contract."""

    platform: str

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        executable: str = "yt-dlp",
    ) -> None:
        self.runner = runner or SubprocessCommandRunner()
        self.executable = _resolve_project_yt_dlp(executable)

    @abstractmethod
    def search(self, request: SearchRequest) -> list[SearchHit]:
        raise NotImplementedError

    @abstractmethod
    def probe(self, request: ProbeRequest) -> ProbeResult:
        raise NotImplementedError

    @abstractmethod
    def download(self, request: DownloadRequest) -> DownloadResult:
        raise NotImplementedError

    def validate_request_platform(self, platform: str) -> None:
        if platform != self.platform:
            raise ValueError(
                f"{type(self).__name__} cannot handle platform {platform!r}"
            )

    def run_command(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
    ) -> CommandResult:
        return self.runner.run(
            args,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
            env={"YTDLP_NO_PLUGINS": "1"},
        )


# Public compatibility alias: both names denote the same stable abstract API.
PlatformAdapter = BasePlatformAdapter


def _resolve_project_yt_dlp(executable: str) -> str:
    """Prefer the yt-dlp entry point installed beside the active Python."""

    if executable != "yt-dlp":
        return executable
    # Do not resolve the virtualenv's Python symlink: the sibling entry point
    # lives in the virtualenv bin directory, not beside the base interpreter.
    project_entrypoint = Path(sys.executable).absolute().with_name("yt-dlp")
    if project_entrypoint.is_file() and os.access(project_entrypoint, os.X_OK):
        return str(project_entrypoint)
    return executable


_SECRET_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "cookiefile",
        "http_headers",
        "request_headers",
        "proxy",
        "token",
    }
)
_TRANSIENT_URL_KEYS = frozenset(
    {"fragment_base_url", "manifest_url", "player_url", "signed_url", "url"}
)
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(cookie\s*[:=]\s*)([^\s;]+(?:;[^\s]+)*)"),
    re.compile(r"(?i)([?&](?:sig|signature|token|expire|key)=)[^&\s]+"),
    re.compile(r"https?://[^\s]+\.googlevideo\.com/[^\s]+", re.IGNORECASE),
)


def sanitize_metadata(value: Any, *, _key: str | None = None) -> Any:
    """Return a JSON-compatible copy without credentials or direct media URLs."""

    key = (_key or "").lower()
    if key in _SECRET_KEYS or key in _TRANSIENT_URL_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): sanitize_metadata(item, _key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_metadata(item) for item in value]
    if isinstance(value, str):
        return sanitize_error_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def sanitize_error_text(message: str, *, max_length: int = 2000) -> str:
    cleaned = message
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        cleaned = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]" if match.lastindex else "[REDACTED]", cleaned)
    return cleaned[:max_length]


def classify_command_error(result: CommandResult) -> AdapterErrorKind:
    if result.timed_out:
        return AdapterErrorKind.TIMEOUT
    text = f"{result.stdout}\n{result.stderr}".lower()
    if any(marker in text for marker in ("429", "rate limit", "too many requests")):
        return AdapterErrorKind.RATE_LIMITED
    if any(marker in text for marker in ("private video", "private access", "login required")):
        return AdapterErrorKind.PRIVATE
    if any(marker in text for marker in ("404", "not found", "video unavailable", "does not exist")):
        return AdapterErrorKind.NOT_FOUND
    if any(marker in text for marker in ("unsupported url", "no suitable extractor")):
        return AdapterErrorKind.UNSUPPORTED
    if any(marker in text for marker in ("max-filesize", "maximum file size", "file is larger")):
        return AdapterErrorKind.RESOURCE_LIMIT
    if any(
        marker in text
        for marker in (
            "http error 403",
            "http error 500",
            "http error 502",
            "http error 503",
            "http error 504",
            "connection reset",
            "connection aborted",
            "remote end closed connection",
            "unexpected_eof_while_reading",
            "eof occurred in violation of protocol",
            "network is unreachable",
            "name or service not known",
            "temporary failure in name resolution",
            "timed out",
            "unable to download",
        )
    ):
        return AdapterErrorKind.NETWORK
    return AdapterErrorKind.TOOL_ERROR


def validate_https_url(url: str, *, allowed_hosts: frozenset[str]) -> str:
    """Validate an HTTPS URL against exact hosts and their subdomains."""

    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("source_url must be an HTTPS URL")
    hostname = parsed.hostname.rstrip(".").lower()
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source_url must not include user information")
    if parsed.port not in (None, 443):
        raise ValueError("source_url must not use a non-HTTPS port")
    if not any(hostname == host or hostname.endswith(f".{host}") for host in allowed_hosts):
        raise ValueError(f"source_url host is not allowed: {hostname!r}")
    return url


def ensure_managed_output_dir(managed_root: Path, output_dir: Path) -> Path:
    """Resolve and create a directory that cannot escape its managed root."""

    root = managed_root.resolve()
    output = output_dir.resolve()
    if output == root:
        raise ValueError("output_dir must be a child of managed_root")
    try:
        output.relative_to(root)
    except ValueError as error:
        raise ValueError("output_dir escapes managed_root") from error
    root.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    # Resolve a second time after creation so an existing symlink cannot redirect writes.
    resolved_output = output.resolve(strict=True)
    try:
        resolved_output.relative_to(root.resolve(strict=True))
    except ValueError as error:
        raise ValueError("output_dir resolves outside managed_root") from error
    return resolved_output


def ensure_child_path(parent: Path, child: Path) -> Path:
    if child.is_symlink():
        raise ValueError("tool output path must not be a symlink")
    resolved_parent = parent.resolve(strict=True)
    resolved_child = child.resolve(strict=True)
    try:
        resolved_child.relative_to(resolved_parent)
    except ValueError as error:
        raise ValueError("tool output path escapes output_dir") from error
    return resolved_child
