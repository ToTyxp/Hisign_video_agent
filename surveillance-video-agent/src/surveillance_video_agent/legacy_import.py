"""Read-only legacy import: YouTube IDs/statuses and accepted-uploader priors only."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from surveillance_video_agent.db import CandidateDatabase, utc_now


_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True, slots=True)
class LegacyImportResult:
    import_id: str
    imported_download_count: int
    imported_uploader_prior_count: int
    missing_metadata_count: int
    accepted_count: int
    downloaded_only_count: int


def import_legacy_state(
    database: CandidateDatabase,
    *,
    history_path: Path,
    info_cache_dir: Path,
    accepted_archive_path: Path | None = None,
) -> LegacyImportResult:
    history = Path(history_path).resolve()
    cache = Path(info_cache_dir).resolve()
    archive = (
        Path(accepted_archive_path).resolve()
        if accepted_archive_path is not None
        else None
    )
    history_bytes = _read_bytes(history)
    history_hash = hashlib.sha256(history_bytes).hexdigest()
    try:
        document = json.loads(history_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("legacy download history is not valid UTF-8 JSON") from error
    records = document.get("records") if isinstance(document, dict) else None
    if not isinstance(records, dict):
        raise ValueError("legacy download history must contain a records object")
    normalized: dict[str, dict] = {}
    for record_key, value in records.items():
        if not isinstance(record_key, str) or not _YOUTUBE_ID.fullmatch(record_key):
            raise ValueError("legacy history contains an invalid YouTube ID")
        if not isinstance(value, dict) or value.get("id") != record_key:
            raise ValueError("legacy history record identity mismatch")
        status = value.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError("legacy history status must be non-empty text")
        normalized[record_key] = value
    accepted_ids = {
        source_id
        for source_id, record in normalized.items()
        if record["status"] == "accepted"
    }
    if archive is not None:
        archive_ids = _read_archive_ids(archive)
        if archive_ids != accepted_ids:
            raise ValueError("accepted archive does not match accepted history records")
    archive_identity = str(archive) if archive is not None else None
    import_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"legacy:{history_hash}:{archive_identity or 'no-archive'}",
        )
    )
    existing = database.connection.execute(
        "SELECT * FROM legacy_imports WHERE import_id = ?", (import_id,)
    ).fetchone()
    if existing is not None:
        return LegacyImportResult(
            import_id=import_id,
            imported_download_count=int(existing["imported_download_count"]),
            imported_uploader_prior_count=int(
                existing["imported_uploader_prior_count"]
            ),
            missing_metadata_count=int(existing["missing_metadata_count"]),
            accepted_count=len(accepted_ids),
            downloaded_only_count=len(normalized) - len(accepted_ids),
        )

    uploader_counts: dict[str, int] = {}
    uploader_sources: dict[str, list[str]] = {}
    missing_metadata = 0
    for source_id in sorted(accepted_ids):
        metadata_path = cache / f"{source_id}.info.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
            missing_metadata += 1
            continue
        if not isinstance(metadata, dict) or metadata.get("id") != source_id:
            missing_metadata += 1
            continue
        uploader_id = _first_text(
            metadata.get("uploader_id"), metadata.get("channel_id")
        )
        if uploader_id is None:
            missing_metadata += 1
            continue
        uploader_counts[uploader_id] = uploader_counts.get(uploader_id, 0) + 1
        uploader_sources.setdefault(uploader_id, []).append(source_id)

    now = utc_now()
    with database.transaction() as connection:
        for source_id, record in sorted(normalized.items()):
            connection.execute(
                """
                INSERT INTO legacy_downloads(
                    candidate_key, youtube_id, legacy_status,
                    source_path, imported_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"youtube:{source_id}",
                    source_id,
                    record["status"],
                    f"{history}#records/{source_id}",
                    now,
                ),
            )
        for uploader_id, count in sorted(uploader_counts.items()):
            points = 2 if count >= 2 else 1
            connection.execute(
                """
                INSERT INTO uploader_priors(
                    platform, uploader_id, completed_count, prior_points,
                    provenance_json, calculated_at
                ) VALUES ('youtube', ?, ?, ?, ?, ?)
                """,
                (
                    uploader_id,
                    count,
                    points,
                    json.dumps(
                        {
                            "history_sha256": history_hash,
                            "criterion": "legacy status accepted only",
                            "source_ids": sorted(uploader_sources[uploader_id]),
                            "rejection_reasons_migrated": False,
                            "channel_ban_created": False,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
        connection.execute(
            """
            INSERT INTO legacy_imports(
                import_id, history_path, history_sha256, archive_path,
                imported_download_count, imported_uploader_prior_count,
                missing_metadata_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                import_id,
                str(history),
                history_hash,
                archive_identity,
                len(normalized),
                len(uploader_counts),
                missing_metadata,
                now,
            ),
        )
    return LegacyImportResult(
        import_id=import_id,
        imported_download_count=len(normalized),
        imported_uploader_prior_count=len(uploader_counts),
        missing_metadata_count=missing_metadata,
        accepted_count=len(accepted_ids),
        downloaded_only_count=len(normalized) - len(accepted_ids),
    )


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as error:
        raise ValueError(f"legacy file not found: {path}") from error


def _read_archive_ids(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        raise ValueError(f"accepted archive not found: {path}") from error
    values = [line.strip() for line in lines if line.strip()]
    if len(values) != len(set(values)) or any(
        not _YOUTUBE_ID.fullmatch(value) for value in values
    ):
        raise ValueError("accepted archive contains duplicate or invalid IDs")
    return set(values)


def _first_text(*values) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None
