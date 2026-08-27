"""Restartable orchestration across calibration preparation and campaign iterations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from surveillance_video_agent.batch_generator import (
    cancel_secondary_batch,
    generate_secondary_batch,
)
from surveillance_video_agent.batch_manifest import BatchExportResult, export_secondary_batch
from surveillance_video_agent.calibration_dataset import (
    CalibrationExportResult,
    export_calibration_dataset,
)
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.dedupe import DedupePolicy, refresh_vector_duplicate_clusters
from surveillance_video_agent.download_pipeline import SerialDownloadWorker, enqueue_downloads
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.frontier import refresh_frontier
from surveillance_video_agent.feedback_rerank import build_feedback_ranking_vectors
from surveillance_video_agent.policies import (
    FrontierPolicyRecord,
    get_campaign_policy,
    get_frontier_policy,
)
from surveillance_video_agent.projection import VectorProjectionService
from surveillance_video_agent.semantic_queries import SemanticQueryVectorService
from surveillance_video_agent.vector_index import QdrantVectorIndex


@dataclass(frozen=True, slots=True)
class CalibrationPreparationSummary:
    campaign_id: str
    projection_enqueued_count: int
    projection_processed_count: int
    calibration_export: CalibrationExportResult


@dataclass(frozen=True, slots=True)
class CampaignIterationSummary:
    campaign_id: str
    status: str
    batch_export: BatchExportResult | None
    batch_yield: float | None
    queued_count: int
    download_outcome_count: int
    reason: str | None = None


def prepare_calibration_phase(
    database: CandidateDatabase,
    index: QdrantVectorIndex,
    projection: VectorProjectionService,
    semantic_queries: SemanticQueryVectorService,
    schema: EmbeddingSchema,
    *,
    run_id: str,
    campaign_id: str,
    query_pack_path: Path,
    output_path: Path,
) -> CalibrationPreparationSummary:
    query_result = semantic_queries.prepare(
        run_id=run_id,
        query_pack_path=query_pack_path,
    )
    if query_result.campaign_id != campaign_id:
        raise ValueError("calibration campaign does not match query pack")
    rows = database.connection.execute(
        """
        SELECT DISTINCT c.candidate_key
        FROM candidates c
        JOIN candidate_task_scores t ON t.candidate_key = c.candidate_key
        WHERE c.status = 'source_qualified' AND c.resource_eligible = 1
          AND t.campaign_id = ? AND t.qualified = 1 AND t.score >= 4
        ORDER BY c.candidate_key
        """,
        (campaign_id,),
    ).fetchall()
    enqueued = 0
    for row in rows:
        if projection.enqueue_candidate(row["candidate_key"]) is not None:
            enqueued += 1
    projection.recover_processing_events()
    processed = projection.process_all()
    export = export_calibration_dataset(
        database,
        index,
        schema,
        run_id=run_id,
        campaign_id=campaign_id,
        query_pack_version=query_result.query_pack_version,
        query_vectors=query_result.vectors,
        output_path=output_path,
    )
    return CalibrationPreparationSummary(
        campaign_id=campaign_id,
        projection_enqueued_count=enqueued,
        projection_processed_count=len(processed),
        calibration_export=export,
    )


def run_calibrated_iteration(
    database: CandidateDatabase,
    index: QdrantVectorIndex,
    semantic_queries: SemanticQueryVectorService,
    schema: EmbeddingSchema,
    dedupe_policy: DedupePolicy,
    *,
    run_id: str,
    campaign_id: str,
    query_pack_path: Path,
    batch_output_path: Path,
    download_worker: SerialDownloadWorker | None = None,
    enable_downloads: bool = False,
) -> CampaignIterationSummary:
    campaign_policy = get_campaign_policy(database, campaign_id)
    hold = database.connection.execute(
        """
        SELECT action, reason FROM campaign_hold_events
        WHERE campaign_id = ?
        ORDER BY created_at DESC, event_id DESC LIMIT 1
        """,
        (campaign_id,),
    ).fetchone()
    if hold is not None and hold["action"] == "hold":
        return CampaignIterationSummary(
            campaign_id,
            "stopped",
            None,
            None,
            0,
            0,
            f"campaign hold: {hold['reason']}",
        )
    frontier_record = get_frontier_policy(database, campaign_id)
    _validate_frontier_binding(frontier_record, schema, dedupe_policy)
    control = database.connection.execute(
        """
        SELECT status, stop_reason FROM campaign_run_control
        WHERE run_id = ? AND campaign_id = ?
        """,
        (run_id, campaign_id),
    ).fetchone()
    if control is not None and control["status"] == "stopped":
        return CampaignIterationSummary(
            campaign_id, "stopped", None, None, 0, 0, control["stop_reason"]
        )
    if _campaign_complete(database, campaign_policy):
        return CampaignIterationSummary(
            campaign_id, "completed", None, None, 0, 0, "subtype targets met"
        )
    if database.connection.execute(
        """
        SELECT 1 FROM media_publish_intents WHERE status = 'pending' LIMIT 1
        """
    ).fetchone() is not None:
        if download_worker is None:
            raise ValueError("pending publish intent requires a download worker")
        download_worker.recover_publish_intents()
    active_queue = database.connection.execute(
        """
        SELECT COUNT(*) FROM queue_assignments q
        JOIN candidates c ON c.candidate_key = q.candidate_key
        WHERE q.campaign_id = ? AND c.status = 'task_queued'
        """,
        (campaign_id,),
    ).fetchone()[0]
    if active_queue:
        if not enable_downloads or download_worker is None:
            return CampaignIterationSummary(
                campaign_id,
                "awaiting_download_approval",
                None,
                None,
                0,
                0,
                "task_queued candidates already exist",
            )
        outcomes = download_worker.process_until_idle()
        return CampaignIterationSummary(
            campaign_id, "downloads_processed", None, None, 0, len(outcomes)
        )
    unfinished = database.connection.execute(
        """
        SELECT batch_id FROM secondary_batches
        WHERE run_id = ? AND campaign_id = ?
          AND status IN ('reviewed', 'queued')
        ORDER BY created_at LIMIT 1
        """,
        (run_id, campaign_id),
    ).fetchone()
    if unfinished is not None:
        batch_export = export_secondary_batch(
            database,
            unfinished["batch_id"],
            batch_output_path,
        )
        yield_row = database.connection.execute(
            "SELECT yield_rate FROM secondary_batch_yields WHERE batch_id = ?",
            (unfinished["batch_id"],),
        ).fetchone()
        if not enable_downloads:
            return CampaignIterationSummary(
                campaign_id,
                "awaiting_download_approval",
                batch_export,
                yield_row["yield_rate"] if yield_row else None,
                0,
                0,
                "existing reviewed batch; downloads explicitly disabled",
            )
        if download_worker is None:
            raise ValueError("enable_downloads requires a SerialDownloadWorker")
        queued = enqueue_downloads(database, unfinished["batch_id"])
        outcomes = download_worker.process_until_idle()
        return CampaignIterationSummary(
            campaign_id,
            "downloads_processed",
            batch_export,
            yield_row["yield_rate"] if yield_row else None,
            len(queued),
            len(outcomes),
        )
    query_result = semantic_queries.prepare(
        run_id=run_id,
        query_pack_path=query_pack_path,
    )
    _require_embedding_coverage(database, campaign_id, schema.version)
    refresh_vector_duplicate_clusters(
        database,
        index,
        schema,
        dedupe_policy,
        run_id=run_id,
        campaign_id=campaign_id,
    )
    frontier = refresh_frontier(
        database,
        run_id=run_id,
        campaign_id=campaign_id,
        query_pack_version=query_result.query_pack_version,
        frontier_policy_version=frontier_record.policy.version,
        embedding_schema_version=schema.version,
        dedupe_policy_version=dedupe_policy.version,
    )
    if frontier.ready_count == 0:
        selected = database.connection.execute(
            """
            SELECT COUNT(*) FROM probe_selections
            WHERE campaign_id = ? AND query_pack_version = ?
            """,
            (campaign_id, query_result.query_pack_version),
        ).fetchone()[0]
        reason = (
            "frontier exhausted and probe budget used"
            if selected >= frontier_record.probe_budget
            else "frontier currently empty; more discovery may be required"
        )
        return CampaignIterationSummary(
            campaign_id, "frontier_exhausted", None, None, 0, 0, reason
        )
    batch = generate_secondary_batch(
        database,
        index,
        schema,
        campaign_policy,
        frontier_record.policy,
        run_id=run_id,
        dedupe_policy_version=dedupe_policy.version,
        query_vectors=build_feedback_ranking_vectors(
            database,
            index,
            schema,
            campaign_id=campaign_id,
            base_vectors=query_result.vectors,
            task_weight=frontier_record.policy.feedback_task_weight,
            source_weight=frontier_record.policy.feedback_source_weight,
        ),
        eligibility_query_vectors=query_result.vectors,
    )
    batch_export = export_secondary_batch(
        database,
        batch.batch_id,
        batch_output_path,
    )
    control = database.connection.execute(
        """
        SELECT status, stop_reason FROM campaign_run_control
        WHERE run_id = ? AND campaign_id = ?
        """,
        (run_id, campaign_id),
    ).fetchone()
    if control is not None and control["status"] == "stopped":
        return CampaignIterationSummary(
            campaign_id,
            "stopped",
            batch_export,
            batch.yield_rate,
            0,
            0,
            control["stop_reason"],
        )
    if not enable_downloads:
        return CampaignIterationSummary(
            campaign_id,
            "awaiting_download_approval",
            batch_export,
            batch.yield_rate,
            0,
            0,
            "batch reviewed; downloads explicitly disabled",
        )
    if download_worker is None:
        raise ValueError("enable_downloads requires a SerialDownloadWorker")
    queued = enqueue_downloads(database, batch.batch_id)
    outcomes = download_worker.process_until_idle()
    return CampaignIterationSummary(
        campaign_id,
        "downloads_processed",
        batch_export,
        batch.yield_rate,
        len(queued),
        len(outcomes),
    )


def _validate_frontier_binding(
    frontier: FrontierPolicyRecord,
    schema: EmbeddingSchema,
    dedupe_policy: DedupePolicy,
) -> None:
    if frontier.embedding_schema_version != schema.version:
        raise ValueError("frontier policy uses a different embedding schema")
    if frontier.dedupe_policy_version != dedupe_policy.version:
        raise ValueError("frontier policy uses a different dedupe policy")


def _require_embedding_coverage(
    database: CandidateDatabase,
    campaign_id: str,
    embedding_schema_version: str,
) -> None:
    rows = database.connection.execute(
        """
        SELECT c.candidate_key,
               SUM(CASE WHEN e.index_status = 'ready'
                         AND e.indexed_input_hash = e.current_input_hash
                        THEN 1 ELSE 0 END) AS ready_vectors
        FROM candidates c
        JOIN candidate_task_scores t ON t.candidate_key = c.candidate_key
        LEFT JOIN candidate_embeddings e
          ON e.candidate_key = c.candidate_key
         AND e.embedding_schema_version = ?
        WHERE c.status = 'source_qualified' AND c.resource_eligible = 1
          AND NOT EXISTS (
              SELECT 1 FROM candidate_suppressions cs
              WHERE cs.candidate_key = c.candidate_key
                AND cs.suppression_kind = 'source_hard_exclusion'
                AND NOT EXISTS (
                    SELECT 1 FROM candidate_suppression_releases csr
                    WHERE csr.suppression_id = cs.suppression_id
                )
          )
          AND t.campaign_id = ?
          AND (
              (t.qualified = 1 AND t.score >= 4)
              OR EXISTS (
                  SELECT 1 FROM semantic_task_eligibility s
                  WHERE s.candidate_key = c.candidate_key
                    AND s.campaign_id = t.campaign_id
                    AND s.subtype = t.subtype
                    AND s.embedding_schema_version = ?
              )
          )
        GROUP BY c.candidate_key
        HAVING ready_vectors < 1
        """,
        (embedding_schema_version, campaign_id, embedding_schema_version),
    ).fetchall()
    if rows:
        raise ValueError(
            f"eligible embedding coverage incomplete for {len(rows)} candidates"
        )


def _campaign_complete(database: CandidateDatabase, campaign_policy) -> bool:
    target = database.connection.execute(
        """
        SELECT target_count FROM campaign_human_targets
        WHERE campaign_id = ? AND target_kind = 'task_usable'
        ORDER BY created_at DESC, target_id DESC LIMIT 1
        """,
        (campaign_policy.campaign_id,),
    ).fetchone()
    if target is not None:
        usable = database.connection.execute(
            """
            SELECT COUNT(DISTINCT candidate_key)
            FROM pilot_feedback_labels
            WHERE campaign_id = ? AND task_usable = 1
            """,
            (campaign_policy.campaign_id,),
        ).fetchone()[0]
        if int(usable) >= int(target["target_count"]):
            return True
    for subtype, target in campaign_policy.subtype_limits:
        count = database.connection.execute(
            """
            SELECT COUNT(*) FROM queue_assignments q
            JOIN candidates c ON c.candidate_key = q.candidate_key
            WHERE q.campaign_id = ? AND q.subtype = ?
              AND c.status = 'downloaded'
            """,
            (campaign_policy.campaign_id, subtype),
        ).fetchone()[0]
        if count < target:
            return False
    return True
