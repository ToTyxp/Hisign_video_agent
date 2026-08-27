"""Batch queue consumption, global serial download, and recoverable publication."""

from __future__ import annotations

import json
import os
import random
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from surveillance_video_agent.adapters import PlatformAdapter
from surveillance_video_agent.contracts import (
    AdapterErrorKind,
    DownloadRequest,
    DownloadResult,
)
from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.technical import sha256_file, technical_check


@dataclass(frozen=True, slots=True)
class DownloadWorkerConfig:
    internal_root: Path
    output_root: Path
    max_height: int = 1080
    max_file_bytes: int = 2 * 1024 * 1024 * 1024
    campaign_max_bytes: int = 30 * 1024 * 1024 * 1024
    timeout_seconds: float = 7200
    cooldown_min_seconds: float = 10
    cooldown_max_seconds: float = 20
    transient_retry_attempts: int = 2
    retry_backoff_base_seconds: float = 20
    retry_backoff_max_seconds: float = 60
    retry_jitter_max_seconds: float = 5
    adapter_version: str = "0.2.0"

    def __post_init__(self) -> None:
        internal = Path(self.internal_root).resolve()
        output = Path(self.output_root).resolve()
        object.__setattr__(self, "internal_root", internal)
        object.__setattr__(self, "output_root", output)
        if not internal.is_absolute() or not output.is_absolute() or internal == output:
            raise ValueError("internal_root and output_root must be distinct absolute paths")
        if not 1 <= self.max_height <= 1080:
            raise ValueError("max_height exceeds the v1 boundary")
        if not 1 <= self.max_file_bytes <= 2 * 1024 * 1024 * 1024:
            raise ValueError("max_file_bytes exceeds the v1 boundary")
        if self.campaign_max_bytes <= 0 or self.timeout_seconds <= 0:
            raise ValueError("campaign byte budget and timeout must be positive")
        if not 0 <= self.cooldown_min_seconds <= self.cooldown_max_seconds:
            raise ValueError("cooldown range is invalid")
        if not 0 <= self.transient_retry_attempts <= 5:
            raise ValueError("transient retry attempts must be between 0 and 5")
        if not 0 <= self.retry_backoff_base_seconds <= self.retry_backoff_max_seconds:
            raise ValueError("retry backoff range is invalid")
        if self.retry_jitter_max_seconds < 0:
            raise ValueError("retry jitter must be non-negative")


@dataclass(frozen=True, slots=True)
class DownloadOutcome:
    attempt_id: str
    candidate_key: str
    status: str
    media_path: str | None


def enqueue_downloads(database: CandidateDatabase, batch_id: str) -> tuple[str, ...]:
    """Consume reviewed candidates up to the current subtype capacity."""

    with database.transaction() as connection:
        batch = connection.execute(
            "SELECT * FROM secondary_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if batch is None or batch["status"] not in {"reviewed", "queued"}:
            raise ValueError("batch must be reviewed or queued")
        policy = connection.execute(
            """
            SELECT subtype_limits_json FROM campaign_policy_versions
            WHERE campaign_id = ? AND policy_version = ?
            """,
            (batch["campaign_id"], batch["campaign_policy_version"]),
        ).fetchone()
        if policy is None:
            raise ValueError("batch campaign capacity policy is missing")
        limits = json.loads(policy["subtype_limits_json"])
        items = connection.execute(
            """
            SELECT i.*, d.decision
            FROM secondary_batch_items i
            JOIN secondary_filter_decisions d
              ON d.batch_id = i.batch_id AND d.candidate_key = i.candidate_key
            WHERE i.batch_id = ? AND d.decision = 'download_eligible'
            ORDER BY i.rank, i.candidate_key
            """,
            (batch_id,),
        ).fetchall()
        queued: list[str] = []
        for item in items:
            existing = connection.execute(
                "SELECT 1 FROM queue_assignments WHERE candidate_key = ?",
                (item["candidate_key"],),
            ).fetchone()
            if existing is not None:
                continue
            limit = int(limits.get(item["subtype"], 0))
            reserved = connection.execute(
                """
                SELECT COUNT(*)
                FROM queue_assignments q
                JOIN candidates c ON c.candidate_key = q.candidate_key
                WHERE q.campaign_id = ? AND q.subtype = ?
                  AND c.status IN ('task_queued', 'downloaded')
                """,
                (batch["campaign_id"], item["subtype"]),
            ).fetchone()[0]
            if reserved >= limit:
                continue
            candidate = connection.execute(
                "SELECT status, resource_eligible FROM candidates WHERE candidate_key = ?",
                (item["candidate_key"],),
            ).fetchone()
            if (
                candidate is None
                or candidate["status"] != "source_qualified"
                or candidate["resource_eligible"] != 1
            ):
                continue
            connection.execute(
                """
                INSERT INTO queue_assignments(
                    candidate_key, batch_id, campaign_id, subtype, rank,
                    queued_at, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["candidate_key"],
                    batch_id,
                    batch["campaign_id"],
                    item["subtype"],
                    item["rank"],
                    utc_now(),
                    batch["run_id"],
                ),
            )
            database.transition_candidate_in_transaction(
                connection,
                item["candidate_key"],
                "task_queued",
                reason="Secondary Batch candidate queued for serial download",
                run_id=batch["run_id"],
            )
            connection.execute(
                """
                UPDATE frontier_entries SET status = 'consumed', updated_at = ?
                WHERE candidate_key = ? AND campaign_id = ?
                  AND subtype = ? AND run_id = ?
                """,
                (
                    utc_now(),
                    item["candidate_key"],
                    batch["campaign_id"],
                    item["subtype"],
                    batch["run_id"],
                ),
            )
            queued.append(item["candidate_key"])
        if queued:
            connection.execute(
                "UPDATE secondary_batches SET status = 'queued' WHERE batch_id = ?",
                (batch_id,),
            )
        elif not _has_unassigned_capacity(
            connection, batch_id, batch["campaign_id"], limits
        ):
            _complete_batch_and_release(connection, batch_id, batch["run_id"])
        return tuple(queued)


class SerialDownloadWorker:
    def __init__(
        self,
        database: CandidateDatabase,
        adapters: Mapping[str, PlatformAdapter],
        config: DownloadWorkerConfig,
        *,
        checker: Callable[[Path], dict] = technical_check,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.database = database
        self.adapters = dict(adapters)
        self.config = config
        self.checker = checker
        self.sleeper = sleeper
        self.rng = rng or random.Random()
        self.config.internal_root.mkdir(parents=True, exist_ok=True)
        self.config.output_root.mkdir(parents=True, exist_ok=True)
        if os.stat(self.config.internal_root).st_dev != os.stat(self.config.output_root).st_dev:
            raise ValueError("internal and output roots must share a filesystem")

    def process_next(self) -> DownloadOutcome | None:
        claimed = self._claim_next()
        if claimed is None:
            return None
        attempt_id = claimed["attempt_id"]
        candidate_key = claimed["candidate_key"]
        cooldown = float(claimed["cooldown_seconds"])
        if cooldown:
            self.sleeper(cooldown)
        adapter = self.adapters.get(claimed["platform"])
        if adapter is None:
            return self._finalize_download_failure(
                claimed,
                AdapterErrorKind.UNSUPPORTED,
                "no adapter registered for platform",
            )
        temp_dir = (
            self.config.internal_root
            / "tmp"
            / "downloads"
            / claimed["run_id"]
            / attempt_id
        )
        temp_dir.mkdir(parents=True, exist_ok=True)
        download_request = DownloadRequest(
            platform=claimed["platform"],
            source_id=claimed["source_id"],
            candidate_key=candidate_key,
            source_url=claimed["source_url"],
            managed_root=self.config.internal_root,
            output_dir=temp_dir,
            network_config="default",
            request_id=f"{attempt_id}:download",
            run_id=claimed["run_id"],
            max_height=self.config.max_height,
            max_filesize_bytes=int(claimed["permitted_file_bytes"]),
            timeout_seconds=self.config.timeout_seconds,
        )
        try:
            result = adapter.download(download_request)
            for retry_ordinal in range(1, self.config.transient_retry_attempts + 1):
                if result.success or not _is_transient_failure(result):
                    break
                delay = self._retry_delay(retry_ordinal)
                self._record_retry_event(
                    claimed,
                    result,
                    retry_ordinal=retry_ordinal,
                    delay_seconds=delay,
                )
                self.sleeper(delay)
                result = adapter.download(download_request)
        except Exception as error:
            return self._finalize_download_failure(
                claimed,
                AdapterErrorKind.TOOL_ERROR,
                str(error),
            )
        if not result.success or result.file_path is None:
            return self._finalize_download_failure(
                claimed,
                result.error_kind or AdapterErrorKind.TOOL_ERROR,
                result.error_message or "adapter download failed",
                bytes_downloaded=result.bytes_downloaded,
            )
        technical = self.checker(result.file_path)
        self._record_technical(attempt_id, technical)
        if not technical.get("technical_passed"):
            return self._finalize_technical_failure(
                claimed,
                result.file_path,
                result.bytes_downloaded,
                "technical integrity checks failed",
            )
        resource_failures = _post_download_resource_failures(
            technical,
            permitted_file_bytes=int(claimed["permitted_file_bytes"]),
        )
        if resource_failures:
            return self._finalize_download_failure(
                claimed,
                AdapterErrorKind.RESOURCE_LIMIT,
                "; ".join(resource_failures),
                bytes_downloaded=result.bytes_downloaded,
                temp_path=result.file_path,
            )
        sha256 = technical.get("sha256") or sha256_file(result.file_path)
        byte_count = int(technical.get("bytes") or result.file_path.stat().st_size)
        existing = self.database.connection.execute(
            "SELECT * FROM media_objects WHERE sha256 = ? AND publish_status = 'published'",
            (sha256,),
        ).fetchone()
        if existing is not None:
            return self._publish_duplicate(claimed, result.file_path, sha256, byte_count, existing)
        return self._publish_new(claimed, result.file_path, sha256, byte_count)

    def _retry_delay(self, retry_ordinal: int) -> float:
        exponential = min(
            self.config.retry_backoff_max_seconds,
            self.config.retry_backoff_base_seconds * (2 ** (retry_ordinal - 1)),
        )
        return exponential + self.rng.uniform(0, self.config.retry_jitter_max_seconds)

    def _record_retry_event(
        self,
        claimed,
        result: DownloadResult,
        *,
        retry_ordinal: int,
        delay_seconds: float,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO download_retry_events(
                    retry_event_id, attempt_id, candidate_key, retry_ordinal,
                    error_kind, delay_seconds, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    claimed["attempt_id"],
                    claimed["candidate_key"],
                    retry_ordinal,
                    result.error_kind.value,
                    delay_seconds,
                    (result.error_message or "transient download failure")[:2000],
                    utc_now(),
                ),
            )

    def process_until_idle(self) -> tuple[DownloadOutcome, ...]:
        outcomes: list[DownloadOutcome] = []
        self.recover_publish_intents()
        while True:
            reviewed = self.database.connection.execute(
                "SELECT batch_id FROM secondary_batches WHERE status = 'reviewed' ORDER BY created_at"
            ).fetchall()
            for row in reviewed:
                enqueue_downloads(self.database, row["batch_id"])
            outcome = self.process_next()
            if outcome is None:
                return tuple(outcomes)
            outcomes.append(outcome)

    def recover_publish_intents(self) -> int:
        intents = self.database.connection.execute(
            "SELECT * FROM media_publish_intents WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        recovered = 0
        for intent in intents:
            source = Path(intent["temp_path"])
            target = Path(intent["target_path"])
            if target.exists() and sha256_file(target) == intent["sha256"]:
                self._complete_publish_intent(intent, target)
                recovered += 1
            elif source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                _fsync_path(target)
                self._complete_publish_intent(intent, target)
                recovered += 1
            if target.exists():
                batch = self.database.connection.execute(
                    "SELECT batch_id FROM queue_assignments WHERE candidate_key = ?",
                    (intent["candidate_key"],),
                ).fetchone()
                if batch is not None:
                    self._refresh_batch(batch["batch_id"])
        return recovered

    def _claim_next(self):
        with self.database.transaction() as connection:
            running = connection.execute(
                "SELECT 1 FROM download_attempts WHERE status = 'running' LIMIT 1"
            ).fetchone()
            if running is not None:
                return None
            row = connection.execute(
                """
                SELECT q.*, c.platform, c.source_id, c.source_url
                FROM queue_assignments q
                JOIN candidates c ON c.candidate_key = q.candidate_key
                WHERE c.status = 'task_queued'
                  AND c.resource_eligible = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM download_attempts a
                      WHERE a.candidate_key = c.candidate_key
                        AND a.status IN ('running', 'succeeded')
                  )
                ORDER BY q.queued_at, q.rank, q.candidate_key
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            used_bytes = connection.execute(
                """
                SELECT COALESCE(SUM(m.bytes), 0)
                FROM media_objects m
                JOIN queue_assignments q ON q.candidate_key = m.candidate_key
                WHERE q.campaign_id = ? AND m.publish_status = 'published'
                """,
                (row["campaign_id"],),
            ).fetchone()[0]
            remaining = self.config.campaign_max_bytes - int(used_bytes)
            if remaining <= 0:
                return None
            previous = connection.execute(
                """
                SELECT campaign_id, subtype FROM download_attempts
                WHERE status IN ('succeeded', 'failed', 'resource_limit')
                ORDER BY finished_at DESC LIMIT 1
                """
            ).fetchone()
            cooldown = 0.0
            if previous is not None and (
                previous["campaign_id"] != row["campaign_id"]
                or previous["subtype"] != row["subtype"]
            ):
                cooldown = self.rng.uniform(
                    self.config.cooldown_min_seconds,
                    self.config.cooldown_max_seconds,
                )
            attempt_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO download_attempts(
                    attempt_id, candidate_key, platform, run_id,
                    campaign_id, subtype, status, adapter_version,
                    network_config, cooldown_seconds, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, 'default', ?, ?)
                """,
                (
                    attempt_id,
                    row["candidate_key"],
                    row["platform"],
                    row["run_id"],
                    row["campaign_id"],
                    row["subtype"],
                    self.config.adapter_version,
                    cooldown,
                    utc_now(),
                ),
            )
            claimed = dict(row)
            claimed.update(
                {
                    "attempt_id": attempt_id,
                    "cooldown_seconds": cooldown,
                    "permitted_file_bytes": min(self.config.max_file_bytes, remaining),
                }
            )
            return claimed

    def _record_technical(self, attempt_id: str, result: dict) -> None:
        decode = {item["point"]: item for item in result.get("decode", [])}
        video = (result.get("video_streams") or [{}])[0]
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO technical_checks(
                    attempt_id, ffprobe_passed, video_stream_present,
                    decode_first_passed, decode_middle_passed, decode_last_passed,
                    duration_seconds, width, height, sha256, details_json, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    int(result.get("ffprobe_returncode") == 0),
                    int(bool(result.get("video_stream_present"))),
                    int(decode.get("first", {}).get("returncode") == 0),
                    int(decode.get("middle", {}).get("returncode") == 0),
                    int(decode.get("last", {}).get("returncode") == 0),
                    result.get("duration_seconds"),
                    video.get("width"),
                    video.get("height"),
                    result.get("sha256"),
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                    utc_now(),
                ),
            )

    def _finalize_download_failure(
        self,
        claimed,
        error_kind: AdapterErrorKind,
        message: str,
        *,
        bytes_downloaded: int | None = None,
        temp_path: Path | None = None,
    ) -> DownloadOutcome:
        status = "resource_limit" if error_kind == AdapterErrorKind.RESOURCE_LIMIT else "failed"
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE download_attempts SET status = ?, error_kind = ?,
                    error_message = ?, bytes_downloaded = ?, temp_path = ?,
                    finished_at = ?
                WHERE attempt_id = ?
                """,
                (
                    status,
                    error_kind.value,
                    message[:2000],
                    bytes_downloaded,
                    str(temp_path) if temp_path is not None else None,
                    utc_now(),
                    claimed["attempt_id"],
                ),
            )
            self.database.transition_candidate_in_transaction(
                connection,
                claimed["candidate_key"],
                "technical_failed",
                reason=f"download failed: {error_kind.value}",
                run_id=claimed["run_id"],
            )
            _release_cluster_lease(connection, claimed["candidate_key"])
        self._refresh_batch(claimed["batch_id"])
        return DownloadOutcome(claimed["attempt_id"], claimed["candidate_key"], "technical_failed", None)

    def _finalize_technical_failure(
        self,
        claimed,
        media_path: Path,
        bytes_downloaded: int | None,
        message: str,
    ) -> DownloadOutcome:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE download_attempts SET status = 'failed', error_kind = 'tool_error',
                    error_message = ?, temp_path = ?, bytes_downloaded = ?, finished_at = ?
                WHERE attempt_id = ?
                """,
                (
                    message,
                    str(media_path),
                    bytes_downloaded,
                    utc_now(),
                    claimed["attempt_id"],
                ),
            )
            self.database.transition_candidate_in_transaction(
                connection,
                claimed["candidate_key"],
                "technical_failed",
                reason=message,
                run_id=claimed["run_id"],
            )
            _release_cluster_lease(connection, claimed["candidate_key"])
        self._refresh_batch(claimed["batch_id"])
        return DownloadOutcome(claimed["attempt_id"], claimed["candidate_key"], "technical_failed", str(media_path))

    def _publish_new(
        self, claimed, media_path: Path, sha256: str, byte_count: int
    ) -> DownloadOutcome:
        target = _published_path(
            self.config.output_root,
            claimed["campaign_id"],
            claimed["candidate_key"],
            sha256,
            media_path.suffix,
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO media_objects(
                    sha256, candidate_key, publish_status, final_path,
                    bytes, created_at
                ) VALUES (?, ?, 'pending', ?, ?, ?)
                """,
                (sha256, claimed["candidate_key"], str(target), byte_count, utc_now()),
            )
            connection.execute(
                """
                INSERT INTO media_publish_intents(
                    attempt_id, candidate_key, sha256, kind, status,
                    temp_path, target_path, created_at
                ) VALUES (?, ?, ?, 'publish', 'pending', ?, ?, ?)
                """,
                (
                    claimed["attempt_id"],
                    claimed["candidate_key"],
                    sha256,
                    str(media_path),
                    str(target),
                    utc_now(),
                ),
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(media_path, target)
        _fsync_path(target)
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE media_objects SET publish_status = 'published',
                    final_path = ?, published_at = ? WHERE sha256 = ?
                """,
                (str(target), utc_now(), sha256),
            )
            connection.execute(
                "UPDATE media_publish_intents SET status = 'completed', completed_at = ? WHERE attempt_id = ?",
                (utc_now(), claimed["attempt_id"]),
            )
            connection.execute(
                """
                UPDATE download_attempts SET status = 'succeeded', temp_path = ?,
                    final_path = ?, bytes_downloaded = ?, finished_at = ?
                WHERE attempt_id = ?
                """,
                (
                    str(media_path),
                    str(target),
                    byte_count,
                    utc_now(),
                    claimed["attempt_id"],
                ),
            )
            self.database.transition_candidate_in_transaction(
                connection,
                claimed["candidate_key"],
                "downloaded",
                reason="technical checks passed and media published",
                run_id=claimed["run_id"],
            )
            _suspend_cluster(connection, claimed["candidate_key"], claimed["run_id"])
        self._refresh_batch(claimed["batch_id"])
        return DownloadOutcome(claimed["attempt_id"], claimed["candidate_key"], "downloaded", str(target))

    def _publish_duplicate(self, claimed, media_path, sha256, byte_count, existing) -> DownloadOutcome:
        quarantine = (
            self.config.internal_root
            / "quarantine"
            / "duplicate_suppressed"
            / claimed["run_id"]
            / f"{_safe_stem(claimed['candidate_key'])}-{sha256[:12]}{media_path.suffix.lower()}"
        )
        pair = sorted((claimed["candidate_key"], existing["candidate_key"]))
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO duplicate_edges(
                    edge_id, left_candidate_key, right_candidate_key, kind,
                    evidence_version, similarity, evidence_json, created_at
                ) VALUES (?, ?, ?, 'sha256', 'sha256-v1', 1.0, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    pair[0],
                    pair[1],
                    json.dumps({"sha256": sha256}),
                    utc_now(),
                ),
            )
            connection.execute(
                """
                INSERT INTO media_publish_intents(
                    attempt_id, candidate_key, sha256, kind, status,
                    temp_path, target_path, created_at
                ) VALUES (?, ?, ?, 'quarantine', 'pending', ?, ?, ?)
                """,
                (
                    claimed["attempt_id"],
                    claimed["candidate_key"],
                    sha256,
                    str(media_path),
                    str(quarantine),
                    utc_now(),
                ),
            )
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        os.replace(media_path, quarantine)
        _fsync_path(quarantine)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE media_publish_intents SET status = 'completed', completed_at = ? WHERE attempt_id = ?",
                (utc_now(), claimed["attempt_id"]),
            )
            connection.execute(
                """
                UPDATE download_attempts SET status = 'succeeded', temp_path = ?,
                    final_path = ?, bytes_downloaded = ?, finished_at = ?
                WHERE attempt_id = ?
                """,
                (
                    str(media_path),
                    str(quarantine),
                    byte_count,
                    utc_now(),
                    claimed["attempt_id"],
                ),
            )
            self.database.transition_candidate_in_transaction(
                connection,
                claimed["candidate_key"],
                "duplicate_suppressed",
                reason=f"SHA-256 duplicates {existing['candidate_key']}",
                run_id=claimed["run_id"],
            )
            _release_cluster_lease(connection, claimed["candidate_key"])
        self._refresh_batch(claimed["batch_id"])
        return DownloadOutcome(
            claimed["attempt_id"], claimed["candidate_key"], "duplicate_suppressed", str(quarantine)
        )

    def _complete_publish_intent(self, intent, target: Path) -> None:
        with self.database.transaction() as connection:
            candidate = connection.execute(
                "SELECT status, updated_run_id FROM candidates WHERE candidate_key = ?",
                (intent["candidate_key"],),
            ).fetchone()
            candidate_run_id = candidate["updated_run_id"]
            if intent["kind"] == "publish":
                connection.execute(
                    """
                    UPDATE media_objects SET publish_status = 'published',
                        final_path = ?, published_at = ? WHERE sha256 = ?
                    """,
                    (str(target), utc_now(), intent["sha256"]),
                )
                new_status = "downloaded"
                _suspend_cluster(connection, intent["candidate_key"], candidate_run_id)
            else:
                new_status = "duplicate_suppressed"
                _release_cluster_lease(connection, intent["candidate_key"])
            connection.execute(
                "UPDATE media_publish_intents SET status = 'completed', completed_at = ? WHERE attempt_id = ?",
                (utc_now(), intent["attempt_id"]),
            )
            connection.execute(
                "UPDATE download_attempts SET status = 'succeeded', final_path = ?, finished_at = ? WHERE attempt_id = ?",
                (str(target), utc_now(), intent["attempt_id"]),
            )
            if candidate["status"] == "task_queued":
                self.database.transition_candidate_in_transaction(
                    connection,
                    intent["candidate_key"],
                    new_status,
                    reason="recovered pending publish intent",
                    run_id=candidate_run_id,
                )

    def _refresh_batch(self, batch_id: str) -> None:
        with self.database.transaction() as connection:
            active = connection.execute(
                """
                SELECT COUNT(*)
                FROM queue_assignments q JOIN candidates c ON c.candidate_key = q.candidate_key
                WHERE q.batch_id = ? AND c.status = 'task_queued'
                """,
                (batch_id,),
            ).fetchone()[0]
            unassigned = connection.execute(
                """
                SELECT COUNT(*)
                FROM secondary_filter_decisions d
                WHERE d.batch_id = ? AND d.decision = 'download_eligible'
                  AND NOT EXISTS (
                      SELECT 1 FROM queue_assignments q
                      WHERE q.candidate_key = d.candidate_key
                  )
                """,
                (batch_id,),
            ).fetchone()[0]
            if active:
                connection.execute(
                    "UPDATE secondary_batches SET status = 'queued' WHERE batch_id = ?",
                    (batch_id,),
                )
                return
            batch = connection.execute(
                "SELECT campaign_id, run_id, campaign_policy_version FROM secondary_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            policy = connection.execute(
                """
                SELECT subtype_limits_json FROM campaign_policy_versions
                WHERE campaign_id = ? AND policy_version = ?
                """,
                (batch["campaign_id"], batch["campaign_policy_version"]),
            ).fetchone()
            limits = json.loads(policy["subtype_limits_json"])
            if unassigned and _has_unassigned_capacity(
                connection, batch_id, batch["campaign_id"], limits
            ):
                connection.execute(
                    "UPDATE secondary_batches SET status = 'reviewed' WHERE batch_id = ?",
                    (batch_id,),
                )
            else:
                _complete_batch_and_release(connection, batch_id, batch["run_id"])


def _published_path(root: Path, campaign_id: str, candidate_key: str, sha256: str, suffix: str) -> Path:
    extension = suffix.lower() if re.fullmatch(r"\.[a-zA-Z0-9]{1,8}", suffix) else ".mkv"
    return root / campaign_id / f"{_safe_stem(candidate_key)}-{sha256[:12]}{extension}"


def _is_transient_failure(result: DownloadResult) -> bool:
    return (
        not result.success
        and result.error_kind
        in {
            AdapterErrorKind.NETWORK,
            AdapterErrorKind.RATE_LIMITED,
            AdapterErrorKind.TIMEOUT,
        }
    )


def _safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")[:180] or "candidate"


def _fsync_path(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _post_download_resource_failures(
    technical: Mapping,
    *,
    permitted_file_bytes: int,
) -> list[str]:
    failures: list[str] = []
    duration = technical.get("duration_seconds")
    if not isinstance(duration, (int, float)) or not 10 <= float(duration) <= 900:
        failures.append("downloaded duration is outside 10-900 seconds")
    streams = technical.get("video_streams") or []
    heights = [
        stream.get("height")
        for stream in streams
        if isinstance(stream, Mapping) and isinstance(stream.get("height"), int)
    ]
    if heights and max(heights) > 1080:
        failures.append("downloaded video height exceeds 1080p")
    byte_count = technical.get("bytes")
    if not isinstance(byte_count, int) or not 0 < byte_count <= permitted_file_bytes:
        failures.append("downloaded file exceeds the permitted byte budget")
    return failures


def _release_cluster_lease(connection, candidate_key: str) -> None:
    connection.execute(
        """
        UPDATE duplicate_cluster_members SET member_status = 'ready'
        WHERE candidate_key = ? AND member_status = 'leased'
        """,
        (candidate_key,),
    )


def _suspend_cluster(connection, candidate_key: str, run_id: str) -> None:
    clusters = connection.execute(
        "SELECT duplicate_cluster_id FROM duplicate_cluster_members WHERE candidate_key = ?",
        (candidate_key,),
    ).fetchall()
    for row in clusters:
        connection.execute(
            """
            UPDATE duplicate_cluster_members
            SET member_status = 'suspended', run_id = ?
            WHERE duplicate_cluster_id = ?
            """,
            (run_id, row["duplicate_cluster_id"]),
        )


def _has_unassigned_capacity(connection, batch_id: str, campaign_id: str, limits: dict) -> bool:
    rows = connection.execute(
        """
        SELECT d.decided_subtype AS subtype, COUNT(*) AS count
        FROM secondary_filter_decisions d
        WHERE d.batch_id = ? AND d.decision = 'download_eligible'
          AND NOT EXISTS (
              SELECT 1 FROM queue_assignments q
              WHERE q.candidate_key = d.candidate_key
          )
        GROUP BY d.decided_subtype
        """,
        (batch_id,),
    ).fetchall()
    for row in rows:
        limit = int(limits.get(row["subtype"], 0))
        reserved = connection.execute(
            """
            SELECT COUNT(*)
            FROM queue_assignments q JOIN candidates c ON c.candidate_key = q.candidate_key
            WHERE q.campaign_id = ? AND q.subtype = ?
              AND c.status IN ('task_queued', 'downloaded')
            """,
            (campaign_id, row["subtype"]),
        ).fetchone()[0]
        if reserved < limit:
            return True
    return False


def _complete_batch_and_release(connection, batch_id: str, run_id: str) -> None:
    leftovers = connection.execute(
        """
        SELECT i.candidate_key, i.campaign_id, i.subtype
        FROM secondary_batch_items i
        WHERE i.batch_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM queue_assignments q
              WHERE q.candidate_key = i.candidate_key
          )
        """,
        (batch_id,),
    ).fetchall()
    for row in leftovers:
        connection.execute(
            """
            UPDATE frontier_entries
            SET status = 'suspended', lease_id = NULL,
                lease_expires_at = NULL, updated_at = ?
            WHERE candidate_key = ? AND campaign_id = ?
              AND subtype = ? AND run_id = ? AND status = 'leased'
            """,
            (utc_now(), row["candidate_key"], row["campaign_id"], row["subtype"], run_id),
        )
        _release_cluster_lease(connection, row["candidate_key"])
    connection.execute(
        "UPDATE secondary_batches SET status = 'completed', completed_at = ? WHERE batch_id = ?",
        (utc_now(), batch_id),
    )
