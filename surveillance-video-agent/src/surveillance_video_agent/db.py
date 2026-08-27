"""SQLite control-plane foundation with transactional audit writes."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Sequence

from surveillance_video_agent.contracts import ProbeResult, SearchHit
from surveillance_video_agent.scoring.models import SourceScoreResult, TaskScoreResult
from surveillance_video_agent.resources import ResourceEvaluation

if TYPE_CHECKING:
    from surveillance_video_agent.embedding import EmbeddingSchema


SCHEMA_VERSION = 2
_LEGAL_TRANSITIONS = {
    ("discovered", "source_qualified"),
    ("source_qualified", "task_queued"),
    ("task_queued", "downloaded"),
    ("task_queued", "technical_failed"),
    ("task_queued", "duplicate_suppressed"),
}


class CandidateDatabase:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            timeout=5.0,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CandidateDatabase":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def initialize(self) -> None:
        migration = files("surveillance_video_agent.migrations").joinpath(
            "0001_initial.sql"
        )
        self.connection.executescript(migration.read_text(encoding="utf-8"))
        candidate_sql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'candidates'"
        ).fetchone()["sql"]
        if "mobile_adjacent" not in candidate_sql:
            mobile_migration = files("surveillance_video_agent.migrations").joinpath(
                "0002_mobile_camera_pool.sql"
            )
            self.connection.executescript(
                mobile_migration.read_text(encoding="utf-8")
            )
            # Recreate indexes and triggers dropped with the v1 candidates table.
            self.connection.executescript(migration.read_text(encoding="utf-8"))
        self.connection.execute("PRAGMA journal_mode = WAL").fetchone()
        version = self.connection.execute(
            "SELECT MAX(schema_version) AS version FROM schema_meta"
        ).fetchone()["version"]
        if version != SCHEMA_VERSION:
            raise RuntimeError(f"unexpected schema version: {version}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def create_run(
        self,
        run_id: str,
        run_type: str,
        *,
        config: Mapping[str, Any] | None = None,
        code_version: str | None = None,
        started_at: str | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, run_type, status, config_json, code_version, started_at
                ) VALUES (?, ?, 'running', ?, ?, ?)
                """,
                (
                    run_id,
                    run_type,
                    _json(config or {}),
                    code_version,
                    started_at or utc_now(),
                ),
            )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        if status not in {"completed", "failed", "stopped"}:
            raise ValueError("run finish status is invalid")
        with self.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE runs SET status = ?, finished_at = ?, result_json = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (status, utc_now(), _json(result or {}), run_id),
            ).rowcount
        if changed != 1:
            raise ValueError("run is not in running state")

    def register_frozen_query_pack(self, path: Path) -> str:
        document = _read_json(Path(path))
        if document.get("status") != "frozen":
            raise ValueError("query pack must be frozen before registration")
        version = _required_text(document, "query_pack_version")
        campaign_id = _required_text(document, "campaign_id")
        queries = document.get("queries")
        if not isinstance(queries, list) or not queries:
            raise ValueError("query pack must contain queries")
        canonical_queries = json.dumps(
            queries,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        actual_hash = hashlib.sha256(canonical_queries.encode("utf-8")).hexdigest()
        if actual_hash != _required_text(document, "content_sha256"):
            raise ValueError("frozen query-pack content hash does not match")
        values = (
            version,
            campaign_id,
            _required_text(document, "concept_pack_version"),
            _required_text(document, "source_sha256"),
            _required_text(document, "content_sha256"),
            _required_text(document, "network_config"),
            _required_text(document, "status"),
            _required_text(document, "frozen_at"),
            _required_text(document, "frozen_by"),
            _json(document),
        )
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT content_sha256 FROM query_packs WHERE query_pack_version = ?",
                (version,),
            ).fetchone()
            if existing is not None:
                if existing["content_sha256"] != document["content_sha256"]:
                    raise ValueError("registered query-pack version has different content")
                return version
            connection.execute(
                """
                INSERT INTO query_packs(
                    query_pack_version, campaign_id, concept_pack_version,
                    source_sha256, content_sha256, network_config, status,
                    frozen_at, frozen_by, content_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            for item in queries:
                if not isinstance(item, dict) or item.get("campaign_id") != campaign_id:
                    raise ValueError("query campaign identity is invalid")
                connection.execute(
                    """
                    INSERT INTO queries(
                        query_id, query_pack_version, campaign_id, subtype, lang,
                        query_text, source_anchor, action_or_scene_term
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _required_text(item, "query_id"),
                        version,
                        campaign_id,
                        _required_text(item, "subtype"),
                        _required_text(item, "lang"),
                        _required_text(item, "query"),
                        _required_text(item, "source_anchor"),
                        _required_text(item, "action_or_scene_term"),
                    ),
                )
        return version

    def register_embedding_schema(self, schema: "EmbeddingSchema") -> None:
        expected = (
            schema.provider,
            schema.model,
            schema.dimensions,
            schema.distance,
            schema.text_template_version,
            schema.normalization_version,
        )
        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM embedding_schema_versions
                WHERE embedding_schema_version = ?
                """,
                (schema.version,),
            ).fetchone()
            if existing is not None:
                actual = tuple(
                    existing[key]
                    for key in (
                        "provider",
                        "model",
                        "dimensions",
                        "distance",
                        "text_template_version",
                        "normalization_version",
                    )
                )
                if actual != expected:
                    raise ValueError(
                        "embedding schema version already has different settings"
                    )
                return
            connection.execute(
                """
                INSERT INTO embedding_schema_versions(
                    embedding_schema_version, provider, model, dimensions,
                    distance, text_template_version, normalization_version,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (schema.version, *expected, utc_now()),
            )

    def insert_candidate(
        self,
        probe: ProbeResult,
        *,
        run_id: str,
        seen_at: str | None = None,
    ) -> None:
        timestamp = seen_at or utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO candidates(
                    candidate_key, platform, source_id, source_url, title,
                    video_description, tags_json, uploader, uploader_id, channel,
                    playlist, duration_seconds, estimated_bytes, width, height,
                    availability, is_live, live_status,
                    first_seen_at, last_seen_at, created_run_id, updated_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_key) DO UPDATE SET
                    source_url = excluded.source_url,
                    title = excluded.title,
                    video_description = excluded.video_description,
                    tags_json = excluded.tags_json,
                    uploader = excluded.uploader,
                    uploader_id = excluded.uploader_id,
                    channel = excluded.channel,
                    playlist = excluded.playlist,
                    duration_seconds = excluded.duration_seconds,
                    estimated_bytes = excluded.estimated_bytes,
                    width = excluded.width,
                    height = excluded.height,
                    availability = excluded.availability,
                    is_live = excluded.is_live,
                    live_status = excluded.live_status,
                    last_seen_at = excluded.last_seen_at,
                    updated_run_id = excluded.updated_run_id
                """,
                (
                    probe.candidate_key,
                    probe.platform,
                    probe.source_id,
                    probe.canonical_url,
                    probe.title,
                    probe.video_description,
                    _json(list(probe.tags)),
                    probe.uploader,
                    probe.uploader_id,
                    probe.channel,
                    probe.playlist,
                    probe.duration_seconds,
                    probe.filesize_approx,
                    probe.width,
                    probe.height,
                    probe.availability,
                    int(probe.is_live) if probe.is_live is not None else None,
                    probe.live_status,
                    timestamp,
                    timestamp,
                    run_id,
                    run_id,
                ),
            )

    def insert_search_hit(
        self,
        hit: SearchHit,
        *,
        run_id: str,
        seen_at: str | None = None,
    ) -> None:
        """Persist cheap discovery metadata without erasing later probe data."""

        timestamp = seen_at or utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO candidates(
                    candidate_key, platform, source_id, source_url, title,
                    uploader, duration_seconds, first_seen_at, last_seen_at,
                    created_run_id, updated_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_key) DO UPDATE SET
                    source_url = excluded.source_url,
                    title = CASE
                        WHEN candidates.source_policy_version IS NULL
                        THEN COALESCE(excluded.title, candidates.title)
                        ELSE candidates.title
                    END,
                    uploader = CASE
                        WHEN candidates.source_policy_version IS NULL
                        THEN COALESCE(excluded.uploader, candidates.uploader)
                        ELSE candidates.uploader
                    END,
                    duration_seconds = CASE
                        WHEN candidates.source_policy_version IS NULL
                        THEN COALESCE(excluded.duration_seconds, candidates.duration_seconds)
                        ELSE candidates.duration_seconds
                    END,
                    last_seen_at = excluded.last_seen_at,
                    updated_run_id = excluded.updated_run_id
                """,
                (
                    hit.candidate_key,
                    hit.platform,
                    hit.source_id,
                    hit.source_url,
                    hit.title,
                    hit.uploader,
                    hit.duration_seconds,
                    timestamp,
                    timestamp,
                    run_id,
                    run_id,
                ),
            )

    def record_discovery(
        self,
        hit: SearchHit,
        *,
        query_id: str,
        run_id: str,
        discovered_at: str | None = None,
    ) -> None:
        timestamp = discovered_at or utc_now()
        discovery_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{run_id}:{query_id}:{hit.candidate_key}:{hit.position}",
            )
        )
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO candidate_discoveries(
                    discovery_id, candidate_key, query_id, platform_position,
                    discovered_at, run_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    discovery_id,
                    hit.candidate_key,
                    query_id,
                    hit.position,
                    timestamp,
                    run_id,
                ),
            )

    def record_source_score(
        self,
        result: SourceScoreResult,
        *,
        run_id: str,
        calculated_at: str | None = None,
    ) -> None:
        timestamp = calculated_at or utc_now()
        with self.transaction() as connection:
            candidate = _candidate_row(connection, result.candidate_key)
            connection.execute(
                """
                UPDATE candidates
                SET source_score = ?, source_policy_version = ?, hard_excluded = ?,
                    hard_exclusion_reasons_json = ?, camera_pool = ?,
                    updated_run_id = ?
                WHERE candidate_key = ?
                """,
                (
                    result.score,
                    result.policy_version,
                    int(result.hard_excluded),
                    _json(
                        [
                            {
                                "category": item.category,
                                "fields": item.matched_fields,
                                "terms": item.matched_terms,
                                "reason": item.reason,
                            }
                            for item in result.hard_exclusions
                        ]
                    ),
                    result.camera_pool,
                    run_id,
                    result.candidate_key,
                ),
            )
            for item in result.hard_exclusions:
                _insert_score_evidence(
                    connection,
                    result.candidate_key,
                    "hard_exclusion",
                    None,
                    None,
                    f"hard.{item.category}",
                    0,
                    item.matched_fields,
                    item.matched_terms,
                    item.reason,
                    result.policy_version,
                    run_id,
                    timestamp,
                )
            for item in result.evidence:
                _insert_score_evidence(
                    connection,
                    result.candidate_key,
                    "source",
                    None,
                    None,
                    item.rule_code,
                    item.points,
                    item.matched_fields,
                    item.matched_terms,
                    item.reason,
                    result.policy_version,
                    run_id,
                    timestamp,
                )
            if result.qualified and candidate["status"] == "discovered":
                _transition(
                    connection,
                    result.candidate_key,
                    "source_qualified",
                    "source score passed threshold",
                    run_id,
                    timestamp,
                )

    def record_task_score(
        self,
        result: TaskScoreResult,
        *,
        run_id: str,
        calculated_at: str | None = None,
    ) -> None:
        timestamp = calculated_at or utc_now()
        with self.transaction() as connection:
            candidate = _candidate_row(connection, result.candidate_key)
            if not result.blocked_by_source_gate:
                if candidate["status"] == "discovered":
                    raise ValueError("task score cannot precede the source-qualified state")
                if candidate["source_policy_version"] != result.policy_version:
                    raise ValueError("task score policy does not match stored source policy")
            connection.execute(
                """
                INSERT INTO candidate_task_scores(
                    candidate_key, campaign_id, subtype, score, qualified,
                    blocked_by_source_gate, policy_version, run_id, calculated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_key, campaign_id, subtype) DO UPDATE SET
                    score = excluded.score,
                    qualified = excluded.qualified,
                    blocked_by_source_gate = excluded.blocked_by_source_gate,
                    policy_version = excluded.policy_version,
                    run_id = excluded.run_id,
                    calculated_at = excluded.calculated_at
                """,
                (
                    result.candidate_key,
                    result.campaign_id,
                    result.subtype,
                    result.score,
                    int(result.qualified),
                    int(result.blocked_by_source_gate),
                    result.policy_version,
                    run_id,
                    timestamp,
                ),
            )
            for item in result.evidence:
                _insert_score_evidence(
                    connection,
                    result.candidate_key,
                    "task",
                    result.campaign_id,
                    result.subtype,
                    item.rule_code,
                    item.points,
                    item.matched_fields,
                    item.matched_terms,
                    item.reason,
                    result.policy_version,
                    run_id,
                    timestamp,
                )

    def record_qualification(
        self,
        source_result: SourceScoreResult,
        task_results: Sequence[TaskScoreResult],
        *,
        run_id: str,
        calculated_at: str | None = None,
    ) -> None:
        """Atomically persist a complete source-first qualification decision."""

        timestamp = calculated_at or utc_now()
        tasks = tuple(task_results)
        if not source_result.qualified and tasks:
            raise ValueError("source-ineligible candidates cannot receive task scores")
        for result in tasks:
            if result.candidate_key != source_result.candidate_key:
                raise ValueError("qualification results belong to different candidates")
            if result.policy_version != source_result.policy_version:
                raise ValueError("qualification results use different policies")
            if result.blocked_by_source_gate:
                raise ValueError("qualified source cannot have blocked task scores")
        with self.transaction() as connection:
            candidate = _candidate_row(connection, source_result.candidate_key)
            if candidate["status"] not in {"discovered", "source_qualified"}:
                raise ValueError(
                    "qualification requires a discovered or source-qualified candidate"
                )
            if candidate["status"] == "source_qualified" and not source_result.qualified:
                raise ValueError("advanced candidate cannot become source-ineligible")
            connection.execute(
                """
                UPDATE candidates
                SET source_score = ?, source_policy_version = ?, hard_excluded = ?,
                    hard_exclusion_reasons_json = ?, camera_pool = ?,
                    updated_run_id = ?
                WHERE candidate_key = ?
                """,
                (
                    source_result.score,
                    source_result.policy_version,
                    int(source_result.hard_excluded),
                    _json(
                        [
                            {
                                "category": item.category,
                                "fields": item.matched_fields,
                                "terms": item.matched_terms,
                                "reason": item.reason,
                            }
                            for item in source_result.hard_exclusions
                        ]
                    ),
                    source_result.camera_pool,
                    run_id,
                    source_result.candidate_key,
                ),
            )
            for item in source_result.hard_exclusions:
                _insert_score_evidence(
                    connection,
                    source_result.candidate_key,
                    "hard_exclusion",
                    None,
                    None,
                    f"hard.{item.category}",
                    0,
                    item.matched_fields,
                    item.matched_terms,
                    item.reason,
                    source_result.policy_version,
                    run_id,
                    timestamp,
                )
            for item in source_result.evidence:
                _insert_score_evidence(
                    connection,
                    source_result.candidate_key,
                    "source",
                    None,
                    None,
                    item.rule_code,
                    item.points,
                    item.matched_fields,
                    item.matched_terms,
                    item.reason,
                    source_result.policy_version,
                    run_id,
                    timestamp,
                )
            if source_result.qualified and candidate["status"] == "discovered":
                _transition(
                    connection,
                    source_result.candidate_key,
                    "source_qualified",
                    "source score passed threshold",
                    run_id,
                    timestamp,
                )
            for result in tasks:
                connection.execute(
                    """
                    INSERT INTO candidate_task_scores(
                        candidate_key, campaign_id, subtype, score, qualified,
                        blocked_by_source_gate, policy_version, run_id, calculated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(candidate_key, campaign_id, subtype) DO UPDATE SET
                        score = excluded.score,
                        qualified = excluded.qualified,
                        blocked_by_source_gate = excluded.blocked_by_source_gate,
                        policy_version = excluded.policy_version,
                        run_id = excluded.run_id,
                        calculated_at = excluded.calculated_at
                    """,
                    (
                        result.candidate_key,
                        result.campaign_id,
                        result.subtype,
                        result.score,
                        int(result.qualified),
                        int(result.blocked_by_source_gate),
                        result.policy_version,
                        run_id,
                        timestamp,
                    ),
                )
                for item in result.evidence:
                    _insert_score_evidence(
                        connection,
                        result.candidate_key,
                        "task",
                        result.campaign_id,
                        result.subtype,
                        item.rule_code,
                        item.points,
                        item.matched_fields,
                        item.matched_terms,
                        item.reason,
                        result.policy_version,
                        run_id,
                        timestamp,
                    )

    def record_resource_evaluation(
        self,
        result: ResourceEvaluation,
        *,
        run_id: str,
    ) -> None:
        with self.transaction() as connection:
            _candidate_row(connection, result.candidate_key)
            connection.execute(
                """
                UPDATE candidates
                SET resource_eligible = ?, resource_reasons_json = ?,
                    resource_policy_version = ?, updated_run_id = ?
                WHERE candidate_key = ?
                """,
                (
                    int(result.eligible),
                    _json(
                        [
                            {"code": reason.code, "reason": reason.reason}
                            for reason in result.reasons
                        ]
                    ),
                    result.policy_version,
                    run_id,
                    result.candidate_key,
                ),
            )

    def record_candidate_suppression(
        self,
        candidate_key: str,
        *,
        suppression_kind: str,
        policy_version: str,
        reasons: Sequence[Mapping[str, Any]],
        run_id: str,
    ) -> None:
        if suppression_kind != "source_hard_exclusion":
            raise ValueError("unsupported candidate suppression kind")
        with self.transaction() as connection:
            _candidate_row(connection, candidate_key)
            connection.execute(
                """
                INSERT OR IGNORE INTO candidate_suppressions(
                    suppression_id, candidate_key, suppression_kind,
                    policy_version, reasons_json, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_key,
                    suppression_kind,
                    policy_version,
                    _json(list(reasons)),
                    run_id,
                    utc_now(),
                ),
            )

    def transition_candidate(
        self,
        candidate_key: str,
        new_status: str,
        *,
        reason: str,
        run_id: str,
        transitioned_at: str | None = None,
    ) -> None:
        with self.transaction() as connection:
            _transition(
                connection,
                candidate_key,
                new_status,
                reason,
                run_id,
                transitioned_at or utc_now(),
            )

    def transition_candidate_in_transaction(
        self,
        connection: sqlite3.Connection,
        candidate_key: str,
        new_status: str,
        *,
        reason: str,
        run_id: str,
        transitioned_at: str | None = None,
    ) -> None:
        if connection is not self.connection or not self.connection.in_transaction:
            raise ValueError("transition requires this database's active transaction")
        _transition(
            connection,
            candidate_key,
            new_status,
            reason,
            run_id,
            transitioned_at or utc_now(),
        )

    def upsert_search_cache(
        self,
        *,
        platform: str,
        query: str,
        lang: str,
        query_pack_version: str,
        network_config: str,
        payload: Any,
        fetched_at: str,
        expires_at: str,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO search_cache(
                    platform, query, lang, query_pack_version, network_config,
                    fetched_at, expires_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, query, lang, query_pack_version, network_config)
                DO UPDATE SET fetched_at = excluded.fetched_at,
                              expires_at = excluded.expires_at,
                              payload_json = excluded.payload_json
                """,
                (
                    platform,
                    query,
                    lang,
                    query_pack_version,
                    network_config,
                    fetched_at,
                    expires_at,
                    _json(payload),
                ),
            )

    def get_search_cache(
        self,
        *,
        platform: str,
        query: str,
        lang: str,
        query_pack_version: str,
        network_config: str,
        now: str | None = None,
    ) -> Any | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM search_cache
            WHERE platform = ? AND query = ? AND lang = ?
              AND query_pack_version = ? AND network_config = ?
              AND expires_at > ?
            """,
            (
                platform,
                query,
                lang,
                query_pack_version,
                network_config,
                now or utc_now(),
            ),
        ).fetchone()
        return None if row is None else json.loads(row["payload_json"])

    def upsert_probe_cache(
        self,
        probe: ProbeResult,
        *,
        network_config: str,
        fetched_at: str,
        expires_at: str,
    ) -> None:
        normalized = {
            "platform": probe.platform,
            "source_id": probe.source_id,
            "candidate_key": probe.candidate_key,
            "source_url": probe.source_url,
            "canonical_url": probe.canonical_url,
            "title": probe.title,
            "video_description": probe.video_description,
            "tags": list(probe.tags),
            "uploader": probe.uploader,
            "uploader_id": probe.uploader_id,
            "channel": probe.channel,
            "playlist": probe.playlist,
            "duration_seconds": probe.duration_seconds,
            "upload_date": probe.upload_date,
            "availability": probe.availability,
            "filesize_approx": probe.filesize_approx,
            "width": probe.width,
            "height": probe.height,
            "is_live": probe.is_live,
            "live_status": probe.live_status,
        }
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO probe_cache(
                    platform, source_id, network_config, fetched_at, expires_at,
                    normalized_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, source_id, network_config)
                DO UPDATE SET fetched_at = excluded.fetched_at,
                              expires_at = excluded.expires_at,
                              normalized_json = excluded.normalized_json,
                              raw_json = excluded.raw_json
                """,
                (
                    probe.platform,
                    probe.source_id,
                    network_config,
                    fetched_at,
                    expires_at,
                    _json(normalized),
                    _json(probe.raw_metadata),
                ),
            )

    def get_probe_cache(
        self,
        *,
        platform: str,
        source_id: str,
        network_config: str,
        now: str | None = None,
    ) -> ProbeResult | None:
        row = self.connection.execute(
            """
            SELECT normalized_json, raw_json
            FROM probe_cache
            WHERE platform = ? AND source_id = ? AND network_config = ?
              AND expires_at > ?
            """,
            (platform, source_id, network_config, now or utc_now()),
        ).fetchone()
        if row is None:
            return None
        normalized = json.loads(row["normalized_json"])
        normalized["tags"] = tuple(normalized.get("tags") or ())
        normalized["raw_metadata"] = json.loads(row["raw_json"])
        return ProbeResult(**normalized)

    def record_adapter_call(
        self,
        *,
        request_id: str,
        run_id: str,
        platform: str,
        operation: str,
        cache_hit: bool,
        status: str,
        started_at: str,
        finished_at: str,
        query_id: str | None = None,
        candidate_key: str | None = None,
        error_kind: str | None = None,
        error_message: str | None = None,
        attempts: int = 1,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO adapter_calls(
                    request_id, run_id, platform, operation, query_id,
                    candidate_key, cache_hit, status, error_kind, error_message,
                    attempts, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    run_id,
                    platform,
                    operation,
                    query_id,
                    candidate_key,
                    int(cache_hit),
                    status,
                    error_kind,
                    error_message,
                    attempts,
                    started_at,
                    finished_at,
                ),
            )

    def record_embedding_call(
        self,
        *,
        call_id: str,
        run_id: str,
        embedding_schema_version: str,
        provider: str,
        model: str,
        operation: str,
        input_hashes: Sequence[str],
        status: str,
        started_at: str,
        finished_at: str,
        error_kind: str | None = None,
        status_code: int | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO embedding_calls(
                    call_id, run_id, embedding_schema_version, provider, model,
                    operation, subject_count, input_hashes_json, status,
                    error_kind, status_code, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    run_id,
                    embedding_schema_version,
                    provider,
                    model,
                    operation,
                    len(input_hashes),
                    _json(list(input_hashes)),
                    status,
                    error_kind,
                    status_code,
                    started_at,
                    finished_at,
                ),
            )

    def get_candidate(self, candidate_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM candidates WHERE candidate_key = ?", (candidate_key,)
        ).fetchone()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _transition(
    connection: sqlite3.Connection,
    candidate_key: str,
    new_status: str,
    reason: str,
    run_id: str,
    timestamp: str,
) -> None:
    candidate = _candidate_row(connection, candidate_key)
    old_status = candidate["status"]
    if old_status == new_status:
        return
    if (old_status, new_status) not in _LEGAL_TRANSITIONS:
        raise ValueError(f"illegal candidate transition: {old_status} -> {new_status}")
    connection.execute(
        "UPDATE candidates SET status = ?, updated_run_id = ? WHERE candidate_key = ?",
        (new_status, run_id, candidate_key),
    )
    connection.execute(
        """
        INSERT INTO state_transitions(
            transition_id, candidate_key, old_status, new_status,
            reason, run_id, transitioned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            candidate_key,
            old_status,
            new_status,
            reason,
            run_id,
            timestamp,
        ),
    )


def _candidate_row(connection: sqlite3.Connection, candidate_key: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM candidates WHERE candidate_key = ?", (candidate_key,)
    ).fetchone()
    if row is None:
        raise ValueError(f"candidate not found: {candidate_key}")
    return row


def _insert_score_evidence(
    connection: sqlite3.Connection,
    candidate_key: str,
    score_kind: str,
    campaign_id: str | None,
    subtype: str | None,
    rule_code: str,
    points: int,
    fields: tuple[str, ...],
    terms: tuple[str, ...],
    reason: str,
    policy_version: str,
    run_id: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO score_evidence(
            evidence_id, candidate_key, score_kind, campaign_id, subtype,
            rule_code, points, matched_fields_json, matched_terms_json,
            reason, policy_version, run_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            candidate_key,
            score_kind,
            campaign_id,
            subtype,
            rule_code,
            points,
            _json(list(fields)),
            _json(list(terms)),
            reason,
            policy_version,
            run_id,
            created_at,
        ),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {path}") from error
    if not isinstance(document, dict):
        raise ValueError("expected a JSON object")
    return document


def _required_text(document: Mapping[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing text field: {key}")
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
