"""Transactional SQLite outbox to Qdrant projection service."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.embedding import (
    EmbeddingProvider,
    EmbeddingSchema,
    build_candidate_embedding_input,
    validate_provider,
)
from surveillance_video_agent.scoring.models import CandidateMetadata
from surveillance_video_agent.vector_index import QdrantVectorIndex, point_id_for
from surveillance_video_agent.qwen_embedding import EmbeddingProviderError


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    event_id: str
    candidate_key: str
    projection_revision: int
    status: str


class VectorProjectionService:
    def __init__(
        self,
        database: CandidateDatabase,
        index: QdrantVectorIndex,
        provider: EmbeddingProvider,
        schema: EmbeddingSchema,
    ) -> None:
        validate_provider(schema, provider)
        self.database = database
        self.index = index
        self.provider = provider
        self.schema = schema
        self._register_schema()

    def enqueue_candidate(self, candidate_key: str) -> str | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM candidates WHERE candidate_key = ?", (candidate_key,)
            ).fetchone()
            if row is None:
                raise ValueError(f"candidate not found: {candidate_key}")
            if row["status"] != "source_qualified":
                raise ValueError("only source-qualified candidates can be projected")
            if row["resource_eligible"] != 1:
                raise ValueError("candidate must pass the static resource gate")
            qualified_tasks = connection.execute(
                """
                SELECT COUNT(*) FROM candidate_task_scores
                WHERE candidate_key = ? AND qualified = 1 AND score >= 4
                """,
                (candidate_key,),
            ).fetchone()[0]
            if not qualified_tasks:
                raise ValueError("candidate requires at least one qualified task score")
            candidate = _candidate_from_row(row)
            embedding_input = build_candidate_embedding_input(candidate, self.schema)
            current = connection.execute(
                """
                SELECT MAX(projection_revision) AS revision,
                       SUM(CASE
                           WHEN index_status = 'ready'
                            AND indexed_input_hash = current_input_hash
                           THEN 1 ELSE 0 END) AS ready_count,
                       MIN(CASE WHEN index_status = 'ready' THEN current_input_hash END) AS ready_hash,
                       MAX(CASE WHEN index_status = 'ready' THEN current_input_hash END) AS max_ready_hash
                FROM candidate_embeddings
                WHERE candidate_key = ? AND embedding_schema_version = ?
                """,
                (candidate_key, self.schema.version),
            ).fetchone()
            if (
                current["ready_count"] == 2
                and current["ready_hash"] == embedding_input.input_hash
                and current["max_ready_hash"] == embedding_input.input_hash
            ):
                return None
            revision = int(current["revision"] or 0) + 1
            point_id = point_id_for(self.schema.version, candidate_key)
            for vector_name in ("relevance", "duplicate"):
                connection.execute(
                    """
                    INSERT INTO candidate_embeddings(
                        candidate_key, embedding_schema_version, vector_name,
                        projection_revision, current_input_hash, indexed_input_hash,
                        qdrant_point_id, index_status, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?, 'pending', NULL)
                    ON CONFLICT(candidate_key, embedding_schema_version, vector_name)
                    DO UPDATE SET
                        projection_revision = excluded.projection_revision,
                        current_input_hash = excluded.current_input_hash,
                        qdrant_point_id = excluded.qdrant_point_id,
                        index_status = 'pending',
                        indexed_at = NULL
                    """,
                    (
                        candidate_key,
                        self.schema.version,
                        vector_name,
                        revision,
                        embedding_input.input_hash,
                        point_id,
                    ),
                )
            connection.execute(
                """
                UPDATE vector_index_outbox
                SET status = 'superseded', completed_at = ?
                WHERE candidate_key = ? AND embedding_schema_version = ?
                  AND status IN ('pending', 'failed')
                """,
                (utc_now(), candidate_key, self.schema.version),
            )
            event_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO vector_index_outbox(
                    event_id, candidate_key, embedding_schema_version,
                    projection_revision, input_hash, event_kind, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'upsert', 'pending', ?)
                """,
                (
                    event_id,
                    candidate_key,
                    self.schema.version,
                    revision,
                    embedding_input.input_hash,
                    utc_now(),
                ),
            )
            return event_id

    def process_next(self) -> ProjectionResult | None:
        claimed = self._claim_next()
        if claimed is None:
            return None
        event_id = claimed["event_id"]
        candidate_key = claimed["candidate_key"]
        revision = int(claimed["projection_revision"])
        input_hash = claimed["input_hash"]
        try:
            row = self.database.connection.execute(
                "SELECT * FROM candidates WHERE candidate_key = ?", (candidate_key,)
            ).fetchone()
            if row is None:
                raise ValueError(f"candidate not found: {candidate_key}")
            if row["resource_eligible"] != 1:
                raise ValueError("candidate no longer passes the static resource gate")
            candidate = _candidate_from_row(row)
            embedding_input = build_candidate_embedding_input(candidate, self.schema)
            if embedding_input.input_hash != input_hash:
                return self._mark_superseded(event_id, candidate_key, revision)
            embedding_call_id = str(uuid.uuid4())
            embedding_started_at = utc_now()
            try:
                vectors = self.provider.embed(
                    [embedding_input.relevance_text, embedding_input.duplicate_text]
                )
                if len(vectors) != 2 or any(
                    len(vector) != self.schema.dimensions for vector in vectors
                ):
                    raise ValueError(
                        "embedding provider returned an invalid vector shape"
                    )
            except Exception as error:
                self.database.record_embedding_call(
                    call_id=embedding_call_id,
                    run_id=row["updated_run_id"],
                    embedding_schema_version=self.schema.version,
                    provider=self.schema.provider,
                    model=self.schema.model,
                    operation="candidate_documents",
                    input_hashes=[embedding_input.input_hash],
                    status="failed",
                    error_kind=(
                        error.kind.value
                        if isinstance(error, EmbeddingProviderError)
                        else "provider_error"
                    ),
                    status_code=(
                        error.status_code
                        if isinstance(error, EmbeddingProviderError)
                        else None
                    ),
                    started_at=embedding_started_at,
                    finished_at=utc_now(),
                )
                raise
            self.database.record_embedding_call(
                call_id=embedding_call_id,
                run_id=row["updated_run_id"],
                embedding_schema_version=self.schema.version,
                provider=self.schema.provider,
                model=self.schema.model,
                operation="candidate_documents",
                input_hashes=[embedding_input.input_hash],
                status="succeeded",
                started_at=embedding_started_at,
                finished_at=utc_now(),
            )
            self.index.upsert_candidate(
                self.schema,
                candidate_key=candidate_key,
                relevance_vector=vectors[0],
                duplicate_vector=vectors[1],
                payload={
                    "platform": row["platform"],
                    "source_id": row["source_id"],
                    "projection_revision": revision,
                    "input_hash": input_hash,
                },
            )
            with self.database.transaction() as connection:
                current = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM candidate_embeddings
                    WHERE candidate_key = ? AND embedding_schema_version = ?
                      AND projection_revision = ? AND current_input_hash = ?
                      AND index_status = 'pending'
                    """,
                    (candidate_key, self.schema.version, revision, input_hash),
                ).fetchone()["count"]
                if current != 2:
                    connection.execute(
                        "UPDATE vector_index_outbox SET status = 'superseded', completed_at = ? WHERE event_id = ?",
                        (utc_now(), event_id),
                    )
                    return ProjectionResult(event_id, candidate_key, revision, "superseded")
                connection.execute(
                    """
                    UPDATE candidate_embeddings
                    SET indexed_input_hash = current_input_hash,
                        index_status = 'ready', indexed_at = ?
                    WHERE candidate_key = ? AND embedding_schema_version = ?
                      AND projection_revision = ? AND current_input_hash = ?
                    """,
                    (utc_now(), candidate_key, self.schema.version, revision, input_hash),
                )
                connection.execute(
                    "UPDATE vector_index_outbox SET status = 'completed', completed_at = ? WHERE event_id = ?",
                    (utc_now(), event_id),
                )
            return ProjectionResult(event_id, candidate_key, revision, "completed")
        except Exception:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE vector_index_outbox SET status = 'failed', completed_at = ? WHERE event_id = ?",
                    (utc_now(), event_id),
                )
                connection.execute(
                    """
                    UPDATE candidate_embeddings SET index_status = 'failed'
                    WHERE candidate_key = ? AND embedding_schema_version = ?
                      AND projection_revision = ?
                    """,
                    (candidate_key, self.schema.version, revision),
                )
            raise

    def process_all(self) -> tuple[ProjectionResult, ...]:
        results: list[ProjectionResult] = []
        while True:
            result = self.process_next()
            if result is None:
                return tuple(results)
            results.append(result)

    def recover_processing_events(self) -> int:
        """Return interrupted events to pending before starting the sole writer."""

        with self.database.transaction() as connection:
            return connection.execute(
                """
                UPDATE vector_index_outbox SET status = 'pending', completed_at = NULL
                WHERE embedding_schema_version = ? AND status = 'processing'
                """,
                (self.schema.version,),
            ).rowcount

    def _claim_next(self):
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM vector_index_outbox
                WHERE embedding_schema_version = ? AND status = 'pending'
                ORDER BY created_at, event_id LIMIT 1
                """,
                (self.schema.version,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE vector_index_outbox SET status = 'processing' WHERE event_id = ?",
                (row["event_id"],),
            )
            return dict(row)

    def _mark_superseded(
        self, event_id: str, candidate_key: str, revision: int
    ) -> ProjectionResult:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE vector_index_outbox SET status = 'superseded', completed_at = ? WHERE event_id = ?",
                (utc_now(), event_id),
            )
        return ProjectionResult(event_id, candidate_key, revision, "superseded")

    def _register_schema(self) -> None:
        self.database.register_embedding_schema(self.schema)


def _candidate_from_row(row) -> CandidateMetadata:
    tags = json.loads(row["tags_json"])
    return CandidateMetadata(
        candidate_key=row["candidate_key"],
        title=row["title"] or "",
        video_description=row["video_description"] or "",
        tags=tuple(item for item in tags if isinstance(item, str)),
        uploader=row["uploader"] or "",
        channel=row["channel"] or "",
        playlist=row["playlist"] or "",
    )
