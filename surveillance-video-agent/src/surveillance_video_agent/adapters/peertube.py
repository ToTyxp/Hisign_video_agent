"""PeerTube adapter with an explicit, auditable federation policy.

PeerTube is federated: a Sepia Search response can point at any instance on the
Internet.  This adapter never treats such a result as authority to contact that
host.  Search and media instance hosts are configured separately, and every
HTTP redirect and yt-dlp source URL is checked against the relevant allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from surveillance_video_agent.adapters.base import CommandRunner, PlatformAdapter
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


_PLATFORM = "peertube"
_DEFAULT_SEARCH_ENDPOINT = "https://sepiasearch.org/api/v1/search/videos"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_WATCH_PATH_RE = re.compile(
    r"^/videos/watch/"
    r"(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)
_HOST_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$",
    re.IGNORECASE,
)
_MAX_JSON_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class HttpJsonResponse:
    """Minimal HTTP response used by the injectable PeerTube client."""

    status_code: int
    payload: Any
    final_url: str


class HttpJsonClient(Protocol):
    """Injectable HTTP boundary; tests provide a non-network fake."""

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int],
        timeout_seconds: float,
        allowed_hosts: frozenset[str],
    ) -> HttpJsonResponse:
        ...


class HttpClientFailure(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _AllowlistRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        super().__init__()
        self._allowed_hosts = allowed_hosts

    def redirect_request(  # type: ignore[override]
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        _validate_https_url(newurl, self._allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibHttpJsonClient:
    """Small standard-library client that validates redirects before following."""

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int],
        timeout_seconds: float,
        allowed_hosts: frozenset[str],
    ) -> HttpJsonResponse:
        _validate_https_url(url, allowed_hosts)
        query = urlencode(params)
        separator = "&" if urlsplit(url).query else "?"
        request_url = f"{url}{separator}{query}" if query else url
        opener = build_opener(_AllowlistRedirectHandler(allowed_hosts))
        request = Request(
            request_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "surveillance-video-agent/2 peertube-adapter",
            },
            method="GET",
        )
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                final_url = response.geturl()
                _validate_https_url(final_url, allowed_hosts)
                body = response.read(_MAX_JSON_BYTES + 1)
                if len(body) > _MAX_JSON_BYTES:
                    raise HttpClientFailure("PeerTube JSON response exceeds 4 MiB")
                status = int(response.status)
        except HTTPError as exc:
            raise HttpClientFailure(
                f"PeerTube HTTP request failed with status {exc.code}",
                status_code=int(exc.code),
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise HttpClientFailure("PeerTube HTTP request failed") from exc

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpClientFailure("PeerTube response is not valid UTF-8 JSON") from exc
        return HttpJsonResponse(status_code=status, payload=payload, final_url=final_url)


class PeerTubeAdapter(PlatformAdapter):
    """Adapter for Sepia Search plus explicitly allowlisted PeerTube instances.

    ``allowed_instance_hosts`` is deliberately required for useful discovery.
    An empty allowlist is valid and safe, but all federated search results will
    be ignored.  This makes expanding federation scope a visible configuration
    change rather than an implicit trust decision.
    """

    platform = _PLATFORM

    def __init__(
        self,
        *,
        search_endpoint: str = _DEFAULT_SEARCH_ENDPOINT,
        allowed_instance_hosts: Iterable[str] = (),
        http_client: HttpJsonClient | None = None,
        runner: CommandRunner | None = None,
        executable: str = "yt-dlp",
    ) -> None:
        super().__init__(runner=runner, executable=executable)
        search_host = _public_host_from_url(search_endpoint)
        expected_path = "/api/v1/search/videos"
        if urlsplit(search_endpoint).path.rstrip("/") != expected_path:
            raise ValueError(f"PeerTube search endpoint must end with {expected_path}")
        self._search_endpoint = _canonical_url(search_endpoint)
        self._search_hosts = frozenset({search_host})
        self._allowed_instance_hosts = frozenset(
            _normalize_configured_host(host) for host in allowed_instance_hosts
        )
        self._http = http_client or UrllibHttpJsonClient()

    @property
    def search_endpoint(self) -> str:
        return self._search_endpoint

    @property
    def allowed_instance_hosts(self) -> frozenset[str]:
        return self._allowed_instance_hosts

    def search(self, request: SearchRequest) -> list[SearchHit]:
        self._validate_search_request(request)
        try:
            response = self._http.get_json(
                self._search_endpoint,
                params={
                    "search": request.query,
                    "start": 0,
                    "count": min(request.limit, 20),
                    "sort": "-match",
                },
                timeout_seconds=60.0,
                allowed_hosts=self._search_hosts,
            )
        except HttpClientFailure as exc:
            raise _adapter_error(_http_error_kind(exc.status_code), str(exc)) from exc

        try:
            _validate_https_url(response.final_url, self._search_hosts)
        except ValueError as exc:
            raise _adapter_error(
                AdapterErrorKind.UNSUPPORTED,
                "PeerTube search redirected outside the configured search host",
            ) from exc
        if response.status_code != 200:
            raise _adapter_error(
                _http_error_kind(response.status_code),
                f"PeerTube search returned status {response.status_code}",
            )
        if not isinstance(response.payload, Mapping):
            raise _adapter_error(AdapterErrorKind.TOOL_ERROR, "Malformed PeerTube search response")
        raw_items = response.payload.get("data", [])
        if not isinstance(raw_items, list):
            raise _adapter_error(AdapterErrorKind.TOOL_ERROR, "PeerTube search data is not a list")

        hits: list[SearchHit] = []
        seen_keys: set[str] = set()
        for raw_position, raw in enumerate(raw_items, start=1):
            if len(hits) >= min(request.limit, 20):
                break
            normalized = self._normalize_search_item(raw)
            if normalized is None:
                continue
            source_id, source_url, source = normalized
            candidate_key = make_candidate_key(_PLATFORM, source_id)
            if candidate_key in seen_keys:
                continue
            seen_keys.add(candidate_key)
            hits.append(
                SearchHit(
                    platform=_PLATFORM,
                    source_id=source_id,
                    candidate_key=candidate_key,
                    source_url=source_url,
                    position=raw_position,
                    query=request.query,
                    lang=request.lang,
                    query_pack_version=request.query_pack_version,
                    title=_optional_text(source.get("name")),
                    uploader=_display_name(source.get("account")),
                    duration_seconds=_optional_nonnegative_int(source.get("duration")),
                    raw_summary=_search_summary(source),
                )
            )
        return hits

    def probe(self, request: ProbeRequest) -> ProbeResult:
        self._validate_common_request(request.platform, request.network_config)
        expected_key = make_candidate_key(_PLATFORM, request.source_id)
        if request.candidate_key != expected_key:
            raise ValueError("candidate_key does not match source_id")
        source_id = _validated_uuid(request.source_id)
        source_host, canonical_url = _validate_source_identity(
            request.source_url,
            source_id,
            self._allowed_instance_hosts,
        )
        api_url = f"https://{source_host}/api/v1/videos/{quote(source_id, safe='')}"

        try:
            response = self._http.get_json(
                api_url,
                params={},
                timeout_seconds=request.timeout_seconds,
                allowed_hosts=frozenset({source_host}),
            )
        except HttpClientFailure as exc:
            raise _adapter_error(_http_error_kind(exc.status_code), str(exc)) from exc
        try:
            _validate_https_url(response.final_url, frozenset({source_host}))
        except ValueError as exc:
            raise _adapter_error(
                AdapterErrorKind.UNSUPPORTED,
                "PeerTube probe redirected outside the configured instance",
            ) from exc
        if response.status_code != 200:
            raise _adapter_error(
                _http_error_kind(response.status_code),
                f"PeerTube probe returned status {response.status_code}",
            )
        if not isinstance(response.payload, Mapping):
            raise _adapter_error(AdapterErrorKind.TOOL_ERROR, "Malformed PeerTube probe response")
        payload = response.payload
        returned_id = payload.get("uuid")
        if not isinstance(returned_id, str) or returned_id.lower() != source_id.lower():
            raise _adapter_error(AdapterErrorKind.TOOL_ERROR, "PeerTube probe UUID mismatch")

        media = _media_shape(payload)
        return ProbeResult(
            platform=_PLATFORM,
            source_id=source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            canonical_url=canonical_url,
            title=_optional_text(payload.get("name")),
            video_description=_optional_text(payload.get("description"), preserve_empty=True),
            tags=_normalized_tags(payload.get("tags")),
            uploader=_display_name(payload.get("account")),
            uploader_id=_entity_id(payload.get("account")),
            channel=_display_name(payload.get("channel") or payload.get("videoChannel")),
            playlist=None,
            duration_seconds=_optional_nonnegative_int(payload.get("duration")),
            upload_date=_optional_text(
                payload.get("originallyPublishedAt") or payload.get("publishedAt")
            ),
            availability=_availability(payload),
            filesize_approx=media[0],
            width=media[1],
            height=media[2],
            is_live=_optional_bool(payload.get("isLive"), payload.get("live")),
            live_status=_live_status(payload),
            raw_metadata=_probe_summary(payload),
        )

    def download(self, request: DownloadRequest) -> DownloadResult:
        self._validate_common_request(request.platform, request.network_config)
        if request.candidate_key != make_candidate_key(_PLATFORM, request.source_id):
            raise ValueError("candidate_key does not match source_id")
        source_id = _validated_uuid(request.source_id)
        _, canonical_url = _validate_source_identity(
            request.source_url,
            source_id,
            self._allowed_instance_hosts,
        )
        output_dir = _prepare_managed_output_dir(request.managed_root, request.output_dir)

        safe_stem = _safe_stem(request.candidate_key)
        output_template = output_dir / f"{safe_stem}.%(ext)s"
        args = [
            self.executable,
            "--ignore-config",
            "--no-plugin-dirs",
            "--no-playlist",
            "--no-write-subs",
            "--no-write-auto-subs",
            "--no-write-comments",
            "--no-write-thumbnail",
            "--restrict-filenames",
            "--max-filesize",
            str(request.max_filesize_bytes),
            "--format",
            f"bv*[height<={request.max_height}]+ba/b[height<={request.max_height}]/b",
            "--merge-output-format",
            "mp4",
            "--print",
            "after_move:filepath",
            "--output",
            str(output_template),
            "--",
            canonical_url,
        ]
        try:
            command = self.run_command(
                args,
                timeout_seconds=request.timeout_seconds,
                cwd=output_dir,
            )
        except (OSError, RuntimeError) as exc:
            return _failed_download(request, AdapterErrorKind.TOOL_ERROR, str(exc))

        if command.timed_out:
            return _failed_download(
                request,
                AdapterErrorKind.TIMEOUT,
                "PeerTube download timed out",
                returncode=command.returncode,
            )
        if command.returncode != 0:
            return _failed_download(
                request,
                _download_error_kind(command.stderr),
                _safe_error_message(command.stderr),
                returncode=command.returncode,
            )

        try:
            file_path = _downloaded_path(command.stdout, output_dir, safe_stem)
            size = file_path.stat().st_size
            if size > request.max_filesize_bytes:
                raise _ResourceLimitError("Downloaded file exceeds configured 2 GB boundary")
        except _ResourceLimitError as exc:
            return _failed_download(
                request,
                AdapterErrorKind.RESOURCE_LIMIT,
                str(exc),
                returncode=command.returncode,
            )
        except (ValueError, OSError) as exc:
            return _failed_download(
                request,
                AdapterErrorKind.TOOL_ERROR,
                str(exc),
                returncode=command.returncode,
            )

        return DownloadResult(
            platform=_PLATFORM,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            success=True,
            file_path=file_path,
            bytes_downloaded=size,
            returncode=command.returncode,
            raw_summary={"adapter": "peertube", "height_limit": request.max_height},
        )

    @staticmethod
    def _validate_common_request(platform: str, network_config: str) -> None:
        if platform != _PLATFORM:
            raise ValueError("Request platform is not peertube")
        if network_config != "default":
            raise ValueError("PeerTube v1 only supports network_config=default")

    def _validate_search_request(self, request: SearchRequest) -> None:
        self._validate_common_request(request.platform, request.network_config)
        if request.lang not in {"en", "es", "fr"}:
            raise ValueError("Unsupported query language")
        if not 1 <= request.limit <= 20:
            raise ValueError("Search limit must be between 1 and 20")

    def _normalize_search_item(
        self, raw: Any
    ) -> tuple[str, str, Mapping[str, Any]] | None:
        if not isinstance(raw, Mapping):
            return None
        raw_id = raw.get("uuid")
        raw_url = raw.get("url")
        if not isinstance(raw_id, str) or not isinstance(raw_url, str):
            return None
        try:
            source_id = _validated_uuid(raw_id)
            source_host = _validate_https_url(raw_url, self._allowed_instance_hosts)
        except ValueError:
            return None
        return source_id, _canonical_watch_url(source_host, source_id), raw


class _ResourceLimitError(ValueError):
    pass


def _adapter_error(kind: AdapterErrorKind, message: str) -> AdapterError:
    return AdapterError(kind=kind, message=message)


def _http_error_kind(status: int | None) -> AdapterErrorKind:
    if status == 429:
        return AdapterErrorKind.RATE_LIMITED
    if status in {404, 410}:
        return AdapterErrorKind.NOT_FOUND
    if status in {401, 403}:
        return AdapterErrorKind.PRIVATE
    if status is None or status >= 500:
        return AdapterErrorKind.NETWORK
    return AdapterErrorKind.TOOL_ERROR


def _download_error_kind(stderr: str) -> AdapterErrorKind:
    text = stderr.lower()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return AdapterErrorKind.RATE_LIMITED
    if "404" in text or "not found" in text or "removed" in text:
        return AdapterErrorKind.NOT_FOUND
    if "private" in text or "login required" in text or "forbidden" in text:
        return AdapterErrorKind.PRIVATE
    if "max-filesize" in text or "larger than max-filesize" in text:
        return AdapterErrorKind.RESOURCE_LIMIT
    if "unsupported url" in text:
        return AdapterErrorKind.UNSUPPORTED
    if "network" in text or "connection" in text or "timed out" in text:
        return AdapterErrorKind.NETWORK
    return AdapterErrorKind.TOOL_ERROR


def _safe_error_message(stderr: str) -> str:
    # Never surface response bodies, URLs with query strings, cookies or headers.
    line = next((part.strip() for part in stderr.splitlines() if part.strip()), "yt-dlp failed")
    line = re.sub(r"https://[^\s]+", "[redacted-url]", line, flags=re.IGNORECASE)
    line = re.sub(
        r"(?i)(cookie|authorization|token|signature|password)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        line,
    )
    return line[:500]


def _failed_download(
    request: DownloadRequest,
    kind: AdapterErrorKind,
    message: str,
    *,
    returncode: int | None = None,
) -> DownloadResult:
    return DownloadResult(
        platform=_PLATFORM,
        source_id=request.source_id,
        candidate_key=request.candidate_key,
        source_url=request.source_url,
        success=False,
        returncode=returncode,
        error_kind=kind,
        error_message=message[:500],
        raw_summary={"adapter": "peertube"},
    )


def _normalize_configured_host(host: str) -> str:
    value = host.strip().lower().rstrip(".")
    # Hosts, not URLs, are accepted so that policy diffs remain unambiguous.
    if "://" in value or "/" in value or "@" in value or ":" in value:
        raise ValueError(f"Invalid PeerTube instance host: {host!r}")
    _validate_public_hostname(value)
    return value


def _public_host_from_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise ValueError("PeerTube endpoints must use absolute HTTPS URLs")
    if parts.username or parts.password or parts.port not in {None, 443}:
        raise ValueError("PeerTube URLs cannot contain credentials or non-HTTPS ports")
    host = parts.hostname.lower().rstrip(".")
    _validate_public_hostname(host)
    return host


def _validate_https_url(url: str, allowed_hosts: frozenset[str]) -> str:
    host = _public_host_from_url(url)
    if host not in allowed_hosts:
        raise ValueError(f"PeerTube host is not allowlisted: {host}")
    return host


def _validate_public_hostname(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise ValueError("Private, loopback, reserved and link-local IP hosts are forbidden")
        return
    if (
        not _HOST_RE.fullmatch(host)
        or host == "localhost"
        or host.endswith((".local", ".localhost", ".internal", ".home", ".arpa"))
    ):
        raise ValueError(f"Host is not a syntactically public DNS name: {host!r}")


def _canonical_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    netloc = host if parts.port in {None, 443} else parts.netloc
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", netloc, path, "", ""))


def _canonical_watch_url(host: str, source_id: str) -> str:
    return f"https://{host}/videos/watch/{source_id.lower()}"


def _validate_source_identity(
    url: str,
    source_id: str,
    allowed_hosts: frozenset[str],
) -> tuple[str, str]:
    host = _validate_https_url(url, allowed_hosts)
    parts = urlsplit(url)
    if parts.query or parts.fragment:
        raise ValueError("PeerTube source URL must not contain a query or fragment")
    match = _WATCH_PATH_RE.fullmatch(parts.path)
    if match is None or match.group("uuid").lower() != source_id.lower():
        raise ValueError("PeerTube source URL path does not match source_id")
    return host, _canonical_watch_url(host, source_id)


def _validated_uuid(source_id: str) -> str:
    if not _UUID_RE.fullmatch(source_id):
        raise ValueError("PeerTube source_id must be a UUID")
    return source_id.lower()


def _optional_text(value: Any, *, preserve_empty: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    if not preserve_empty and not value.strip():
        return None
    return value


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return None


def _optional_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None


def _display_name(entity: Any) -> str | None:
    if not isinstance(entity, Mapping):
        return None
    for key in ("displayName", "name"):
        value = _optional_text(entity.get(key))
        if value:
            return value
    return None


def _entity_id(entity: Any) -> str | None:
    if not isinstance(entity, Mapping):
        return None
    raw_id = entity.get("id")
    if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool):
        return str(raw_id)
    name = _optional_text(entity.get("name"))
    host = _optional_text(entity.get("host"))
    if name and host:
        return f"{name}@{host.lower()}"
    return name


def _normalized_tags(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    tags: list[str] = []
    for item in raw:
        value = item.get("name") if isinstance(item, Mapping) else item
        if isinstance(value, str) and value.strip() and value not in tags:
            tags.append(value)
    return tuple(tags)


def _availability(payload: Mapping[str, Any]) -> str | None:
    privacy = payload.get("privacy")
    state = payload.get("state")
    privacy_id = privacy.get("id") if isinstance(privacy, Mapping) else None
    state_id = state.get("id") if isinstance(state, Mapping) else None
    if privacy_id == 1 and state_id in {None, 1}:
        return "public"
    label = _entity_label(privacy) or _entity_label(state)
    return label.lower() if label else None


def _live_status(payload: Mapping[str, Any]) -> str | None:
    live = _optional_bool(payload.get("isLive"), payload.get("live"))
    if live is True:
        return "is_live"
    if live is False:
        return "not_live"
    return None


def _entity_label(entity: Any) -> str | None:
    if not isinstance(entity, Mapping):
        return None
    for key in ("label", "displayName", "name"):
        value = _optional_text(entity.get(key))
        if value:
            return value
    return None


def _media_shape(payload: Mapping[str, Any]) -> tuple[int | None, int | None, int | None]:
    files: list[Mapping[str, Any]] = []
    direct = payload.get("files")
    if isinstance(direct, list):
        files.extend(item for item in direct if isinstance(item, Mapping))
    playlists = payload.get("streamingPlaylists")
    if isinstance(playlists, list):
        for playlist in playlists:
            if isinstance(playlist, Mapping) and isinstance(playlist.get("files"), list):
                files.extend(
                    item for item in playlist["files"] if isinstance(item, Mapping)
                )
    best: tuple[int, int | None, int | None] | None = None
    max_size: int | None = None
    for item in files:
        size = _optional_nonnegative_int(item.get("size"))
        if size is not None:
            max_size = max(max_size or 0, size)
        resolution = item.get("resolution")
        height = None
        if isinstance(resolution, Mapping):
            height = _optional_nonnegative_int(resolution.get("id"))
        height = height or _optional_nonnegative_int(item.get("height"))
        width = _optional_nonnegative_int(item.get("width"))
        rank = height or 0
        if best is None or rank > best[0]:
            best = (rank, width, height)
    if best is None:
        return (
            max_size,
            _optional_nonnegative_int(payload.get("width")),
            _optional_nonnegative_int(payload.get("height")),
        )
    return max_size, best[1], best[2]


def _search_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "uuid": payload.get("uuid"),
        "name": payload.get("name"),
        "duration": payload.get("duration"),
        "published_at": payload.get("publishedAt"),
        "account": _display_name(payload.get("account")),
        "channel": _display_name(payload.get("channel") or payload.get("videoChannel")),
    }


def _probe_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "uuid": payload.get("uuid"),
        "name": payload.get("name"),
        "duration": payload.get("duration"),
        "published_at": payload.get("publishedAt"),
        "originally_published_at": payload.get("originallyPublishedAt"),
        "privacy": _entity_label(payload.get("privacy")),
        "state": _entity_label(payload.get("state")),
        "account": _display_name(payload.get("account")),
        "channel": _display_name(payload.get("channel") or payload.get("videoChannel")),
        "tags": list(_normalized_tags(payload.get("tags"))),
    }


def _prepare_managed_output_dir(managed_root: Path, output_dir: Path) -> Path:
    if not managed_root.exists() or not managed_root.is_dir() or managed_root.is_symlink():
        raise ValueError("managed_root must be an existing non-symlink directory")
    root = managed_root.resolve(strict=True)
    prospective = output_dir.resolve(strict=False)
    if prospective != root and root not in prospective.parents:
        raise ValueError("output_dir escapes managed_root")
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved = output_dir.resolve(strict=True)
    if resolved != prospective or (resolved != root and root not in resolved.parents):
        raise ValueError("output_dir resolves outside managed_root")
    return resolved


def _safe_stem(candidate_key: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate_key).strip("._")
    if not stem:
        raise ValueError("candidate_key does not produce a safe filename")
    return stem[:180]


def _downloaded_path(stdout: str, output_dir: Path, safe_stem: str) -> Path:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    candidates: list[Path] = []
    if lines:
        candidates.append(Path(lines[-1]))
    candidates.extend(sorted(output_dir.glob(f"{safe_stem}.*")))
    seen: set[Path] = set()
    valid: list[Path] = []
    for candidate in candidates:
        absolute = candidate if candidate.is_absolute() else output_dir / candidate
        if absolute in seen or not absolute.exists():
            continue
        seen.add(absolute)
        resolved = absolute.resolve(strict=True)
        if absolute.is_symlink() or output_dir not in resolved.parents or not resolved.is_file():
            raise ValueError("yt-dlp output escaped the managed output directory")
        if resolved.suffix in {".part", ".ytdl"}:
            continue
        valid.append(resolved)
    unique = list(dict.fromkeys(valid))
    if len(unique) != 1:
        raise ValueError("yt-dlp did not produce exactly one managed media file")
    return unique[0]
