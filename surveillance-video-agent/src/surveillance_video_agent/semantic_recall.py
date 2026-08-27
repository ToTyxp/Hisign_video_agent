"""Calibration-only semantic recall over source/resource-qualified metadata."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.embedding import (
    EmbeddingSchema,
    build_candidate_embedding_input,
    validate_provider,
)
from surveillance_video_agent.qwen_embedding import EmbeddingProviderError
from surveillance_video_agent.scoring import CandidateMetadata
from surveillance_video_agent.vector_index import (
    QdrantVectorIndex,
    calibration_point_id,
)


@dataclass(frozen=True, slots=True)
class CalibrationIndexResult:
    eligible_count: int
    generated_count: int
    cached_count: int
    api_call_count: int


@dataclass(frozen=True, slots=True)
class SemanticRecallExportResult:
    export_id: str
    output_path: Path
    content_sha256: str
    record_count: int
    subtype_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class SemanticThresholdOverrideResult:
    policy_version: str
    threshold: float
    pair_count: int
    unique_candidate_count: int
    promoted_relevance_count: int
    campaign_subtype_counts: Mapping[str, Mapping[str, int]]


class CalibrationSemanticRecallService:
    def __init__(
        self,
        database: CandidateDatabase,
        index: QdrantVectorIndex,
        provider,
        schema: EmbeddingSchema,
    ) -> None:
        validate_provider(schema, provider)
        self.database = database
        self.index = index
        self.provider = provider
        self.schema = schema
        self.database.register_embedding_schema(schema)

    def prepare_index(self, *, run_id: str) -> CalibrationIndexResult:
        self._validate_run(run_id)
        rows = self.database.connection.execute(
            """
            SELECT * FROM candidates
            WHERE status = 'source_qualified' AND resource_eligible = 1
              AND NOT EXISTS (
                  SELECT 1 FROM candidate_suppressions s
                  WHERE s.candidate_key = candidates.candidate_key
                    AND s.suppression_kind = 'source_hard_exclusion'
                    AND NOT EXISTS (
                        SELECT 1 FROM candidate_suppression_releases sr
                        WHERE sr.suppression_id = s.suppression_id
                    )
              )
            ORDER BY candidate_key
            """
        ).fetchall()
        stale = []
        cached = 0
        for row in rows:
            metadata = _metadata(row)
            embedding_input = build_candidate_embedding_input(metadata, self.schema)
            existing = self.database.connection.execute(
                """
                SELECT * FROM calibration_candidate_embeddings
                WHERE candidate_key = ? AND embedding_schema_version = ?
                """,
                (row["candidate_key"], self.schema.version),
            ).fetchone()
            if (
                existing is not None
                and existing["index_status"] == "ready"
                and existing["input_hash"] == embedding_input.input_hash
                and self.index.has_calibration_candidate(
                    self.schema, candidate_key=row["candidate_key"]
                )
            ):
                cached += 1
                continue
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO calibration_candidate_embeddings(
                        candidate_key, embedding_schema_version, input_hash,
                        qdrant_point_id, index_status, indexed_at,
                        error_kind, updated_run_id
                    ) VALUES (?, ?, ?, ?, 'pending', NULL, NULL, ?)
                    ON CONFLICT(candidate_key, embedding_schema_version)
                    DO UPDATE SET input_hash = excluded.input_hash,
                                  qdrant_point_id = excluded.qdrant_point_id,
                                  index_status = 'pending', indexed_at = NULL,
                                  error_kind = NULL,
                                  updated_run_id = excluded.updated_run_id
                    """,
                    (
                        row["candidate_key"],
                        self.schema.version,
                        embedding_input.input_hash,
                        calibration_point_id(
                            self.schema.version, row["candidate_key"]
                        ),
                        run_id,
                    ),
                )
            stale.append((row, embedding_input))
        call_count = 0
        for start in range(0, len(stale), 20):
            batch = stale[start : start + 20]
            call_count += 1
            self._embed_batch(batch, run_id=run_id)
        return CalibrationIndexResult(
            eligible_count=len(rows),
            generated_count=len(stale),
            cached_count=cached,
            api_call_count=call_count,
        )

    def export_recall(
        self,
        *,
        run_id: str,
        campaign_id: str,
        query_pack_version: str,
        query_vectors: Mapping[str, Sequence[float]],
        scoring_policy_version: str,
        output_path: Path,
        top_n_per_subtype: int = 50,
    ) -> SemanticRecallExportResult:
        self._validate_run(run_id)
        if top_n_per_subtype <= 0:
            raise ValueError("top_n_per_subtype must be positive")
        candidates = self.database.connection.execute(
            """
            SELECT c.*
            FROM candidates c
            JOIN calibration_candidate_embeddings e
              ON e.candidate_key = c.candidate_key
             AND e.embedding_schema_version = ?
             AND e.index_status = 'ready'
            WHERE c.status = 'source_qualified' AND c.resource_eligible = 1
              AND NOT EXISTS (
                  SELECT 1 FROM candidate_suppressions s
                  WHERE s.candidate_key = c.candidate_key
                    AND s.suppression_kind = 'source_hard_exclusion'
                    AND NOT EXISTS (
                        SELECT 1 FROM candidate_suppression_releases sr
                        WHERE sr.suppression_id = s.suppression_id
                    )
              )
            ORDER BY c.candidate_key
            """,
            (self.schema.version,),
        ).fetchall()
        by_key = {row["candidate_key"]: row for row in candidates}
        keys = tuple(by_key)
        documents = []
        subtype_counts: dict[str, int] = {}
        for subtype, query_vector in query_vectors.items():
            allowed_keys = [
                key
                for key in keys
                if not self._forbidden(
                    key,
                    campaign_id,
                    subtype,
                    scoring_policy_version,
                )
            ]
            matches = self.index.query_calibration_relevance(
                self.schema,
                query_vector,
                candidate_keys=allowed_keys,
                limit=top_n_per_subtype,
            )
            for rank, match in enumerate(matches, 1):
                row = by_key[match.candidate_key]
                attribution = self._attribution(
                    match.candidate_key,
                    campaign_id,
                    query_pack_version,
                )
                task = self.database.connection.execute(
                    """
                    SELECT score, qualified FROM candidate_task_scores
                    WHERE candidate_key = ? AND campaign_id = ? AND subtype = ?
                    """,
                    (match.candidate_key, campaign_id, subtype),
                ).fetchone()
                documents.append(
                    {
                        "candidate_key": match.candidate_key,
                        "campaign_id": campaign_id,
                        "subtype": subtype,
                        "semantic_rank": rank,
                        "similarity": match.score,
                        "platform": row["platform"],
                        "lang": attribution["lang"] if attribution else None,
                        "query_id": attribution["query_id"] if attribution else None,
                        "query_pack_version": query_pack_version,
                        "title": row["title"],
                        "video_description": row["video_description"],
                        "tags": json.loads(row["tags_json"]),
                        "uploader_identity": row["uploader_id"]
                        or row["uploader"]
                        or row["channel"]
                        or match.candidate_key,
                        "source_score": row["source_score"],
                        "lexical_task_score": task["score"] if task else 0,
                        "lexical_task_qualified": bool(task["qualified"])
                        if task
                        else False,
                        "duration_seconds": row["duration_seconds"],
                        "embedding_schema_version": self.schema.version,
                        "calibration_only": True,
                        "usable": None,
                        "label_notes": None,
                    }
                )
            subtype_counts[subtype] = len(matches)
        documents.sort(
            key=lambda item: (
                item["subtype"],
                item["semantic_rank"],
                item["candidate_key"],
            )
        )
        return self._write_export(
            documents,
            subtype_counts,
            run_id=run_id,
            campaign_id=campaign_id,
            query_pack_version=query_pack_version,
            output_path=output_path,
        )

    def apply_threshold_override(
        self,
        *,
        run_id: str,
        campaign_query_vectors: Mapping[
            str, tuple[str, Mapping[str, Sequence[float]]]
        ],
        scoring_policy_version: str,
        threshold: float = 0.40,
        policy_version: str = "user-semantic-threshold-0.40-v1.0.0",
        required_discovery_query_pack_versions: Mapping[
            str, str | Sequence[str]
        ] | None = None,
        required_source_policy_version: str | None = None,
    ) -> SemanticThresholdOverrideResult:
        self._validate_run(run_id)
        if not 0 <= threshold <= 1:
            raise ValueError("semantic override threshold must be between 0 and 1")
        candidates = self.database.connection.execute(
            """
            SELECT c.candidate_key
            FROM candidates c
            JOIN calibration_candidate_embeddings e
              ON e.candidate_key = c.candidate_key
             AND e.embedding_schema_version = ?
             AND e.index_status = 'ready'
            WHERE c.status = 'source_qualified' AND c.resource_eligible = 1
              AND (? IS NULL OR c.source_policy_version = ?)
              AND NOT EXISTS (
                  SELECT 1 FROM candidate_suppressions s
                  WHERE s.candidate_key = c.candidate_key
                    AND s.suppression_kind = 'source_hard_exclusion'
                    AND NOT EXISTS (
                        SELECT 1 FROM candidate_suppression_releases sr
                        WHERE sr.suppression_id = s.suppression_id
                    )
              )
            ORDER BY c.candidate_key
            """,
            (
                self.schema.version,
                required_source_policy_version,
                required_source_policy_version,
            ),
        ).fetchall()
        keys = [row["candidate_key"] for row in candidates]
        selected_candidates: set[str] = set()
        pair_count = 0
        counts: dict[str, dict[str, int]] = {}
        now = utc_now()
        with self.database.transaction() as connection:
            for campaign_id, (query_pack_version, query_vectors) in (
                campaign_query_vectors.items()
            ):
                for subtype, query_vector in query_vectors.items():
                    allowed = [
                        key
                        for key in keys
                        if self._matches_required_discovery(
                            key,
                            campaign_id,
                            (required_discovery_query_pack_versions or {}).get(
                                campaign_id
                            ),
                        )
                        if not self._forbidden(
                            key,
                            campaign_id,
                            subtype,
                            scoring_policy_version,
                        )
                    ]
                    matches = self.index.query_calibration_relevance(
                        self.schema,
                        query_vector,
                        candidate_keys=allowed,
                        limit=len(allowed),
                    )
                    qualifying = [item for item in matches if item.score > threshold]
                    counts.setdefault(campaign_id, {})[subtype] = len(qualifying)
                    for match in qualifying:
                        eligibility_id = str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"{policy_version}:{self.schema.version}:"
                                f"{query_pack_version}:{campaign_id}:{subtype}:"
                                f"{match.candidate_key}",
                            )
                        )
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO semantic_task_eligibility(
                                eligibility_id, candidate_key, campaign_id,
                                subtype, query_pack_version,
                                embedding_schema_version, policy_version,
                                similarity, threshold, run_id, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                eligibility_id,
                                match.candidate_key,
                                campaign_id,
                                subtype,
                                query_pack_version,
                                self.schema.version,
                                policy_version,
                                match.score,
                                threshold,
                                run_id,
                                now,
                            ),
                        )
                        pair_count += 1
                        selected_candidates.add(match.candidate_key)
        promoted = 0
        for candidate_key in sorted(selected_candidates):
            existing = self.database.connection.execute(
                """
                SELECT 1 FROM candidate_embeddings
                WHERE candidate_key = ? AND embedding_schema_version = ?
                  AND vector_name = 'relevance' AND index_status = 'ready'
                  AND indexed_input_hash = current_input_hash
                """,
                (candidate_key, self.schema.version),
            ).fetchone()
            if existing is not None:
                continue
            calibration = self.database.connection.execute(
                """
                SELECT input_hash FROM calibration_candidate_embeddings
                WHERE candidate_key = ? AND embedding_schema_version = ?
                  AND index_status = 'ready'
                """,
                (candidate_key, self.schema.version),
            ).fetchone()
            vector = self.index.get_calibration_candidate_vector(
                self.schema, candidate_key=candidate_key
            )
            if calibration is None or vector is None:
                raise RuntimeError("calibration relevance vector disappeared")
            point_id = self.index.upsert_candidate_relevance_only(
                self.schema,
                candidate_key=candidate_key,
                relevance_vector=vector,
                payload={
                    "semantic_override": True,
                    "policy_version": policy_version,
                    "input_hash": calibration["input_hash"],
                },
            )
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO candidate_embeddings(
                        candidate_key, embedding_schema_version, vector_name,
                        projection_revision, current_input_hash,
                        indexed_input_hash, qdrant_point_id,
                        index_status, indexed_at
                    ) VALUES (?, ?, 'relevance', 1, ?, ?, ?, 'ready', ?)
                    ON CONFLICT(candidate_key, embedding_schema_version, vector_name)
                    DO UPDATE SET current_input_hash = excluded.current_input_hash,
                                  indexed_input_hash = excluded.indexed_input_hash,
                                  qdrant_point_id = excluded.qdrant_point_id,
                                  index_status = 'ready',
                                  indexed_at = excluded.indexed_at
                    """,
                    (
                        candidate_key,
                        self.schema.version,
                        calibration["input_hash"],
                        calibration["input_hash"],
                        point_id,
                        utc_now(),
                    ),
                )
            promoted += 1
        return SemanticThresholdOverrideResult(
            policy_version=policy_version,
            threshold=threshold,
            pair_count=pair_count,
            unique_candidate_count=len(selected_candidates),
            promoted_relevance_count=promoted,
            campaign_subtype_counts={
                campaign: dict(subtypes) for campaign, subtypes in counts.items()
            },
        )

    def _matches_required_discovery(
        self,
        candidate_key: str,
        campaign_id: str,
        query_pack_version: str | Sequence[str] | None,
    ) -> bool:
        if query_pack_version is None:
            return True
        versions = (
            (query_pack_version,)
            if isinstance(query_pack_version, str)
            else tuple(query_pack_version)
        )
        if not versions:
            return True
        placeholders = ",".join("?" for _ in versions)
        return self.database.connection.execute(
            f"""
            SELECT 1
            FROM candidate_discoveries d
            JOIN queries q ON q.query_id = d.query_id
            WHERE d.candidate_key = ? AND q.campaign_id = ?
              AND q.query_pack_version IN ({placeholders})
            LIMIT 1
            """,
            (candidate_key, campaign_id, *versions),
        ).fetchone() is not None

    def _embed_batch(self, batch, *, run_id: str) -> None:
        call_id = str(uuid.uuid4())
        hashes = [embedding_input.input_hash for _, embedding_input in batch]
        started_at = utc_now()
        try:
            vectors = self.provider.embed_documents(
                [embedding_input.relevance_text for _, embedding_input in batch]
            )
            if len(vectors) != len(batch) or any(
                len(vector) != self.schema.dimensions for vector in vectors
            ):
                raise ValueError("calibration provider returned invalid vectors")
        except Exception as error:
            kind = (
                error.kind.value
                if isinstance(error, EmbeddingProviderError)
                else "provider_error"
            )
            status_code = (
                error.status_code if isinstance(error, EmbeddingProviderError) else None
            )
            self._record_call(
                call_id,
                run_id,
                hashes,
                status="failed",
                error_kind=kind,
                status_code=status_code,
                started_at=started_at,
            )
            with self.database.transaction() as connection:
                for row, _ in batch:
                    connection.execute(
                        """
                        UPDATE calibration_candidate_embeddings
                        SET index_status = 'failed', indexed_at = NULL,
                            error_kind = ?, updated_run_id = ?
                        WHERE candidate_key = ? AND embedding_schema_version = ?
                        """,
                        (kind, run_id, row["candidate_key"], self.schema.version),
                    )
            raise
        self._record_call(
            call_id,
            run_id,
            hashes,
            status="succeeded",
            error_kind=None,
            status_code=None,
            started_at=started_at,
        )
        for (row, embedding_input), vector in zip(batch, vectors, strict=True):
            self.index.upsert_calibration_candidate(
                self.schema,
                candidate_key=row["candidate_key"],
                relevance_vector=vector,
                payload={
                    "input_hash": embedding_input.input_hash,
                    "platform": row["platform"],
                    "calibration_only": True,
                },
            )
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE calibration_candidate_embeddings
                    SET index_status = 'ready', indexed_at = ?,
                        error_kind = NULL, updated_run_id = ?
                    WHERE candidate_key = ? AND embedding_schema_version = ?
                    """,
                    (utc_now(), run_id, row["candidate_key"], self.schema.version),
                )

    def _record_call(
        self,
        call_id: str,
        run_id: str,
        hashes,
        *,
        status: str,
        error_kind: str | None,
        status_code: int | None,
        started_at: str,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO calibration_embedding_calls(
                    call_id, run_id, embedding_schema_version,
                    provider, model, subject_count, input_hashes_json,
                    status, error_kind, status_code, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    run_id,
                    self.schema.version,
                    self.schema.provider,
                    self.schema.model,
                    len(hashes),
                    json.dumps(hashes, separators=(",", ":")),
                    status,
                    error_kind,
                    status_code,
                    started_at,
                    utc_now(),
                ),
            )

    def _forbidden(self, candidate_key, campaign_id, subtype, policy_version) -> bool:
        return self.database.connection.execute(
            """
            SELECT 1 FROM score_evidence
            WHERE candidate_key = ? AND campaign_id = ? AND subtype = ?
              AND rule_code = 'task.forbidden_semantics'
              AND policy_version = ? LIMIT 1
            """,
            (candidate_key, campaign_id, subtype, policy_version),
        ).fetchone() is not None

    def _attribution(self, candidate_key, campaign_id, query_pack_version):
        row = self.database.connection.execute(
            """
            SELECT q.query_id, q.lang
            FROM candidate_discoveries d JOIN queries q ON q.query_id = d.query_id
            WHERE d.candidate_key = ? AND q.campaign_id = ?
              AND q.query_pack_version = ?
            ORDER BY d.platform_position, d.discovered_at, q.query_id LIMIT 1
            """,
            (candidate_key, campaign_id, query_pack_version),
        ).fetchone()
        if row is not None:
            return row
        return self.database.connection.execute(
            """
            SELECT q.query_id, q.lang
            FROM candidate_discoveries d JOIN queries q ON q.query_id = d.query_id
            WHERE d.candidate_key = ? AND q.campaign_id = ?
            ORDER BY d.platform_position, d.discovered_at, q.query_id LIMIT 1
            """,
            (candidate_key, campaign_id),
        ).fetchone()

    def _write_export(
        self,
        documents,
        subtype_counts,
        *,
        run_id,
        campaign_id,
        query_pack_version,
        output_path,
    ) -> SemanticRecallExportResult:
        destination = Path(output_path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        hasher = hashlib.sha256()
        with temporary.open("w", encoding="utf-8") as handle:
            for document in documents:
                line = json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ) + "\n"
                handle.write(line)
                hasher.update(line.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        digest = hasher.hexdigest()
        export_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{run_id}:{campaign_id}:{query_pack_version}:"
                f"{self.schema.version}:{destination}:{digest}",
            )
        )
        subtype_json = json.dumps(
            subtype_counts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO semantic_recall_exports(
                    export_id, run_id, campaign_id, query_pack_version,
                    embedding_schema_version, output_path, content_sha256,
                    record_count, subtype_counts_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_id,
                    run_id,
                    campaign_id,
                    query_pack_version,
                    self.schema.version,
                    str(destination),
                    digest,
                    len(documents),
                    subtype_json,
                    utc_now(),
                ),
            )
        return SemanticRecallExportResult(
            export_id=export_id,
            output_path=destination,
            content_sha256=digest,
            record_count=len(documents),
            subtype_counts=dict(subtype_counts),
        )

    def _validate_run(self, run_id: str) -> None:
        row = self.database.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or row["status"] != "running":
            raise ValueError("an existing running run_id is required")


def _metadata(row) -> CandidateMetadata:
    return CandidateMetadata(
        candidate_key=row["candidate_key"],
        title=row["title"] or "",
        video_description=row["video_description"] or "",
        tags=tuple(json.loads(row["tags_json"])),
        uploader=row["uploader"] or "",
        channel=row["channel"] or "",
        playlist=row["playlist"] or "",
    )
