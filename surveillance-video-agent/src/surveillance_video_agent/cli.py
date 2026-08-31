"""Operational CLI for the surveillance candidate-pool v2 workflow."""

from __future__ import annotations

import argparse
import json
import random
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from surveillance_video_agent.adapters import (
    DailymotionAdapter,
    PeerTubeAdapter,
    YouTubeAdapter,
)
from surveillance_video_agent.calibration import (
    calibrate_relevance_thresholds,
    store_calibration_result,
)
from surveillance_video_agent.calibration_dataset import (
    load_labeled_calibration_dataset,
)
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.dedupe import get_dedupe_policy
from surveillance_video_agent.discovery import DiscoveryConfig, DiscoveryService
from surveillance_video_agent.download_pipeline import (
    DownloadWorkerConfig,
    SerialDownloadWorker,
)
from surveillance_video_agent.legacy_import import import_legacy_state
from surveillance_video_agent.manifest import export_campaign_manifest
from surveillance_video_agent.pilot_review import export_pilot_review
from surveillance_video_agent.pilot_feedback import (
    CONFLICT_ATTACK_NEGATIVE_TERMS,
    import_pilot_feedback,
    refine_semantic_gate,
)
from surveillance_video_agent.policies import (
    bootstrap_default_campaign_policies,
    bootstrap_safe_dedupe_policy,
    create_focused_frontier_policy,
    create_user_override_frontier_policy,
    get_campaign_policy,
    get_frontier_policy,
    update_campaign_policy,
    update_frontier_policy,
)
from surveillance_video_agent.projection import VectorProjectionService
from surveillance_video_agent.qwen_embedding import (
    QWEN_SCHEMA,
    DashScopeQwenEmbeddingProvider,
)
from surveillance_video_agent.rescore import rescore_source_qualified_candidates
from surveillance_video_agent.scoring import load_scoring_bundle
from surveillance_video_agent.semantic_queries import SemanticQueryVectorService
from surveillance_video_agent.semantic_recall import CalibrationSemanticRecallService
from surveillance_video_agent.vector_index import QdrantVectorIndex
from surveillance_video_agent.workflow import (
    prepare_calibration_phase,
    run_calibrated_iteration,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent
STATE_DB = PROJECT_ROOT / ".surveillance-pool/state/candidates.sqlite3"
QDRANT_PATH = PROJECT_ROOT / ".surveillance-pool/vector/qdrant"
INTERNAL_ROOT = PROJECT_ROOT / ".surveillance-pool"
OUTPUT_ROOT = WORKSPACE_ROOT / "Candidate_Downloads"
SCORING_POLICY = PROJECT_ROOT / "query-packs/scoring-policy.v1.9.0.json"


def _query_pack_minor(path: Path) -> int:
    marker = ".qp.v1."
    if marker not in path.name:
        return -1
    return int(path.name.split(marker, 1)[1].split(".", 1)[0])


SIGN_QUERY_PACKS = tuple(
    sorted(
        (PROJECT_ROOT / "query-packs/sign_action_v1").glob(
            "sign_action_v1.qp.v1.*.json"
        ),
        key=_query_pack_minor,
    )
)
QUERY_PACKS = {
    "demand_action_v1": PROJECT_ROOT
    / "query-packs/demand_action_v1/demand_action_v1.qp.v1.3.0.json",
    "fight_confounder_v1": PROJECT_ROOT
    / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.10.0.json",
    "sign_action_v1": PROJECT_ROOT
    / SIGN_QUERY_PACKS[-1].relative_to(PROJECT_ROOT),
    "fight_positive_v1": PROJECT_ROOT
    / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.4.0.json",
}


def initialize_state(*, import_legacy: bool = True) -> dict[str, Any]:
    with CandidateDatabase(STATE_DB) as database:
        database.initialize()
        for path in QUERY_PACKS.values():
            database.register_frozen_query_pack(path)
        bootstrap_default_campaign_policies(database)
        database.register_embedding_schema(QWEN_SCHEMA)
        dedupe = bootstrap_safe_dedupe_policy(database, QWEN_SCHEMA)
        legacy = None
        if import_legacy:
            legacy = import_legacy_state(
                database,
                history_path=WORKSPACE_ROOT / ".ytb-download/state/download-history.json",
                info_cache_dir=WORKSPACE_ROOT / ".ytb-download/cache",
                accepted_archive_path=WORKSPACE_ROOT
                / ".ytb-download/state/accepted.archive",
            )
        return {
            "state_db": str(STATE_DB),
            "qdrant_path": str(QDRANT_PATH),
            "campaigns": sorted(QUERY_PACKS),
            "embedding_schema_version": QWEN_SCHEMA.version,
            "safe_dedupe_policy_version": dedupe.version,
            "legacy": asdict(legacy) if legacy else None,
        }


def run_discovery_command(args) -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = args.run_id or "discovery-" + str(uuid.uuid4())
    adapters = _adapters(tuple(args.peertube_instance))
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        database.create_run(
            run_id,
            "discovery-calibration-preparation",
            config={
                "campaign_id": args.campaign,
                "query_pack": str(QUERY_PACKS[args.campaign]),
                "probe_limit": args.probe_limit,
                "downloads_enabled": False,
            },
        )
        try:
            scoring = load_scoring_bundle(
                SCORING_POLICY,
                tuple(QUERY_PACKS.values()),
            )
            discovery_service = DiscoveryService(database, adapters, scoring)
            config = DiscoveryConfig(
                campaign_id=args.campaign,
                query_pack_version=_query_pack_version(QUERY_PACKS[args.campaign]),
                probe_limit=args.probe_limit,
            )
            discovery = discovery_service.discover_and_qualify(
                run_id=run_id,
                config=config,
            )
            provider = DashScopeQwenEmbeddingProvider()
            projection = VectorProjectionService(
                database,
                index,
                provider,
                QWEN_SCHEMA,
            )
            semantic = SemanticQueryVectorService(
                database,
                index,
                provider,
                QWEN_SCHEMA,
            )
            output_path = (
                INTERNAL_ROOT
                / "runs"
                / run_id
                / "calibration"
                / f"{args.campaign}.jsonl"
            )
            preparation = prepare_calibration_phase(
                database,
                index,
                projection,
                semantic,
                QWEN_SCHEMA,
                run_id=run_id,
                campaign_id=args.campaign,
                query_pack_path=QUERY_PACKS[args.campaign],
                output_path=output_path,
            )
            result = {
                "run_id": run_id,
                "downloads_attempted": False,
                "discovery": asdict(discovery),
                "calibration": _jsonable(asdict(preparation)),
            }
            database.finish_run(run_id, status="completed", result=result)
            return result
        except Exception:
            database.finish_run(run_id, status="failed", result={"stage": "discovery"})
            raise


def run_calibration_command(args) -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = "calibration-" + str(uuid.uuid4())
    with CandidateDatabase(STATE_DB) as database:
        database.initialize()
        database.create_run(
            run_id,
            "threshold-calibration",
            config={"campaign_id": args.campaign, "labels": str(args.labels)},
        )
        campaign = get_campaign_policy(database, args.campaign)
        examples = load_labeled_calibration_dataset(args.labels)
        result = calibrate_relevance_thresholds(
            examples,
            campaign_id=args.campaign,
            embedding_schema_version=QWEN_SCHEMA.version,
            expected_subtypes=[name for name, _ in campaign.subtype_limits],
        )
        store_calibration_result(
            database,
            QWEN_SCHEMA,
            result,
            run_id=run_id,
        )
        output = {
            "run_id": run_id,
            "calibration_id": result.calibration_id,
            "status": result.status,
            "thresholds": dict(result.thresholds),
            "subtypes": [asdict(item) for item in result.subtypes],
        }
        if result.status != "passed":
            database.finish_run(run_id, status="stopped", result=output)
            return output
        dedupe = bootstrap_safe_dedupe_policy(database, QWEN_SCHEMA)
        campaign_row = database.connection.execute(
            """
            SELECT active_frontier_policy_version FROM campaigns
            WHERE campaign_id = ?
            """,
            (args.campaign,),
        ).fetchone()
        frontier = update_frontier_policy(
            database,
            campaign_id=args.campaign,
            expected_version=campaign_row["active_frontier_policy_version"],
            calibration_id=result.calibration_id,
            probe_budget=args.probe_limit,
            batch_size=args.batch_size,
            vector_oversample_factor=5,
            embedding_schema_version=QWEN_SCHEMA.version,
            rrf_k=60,
            dedupe_policy_version=dedupe.version,
            low_yield_threshold=0.10,
            low_yield_consecutive_windows=3,
            low_yield_partition_window_size=20,
            reason="operator imported passed human calibration labels",
        )
        output["frontier_policy_version"] = frontier.policy.version
        database.finish_run(run_id, status="completed", result=output)
        return output


def run_rescore_command() -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = "rescore-" + str(uuid.uuid4())
    with CandidateDatabase(STATE_DB) as database:
        database.initialize()
        database.create_run(
            run_id,
            "offline-task-rescore",
            config={"scoring_policy": str(SCORING_POLICY)},
        )
        try:
            policy = load_scoring_bundle(
                SCORING_POLICY,
                tuple(QUERY_PACKS.values()),
            )
            result = rescore_source_qualified_candidates(
                database,
                policy,
                run_id=run_id,
            )
            output = asdict(result)
            output["run_id"] = run_id
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(run_id, status="failed", result={"stage": "rescore"})
            raise


def run_semantic_recall_command(args) -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = "semantic-recall-" + str(uuid.uuid4())
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        database.create_run(
            run_id,
            "calibration-only-semantic-recall",
            config={
                "campaigns": sorted(QUERY_PACKS),
                "top_n_per_subtype": args.top_n,
                "production_task_state_changes": False,
                "downloads_enabled": False,
            },
        )
        try:
            provider = DashScopeQwenEmbeddingProvider()
            recall = CalibrationSemanticRecallService(
                database,
                index,
                provider,
                QWEN_SCHEMA,
            )
            index_result = recall.prepare_index(run_id=run_id)
            semantic = SemanticQueryVectorService(
                database,
                index,
                provider,
                QWEN_SCHEMA,
            )
            exports = {}
            for campaign_id, query_pack_path in QUERY_PACKS.items():
                query_result = semantic.prepare(
                    run_id=run_id,
                    query_pack_path=query_pack_path,
                )
                destination = (
                    INTERNAL_ROOT
                    / "runs"
                    / run_id
                    / "semantic-recall"
                    / f"{campaign_id}.jsonl"
                )
                export = recall.export_recall(
                    run_id=run_id,
                    campaign_id=campaign_id,
                    query_pack_version=query_result.query_pack_version,
                    query_vectors=query_result.vectors,
                    scoring_policy_version=_scoring_policy_version(),
                    output_path=destination,
                    top_n_per_subtype=args.top_n,
                )
                exports[campaign_id] = _jsonable(asdict(export))
            output = {
                "run_id": run_id,
                "calibration_only": True,
                "candidate_state_changes": False,
                "downloads_attempted": False,
                "index": asdict(index_result),
                "exports": exports,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id,
                status="failed",
                result={"stage": "semantic_recall"},
            )
            raise


def run_semantic_override_command(args) -> dict[str, Any]:
    """Activate the user's explicit semantic threshold as an auditable policy."""

    initialize_state(import_legacy=True)
    run_id = "semantic-override-" + str(uuid.uuid4())
    policy_version = args.policy_version or (
        f"user-semantic-threshold-{args.threshold:.2f}-v1.0.0"
    )
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        database.create_run(
            run_id,
            "explicit-user-semantic-threshold-override",
            config={
                "threshold": args.threshold,
                "comparison": ">",
                "batch_size": args.batch_size,
                "downloads_enabled": False,
                "source_gate_relaxed": False,
                "resource_gate_relaxed": False,
            },
        )
        try:
            provider = DashScopeQwenEmbeddingProvider()
            recall = CalibrationSemanticRecallService(
                database,
                index,
                provider,
                QWEN_SCHEMA,
            )
            index_result = recall.prepare_index(run_id=run_id)
            semantic = SemanticQueryVectorService(
                database,
                index,
                provider,
                QWEN_SCHEMA,
            )
            campaign_vectors = {}
            for campaign_id, query_pack_path in QUERY_PACKS.items():
                prepared = semantic.prepare(
                    run_id=run_id,
                    query_pack_path=query_pack_path,
                )
                campaign_vectors[campaign_id] = (
                    prepared.query_pack_version,
                    prepared.vectors,
                )
            override = recall.apply_threshold_override(
                run_id=run_id,
                campaign_query_vectors=campaign_vectors,
                scoring_policy_version=_scoring_policy_version(),
                threshold=args.threshold,
                policy_version=policy_version,
            )
            dedupe = bootstrap_safe_dedupe_policy(database, QWEN_SCHEMA)
            policies = {}
            for campaign_id, (query_pack_version, _) in campaign_vectors.items():
                active = database.connection.execute(
                    """
                    SELECT active_frontier_policy_version FROM campaigns
                    WHERE campaign_id = ?
                    """,
                    (campaign_id,),
                ).fetchone()
                frontier = create_user_override_frontier_policy(
                    database,
                    campaign_id=campaign_id,
                    expected_version=active["active_frontier_policy_version"],
                    query_pack_version=query_pack_version,
                    embedding_schema_version=QWEN_SCHEMA.version,
                    dedupe_policy_version=dedupe.version,
                    semantic_eligibility_policy_version=policy_version,
                    threshold=args.threshold,
                    batch_size=args.batch_size,
                    reason=(
                        "User explicitly requested all semantic similarities above "
                        f"{args.threshold:.2f} enter the qualified competition pool; "
                        "source/resource gates remain unchanged"
                    ),
                )
                policies[campaign_id] = frontier.policy.version
            output = {
                "run_id": run_id,
                "threshold": args.threshold,
                "comparison": ">",
                "batch_size": args.batch_size,
                "source_gate_relaxed": False,
                "resource_gate_relaxed": False,
                "index": asdict(index_result),
                "eligibility": _jsonable(asdict(override)),
                "frontier_policies": policies,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id,
                status="failed",
                result={"stage": "semantic_threshold_override"},
            )
            raise


def run_pilot_feedback_command(args) -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = "pilot-feedback-" + str(uuid.uuid4())
    base_policy_version = "user-semantic-threshold-0.40-v1.0.0"
    refined_policy_version = "pilot-feedback-semantic-gate-v1.1.0"
    thresholds = {
        "demand_action_v1": 0.44,
        "fight_confounder_v1": 0.40,
    }
    with CandidateDatabase(STATE_DB) as database:
        database.initialize()
        database.create_run(
            run_id,
            "pilot-feedback-policy-refinement",
            config={
                "feedback_path": str(args.feedback.resolve()),
                "base_policy_version": base_policy_version,
                "refined_policy_version": refined_policy_version,
                "campaign_thresholds": thresholds,
                "minimum_source_score": 6,
                "downloads_enabled": False,
            },
        )
        try:
            feedback = import_pilot_feedback(
                database,
                args.feedback,
                run_id=run_id,
            )
            refinement = refine_semantic_gate(
                database,
                run_id=run_id,
                base_policy_version=base_policy_version,
                policy_version=refined_policy_version,
                campaign_thresholds=thresholds,
                minimum_source_score=6,
                negative_terms={
                    ("fight_confounder_v1", "冲突但未攻击"):
                    CONFLICT_ATTACK_NEGATIVE_TERMS,
                },
            )
            dedupe = bootstrap_safe_dedupe_policy(database, QWEN_SCHEMA)
            policies = {}
            for campaign_id, threshold in thresholds.items():
                active = database.connection.execute(
                    """
                    SELECT active_frontier_policy_version FROM campaigns
                    WHERE campaign_id = ?
                    """,
                    (campaign_id,),
                ).fetchone()
                frontier = create_user_override_frontier_policy(
                    database,
                    campaign_id=campaign_id,
                    expected_version=active["active_frontier_policy_version"],
                    query_pack_version=_query_pack_version(QUERY_PACKS[campaign_id]),
                    embedding_schema_version=QWEN_SCHEMA.version,
                    dedupe_policy_version=dedupe.version,
                    semantic_eligibility_policy_version=refined_policy_version,
                    threshold=threshold,
                    batch_size=5,
                    reason=(
                        "Refined from the first visual pilot: semantic-only source "
                        "score >= 6; demand threshold > 0.44; fight threshold > 0.40; "
                        "explicit attack metadata excluded from non-attack conflict"
                    ),
                )
                policies[campaign_id] = frontier.policy.version
            manifest_counts = {
                campaign_id: export_campaign_manifest(
                    database,
                    campaign_id,
                    OUTPUT_ROOT / campaign_id / "manifest.jsonl",
                )
                for campaign_id in thresholds
            }
            attempted = sum(manifest_counts.values())
            assignment_count = int(
                database.connection.execute(
                    """
                    SELECT COUNT(*) FROM queue_assignments
                    WHERE campaign_id IN ('demand_action_v1', 'fight_confounder_v1')
                    """
                ).fetchone()[0]
            )
            downloaded = int(
                database.connection.execute(
                    """
                    SELECT COUNT(*) FROM queue_assignments q
                    JOIN candidates c ON c.candidate_key = q.candidate_key
                    WHERE q.campaign_id IN ('demand_action_v1', 'fight_confounder_v1')
                      AND c.status = 'downloaded'
                    """
                ).fetchone()[0]
            )
            duplicate_count = int(
                database.connection.execute(
                    "SELECT COUNT(*) FROM duplicate_edges WHERE kind = 'sha256'"
                ).fetchone()[0]
            )
            source_rate = (
                feedback.source_correct_count / feedback.source_determinate_count
                if feedback.source_determinate_count
                else 0.0
            )
            task_rate = (
                feedback.task_usable_count / feedback.task_determinate_count
                if feedback.task_determinate_count
                else 0.0
            )
            technical_rate = downloaded / attempted if attempted else 0.0
            gates = {
                "source_accuracy_gt_60pct": source_rate > 0.60,
                "task_usable_gt_20pct": task_rate > 0.20,
                "manifest_complete_100pct": attempted == assignment_count,
                "technical_success_gt_90pct": technical_rate > 0.90,
                "no_duplicates": duplicate_count == 0,
            }
            output = {
                "run_id": run_id,
                "feedback": asdict(feedback),
                "rates": {
                    "source_accuracy": source_rate,
                    "task_usable": task_rate,
                    "technical_success": technical_rate,
                },
                "gates": gates,
                "expansion_allowed": all(gates.values()),
                "refinement": _jsonable(asdict(refinement)),
                "active_frontier_policies": policies,
                "downloads_attempted": False,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id,
                status="failed",
                result={"stage": "pilot_feedback_refinement"},
            )
            raise


def run_pilot_feedback_import_command(args) -> dict[str, Any]:
    """Import a later visual pilot without implicitly changing frozen policy."""

    initialize_state(import_legacy=True)
    run_id = "pilot-feedback-import-" + str(uuid.uuid4())
    with CandidateDatabase(STATE_DB) as database:
        database.initialize()
        database.create_run(
            run_id,
            "pilot-feedback-import",
            config={"feedback_path": str(args.feedback.resolve())},
        )
        try:
            feedback = import_pilot_feedback(
                database,
                args.feedback,
                run_id=run_id,
            )
            groups = [
                dict(row)
                for row in database.connection.execute(
                    """
                    SELECT l.campaign_id, l.shown_subtype,
                           COUNT(*) AS label_count,
                           SUM(CASE WHEN l.task_usable = 1 THEN 1 ELSE 0 END)
                               AS task_usable_count,
                           SUM(CASE WHEN l.source_correct = 1 THEN 1 ELSE 0 END)
                               AS source_correct_count
                    FROM pilot_feedback_labels l
                    WHERE l.import_id = ?
                    GROUP BY l.campaign_id, l.shown_subtype
                    ORDER BY l.campaign_id, l.shown_subtype
                    """,
                    (feedback.import_id,),
                ).fetchall()
            ]
            output = {
                "run_id": run_id,
                "feedback": asdict(feedback),
                "groups": groups,
                "policy_changed": False,
                "downloads_attempted": False,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id,
                status="failed",
                result={"stage": "pilot_feedback_import"},
            )
            raise


def run_campaign_hold_command(args) -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = "campaign-hold-" + str(uuid.uuid4())
    with CandidateDatabase(STATE_DB) as database:
        database.initialize()
        database.create_run(
            run_id,
            "campaign-operational-hold",
            config={
                "campaign_id": args.campaign,
                "action": args.action,
                "reason": args.reason,
            },
        )
        database.connection.execute(
            """
            INSERT INTO campaign_hold_events(
                event_id, campaign_id, action, reason, run_id, created_at
            ) VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (str(uuid.uuid4()), args.campaign, args.action, args.reason, run_id),
        )
        output = {
            "run_id": run_id,
            "campaign_id": args.campaign,
            "action": args.action,
            "reason": args.reason,
        }
        database.finish_run(run_id, status="completed", result=output)
        return output


def run_download_only_capacity_command(args) -> dict[str, Any]:
    """Version a statistical candidate budget without touching human labels."""

    initialize_state(import_legacy=True)
    run_id = "download-only-capacity-" + str(uuid.uuid4())
    with CandidateDatabase(STATE_DB) as database:
        database.initialize()
        database.create_run(
            run_id,
            "download-only-statistical-capacity",
            config={
                "campaign_id": args.campaign,
                "candidate_budget": args.candidate_budget,
                "human_labels_imported": False,
                "review_export_enabled": False,
            },
        )
        try:
            current = get_campaign_policy(database, args.campaign)
            subtype_limits = _scale_subtype_limits(
                current.subtype_limits, args.candidate_budget
            )
            policy = update_campaign_policy(
                database,
                campaign_id=args.campaign,
                expected_version=current.version,
                subtype_limits=subtype_limits,
                max_candidates=args.candidate_budget,
                reason=args.reason,
                created_by="download_only_statistical_capacity",
            )
            database.connection.execute(
                """
                INSERT INTO campaign_hold_events(
                    event_id, campaign_id, action, reason, run_id, created_at
                ) VALUES (?, ?, 'release', ?, ?, datetime('now'))
                """,
                (
                    str(uuid.uuid4()),
                    args.campaign,
                    "external platform owns human labels; release download-only batches",
                    run_id,
                ),
            )
            output = {
                "run_id": run_id,
                "campaign_id": args.campaign,
                "candidate_budget": args.candidate_budget,
                "subtype_limits": subtype_limits,
                "campaign_policy_version": policy.version,
                "human_labels_imported": False,
                "review_export_enabled": False,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id, status="failed", result={"stage": "capacity_update"}
            )
            raise


def _scale_subtype_limits(
    limits: Sequence[tuple[str, int]], target: int
) -> dict[str, int]:
    """Scale existing subtype mix by largest remainder, preserving semantics."""

    if target <= 0:
        raise ValueError("candidate budget must be positive")
    total = sum(limit for _, limit in limits)
    if total <= 0:
        raise ValueError("existing subtype limits must be positive")
    raw = [(name, target * limit / total) for name, limit in limits]
    scaled = {name: int(value) for name, value in raw}
    for name, _ in sorted(raw, key=lambda item: (-((item[1]) % 1), item[0]))[
        : target - sum(scaled.values())
    ]:
        scaled[name] += 1
    return scaled


def run_sign_expand_capacity_command() -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = "sign-expand-capacity-" + str(uuid.uuid4())
    campaign_id = "sign_action_v1"
    with CandidateDatabase(STATE_DB) as database:
        database.initialize()
        database.create_run(
            run_id,
            "sign-human-target-capacity",
            config={
                "campaign_id": campaign_id,
                "human_usable_target": 60,
                "candidate_budget": 180,
            },
        )
        current = get_campaign_policy(database, campaign_id)
        policy = update_campaign_policy(
            database,
            campaign_id=campaign_id,
            expected_version=current.version,
            subtype_limits={"举牌/横幅": 180},
            max_candidates=180,
            reason=(
                "User confirmed target is 60 human-usable sign videos; "
                "35.3% observed precision requires approximately 180 candidate attempts"
            ),
        )
        database.connection.execute(
            """
            INSERT INTO campaign_human_targets(
                target_id, campaign_id, target_kind, target_count,
                candidate_budget, policy_version, reason, run_id, created_at
            ) VALUES (?, ?, 'task_usable', 60, 180, ?, ?, ?, datetime('now'))
            """,
            (
                str(uuid.uuid4()),
                campaign_id,
                policy.version,
                "User confirmed 60 means externally human-labeled task-usable videos",
                run_id,
            ),
        )
        output = {
            "run_id": run_id,
            "campaign_id": campaign_id,
            "campaign_policy_version": policy.version,
            "human_usable_target": 60,
            "candidate_budget": 180,
        }
        database.finish_run(run_id, status="completed", result=output)
        return output


def run_sign_focus_command() -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = "sign-focus-" + str(uuid.uuid4())
    campaign_id = "demand_action_v1"
    query_pack_path = QUERY_PACKS[campaign_id]
    query_pack_version = _query_pack_version(query_pack_path)
    base_policy_version = "demand-sign-v13-base-0.40-v1.0.0"
    focused_policy_version = "demand-sign-v13-focused-v1.0.0"
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        database.create_run(
            run_id,
            "focused-sign-frontier-activation",
            config={
                "campaign_id": campaign_id,
                "focused_subtype": "举牌/横幅",
                "query_pack_version": query_pack_version,
                "similarity_threshold": 0.44,
                "minimum_source_score": 6,
                "downloads_enabled": False,
            },
        )
        try:
            provider = DashScopeQwenEmbeddingProvider()
            recall = CalibrationSemanticRecallService(
                database,
                index,
                provider,
                QWEN_SCHEMA,
            )
            index_result = recall.prepare_index(run_id=run_id)
            semantic = SemanticQueryVectorService(
                database,
                index,
                provider,
                QWEN_SCHEMA,
            )
            prepared = semantic.prepare(
                run_id=run_id,
                query_pack_path=query_pack_path,
            )
            base = recall.apply_threshold_override(
                run_id=run_id,
                campaign_query_vectors={
                    campaign_id: (
                        prepared.query_pack_version,
                        {"举牌/横幅": prepared.vectors["举牌/横幅"]},
                    )
                },
                scoring_policy_version=_scoring_policy_version(),
                threshold=0.40,
                policy_version=base_policy_version,
                required_discovery_query_pack_versions={
                    campaign_id: query_pack_version
                },
            )
            focused = refine_semantic_gate(
                database,
                run_id=run_id,
                base_policy_version=base_policy_version,
                policy_version=focused_policy_version,
                campaign_thresholds={campaign_id: 0.44},
                minimum_source_score=6,
                negative_terms={},
            )
            dedupe = bootstrap_safe_dedupe_policy(database, QWEN_SCHEMA)
            active = database.connection.execute(
                """
                SELECT active_frontier_policy_version FROM campaigns
                WHERE campaign_id = ?
                """,
                (campaign_id,),
            ).fetchone()
            frontier = create_focused_frontier_policy(
                database,
                campaign_id=campaign_id,
                expected_version=active["active_frontier_policy_version"],
                focused_subtype="举牌/横幅",
                query_pack_version=query_pack_version,
                embedding_schema_version=QWEN_SCHEMA.version,
                dedupe_policy_version=dedupe.version,
                semantic_eligibility_policy_version=focused_policy_version,
                threshold=0.44,
                reason=(
                    "User directed collection focus to sign/banner; only v1.3 "
                    "discoveries with source score >= 6 and similarity > 0.44 compete"
                ),
                batch_size=5,
            )
            database.connection.execute(
                """
                INSERT INTO campaign_hold_events(
                    event_id, campaign_id, action, reason, run_id, created_at
                ) VALUES (?, ?, 'release', ?, ?, datetime('now'))
                """,
                (
                    str(uuid.uuid4()),
                    campaign_id,
                    "release only for five-item v1.3 focused sign pilot",
                    run_id,
                ),
            )
            output = {
                "run_id": run_id,
                "campaign_id": campaign_id,
                "focused_subtype": "举牌/横幅",
                "query_pack_version": query_pack_version,
                "index": asdict(index_result),
                "base_eligibility": _jsonable(asdict(base)),
                "focused_eligibility": _jsonable(asdict(focused)),
                "frontier_policy_version": frontier.policy.version,
                "downloads_attempted": False,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id,
                status="failed",
                result={"stage": "sign_focus_activation"},
            )
            raise


def run_sign_mobile_activate_command() -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = "sign-mobile-activate-" + str(uuid.uuid4())
    campaign_id = "sign_action_v1"
    query_pack_path = QUERY_PACKS[campaign_id]
    query_pack_version = _query_pack_version(query_pack_path)
    eligibility_policy = "sign-mobile-semantic-0.44-v1.0.0"
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        database.create_run(
            run_id,
            "sign-mobile-frontier-activation",
            config={
                "campaign_id": campaign_id,
                "query_pack_versions": [
                    "sign_action_v1.qp.v1.0.0",
                    "sign_action_v1.qp.v1.1.0",
                ],
                "threshold": 0.44,
                "allowed_camera_pools": ["surveillance", "mobile_adjacent"],
                "batch_size": 7,
                "downloads_enabled": False,
            },
        )
        try:
            provider = DashScopeQwenEmbeddingProvider()
            recall = CalibrationSemanticRecallService(
                database, index, provider, QWEN_SCHEMA
            )
            index_result = recall.prepare_index(run_id=run_id)
            semantic = SemanticQueryVectorService(
                database, index, provider, QWEN_SCHEMA
            )
            prepared = semantic.prepare(
                run_id=run_id,
                query_pack_path=query_pack_path,
            )
            eligibility = recall.apply_threshold_override(
                run_id=run_id,
                campaign_query_vectors={
                    campaign_id: (
                        prepared.query_pack_version,
                        {"举牌/横幅": prepared.vectors["举牌/横幅"]},
                    )
                },
                scoring_policy_version=_scoring_policy_version(),
                threshold=0.44,
                policy_version=eligibility_policy,
                required_discovery_query_pack_versions={
                    campaign_id: (
                        "sign_action_v1.qp.v1.0.0",
                        "sign_action_v1.qp.v1.1.0",
                    )
                },
            )
            dedupe = bootstrap_safe_dedupe_policy(database, QWEN_SCHEMA)
            active = database.connection.execute(
                """
                SELECT active_frontier_policy_version FROM campaigns
                WHERE campaign_id = ?
                """,
                (campaign_id,),
            ).fetchone()
            frontier = create_user_override_frontier_policy(
                database,
                campaign_id=campaign_id,
                expected_version=active["active_frontier_policy_version"],
                query_pack_version=query_pack_version,
                embedding_schema_version=QWEN_SCHEMA.version,
                dedupe_policy_version=dedupe.version,
                semantic_eligibility_policy_version=eligibility_policy,
                threshold=0.44,
                batch_size=7,
                allowed_camera_pools=("surveillance", "mobile_adjacent"),
                reason=(
                    "User allowed mobile capture for the 60-item sign target; "
                    "activate only the seven >0.44 candidates from mobile query v1.0/v1.1"
                ),
            )
            output = {
                "run_id": run_id,
                "campaign_id": campaign_id,
                "index": asdict(index_result),
                "eligibility": _jsonable(asdict(eligibility)),
                "frontier_policy_version": frontier.policy.version,
                "downloads_attempted": False,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id,
                status="failed",
                result={"stage": "sign_mobile_activation"},
            )
            raise


def run_sign_small_activate_command() -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = "sign-small-activate-" + str(uuid.uuid4())
    campaign_id = "sign_action_v1"
    query_pack_path = QUERY_PACKS[campaign_id]
    query_pack_version = _query_pack_version(query_pack_path)
    eligibility_policy = "sign-small-semantic-0.44-v1.0.0"
    discovery_versions = (
        "sign_action_v1.qp.v1.0.0",
        "sign_action_v1.qp.v1.1.0",
        "sign_action_v1.qp.v1.2.0",
    )
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        database.create_run(
            run_id,
            "sign-small-frontier-activation",
            config={
                "campaign_id": campaign_id,
                "query_pack_versions": list(discovery_versions),
                "threshold": 0.44,
                "batch_size": 5,
                "small_scale_definition": "1-5 direct sign participants",
                "downloads_enabled": False,
            },
        )
        try:
            provider = DashScopeQwenEmbeddingProvider()
            recall = CalibrationSemanticRecallService(
                database, index, provider, QWEN_SCHEMA
            )
            index_result = recall.prepare_index(run_id=run_id)
            semantic = SemanticQueryVectorService(
                database, index, provider, QWEN_SCHEMA
            )
            prepared = semantic.prepare(
                run_id=run_id,
                query_pack_path=query_pack_path,
            )
            eligibility = recall.apply_threshold_override(
                run_id=run_id,
                campaign_query_vectors={
                    campaign_id: (
                        prepared.query_pack_version,
                        {"举牌/横幅": prepared.vectors["举牌/横幅"]},
                    )
                },
                scoring_policy_version=_scoring_policy_version(),
                threshold=0.44,
                policy_version=eligibility_policy,
                required_discovery_query_pack_versions={
                    campaign_id: discovery_versions
                },
            )
            dedupe = bootstrap_safe_dedupe_policy(database, QWEN_SCHEMA)
            active = database.connection.execute(
                """
                SELECT active_frontier_policy_version FROM campaigns
                WHERE campaign_id = ?
                """,
                (campaign_id,),
            ).fetchone()
            frontier = create_user_override_frontier_policy(
                database,
                campaign_id=campaign_id,
                expected_version=active["active_frontier_policy_version"],
                query_pack_version=query_pack_version,
                embedding_schema_version=QWEN_SCHEMA.version,
                dedupe_policy_version=dedupe.version,
                semantic_eligibility_policy_version=eligibility_policy,
                threshold=0.44,
                batch_size=5,
                allowed_camera_pools=("surveillance", "mobile_adjacent"),
                reason=(
                    "User froze small-scale as 1-5 direct sign participants; "
                    "activate only current >0.44 mobile/surveillance candidates"
                ),
            )
            database.connection.execute(
                """
                INSERT INTO campaign_hold_events(
                    event_id, campaign_id, action, reason, run_id, created_at
                ) VALUES (?, ?, 'release', ?, ?, datetime('now'))
                """,
                (
                    str(uuid.uuid4()),
                    campaign_id,
                    "release only for five-item small-scale estimation pilot",
                    run_id,
                ),
            )
            output = {
                "run_id": run_id,
                "campaign_id": campaign_id,
                "index": asdict(index_result),
                "eligibility": _jsonable(asdict(eligibility)),
                "frontier_policy_version": frontier.policy.version,
                "downloads_attempted": False,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id,
                status="failed",
                result={"stage": "sign_small_activation"},
            )
            raise


def run_sign_scale_activate_command() -> dict[str, Any]:
    initialize_state(import_legacy=True)
    run_id = "sign-scale-activate-" + str(uuid.uuid4())
    campaign_id = "sign_action_v1"
    current_pack = QUERY_PACKS[campaign_id]
    current_version = _query_pack_version(current_pack)
    broad_pack = current_pack
    ensemble_packs = SIGN_QUERY_PACKS
    discovery_versions = tuple(
        _query_pack_version(path) for path in ensemble_packs
    )
    eligibility_policy = "sign-increment-multi-query-semantic-0.44-v1.0.0"
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        database.create_run(
            run_id,
            "sign-scale-frontier-activation",
            config={
                "campaign_id": campaign_id,
                "ranking_query_pack": str(broad_pack),
                "eligibility_query_packs": [str(path) for path in ensemble_packs],
                "attributed_query_pack_version": current_version,
                "threshold": 0.44,
                "batch_size": 20,
            },
        )
        try:
            provider = DashScopeQwenEmbeddingProvider()
            recall = CalibrationSemanticRecallService(
                database, index, provider, QWEN_SCHEMA
            )
            index_result = recall.prepare_index(run_id=run_id)
            semantic = SemanticQueryVectorService(
                database, index, provider, QWEN_SCHEMA
            )
            sign_vectors = []
            for path in ensemble_packs:
                database.register_frozen_query_pack(path)
                prepared = semantic.prepare(run_id=run_id, query_pack_path=path)
                sign_vectors.append(prepared.vectors["举牌/横幅"])
            eligibility = recall.apply_threshold_override(
                run_id=run_id,
                campaign_query_vectors={
                    campaign_id: (
                        current_version,
                        {"举牌/横幅": tuple(sign_vectors)},
                    )
                },
                scoring_policy_version=_scoring_policy_version(),
                threshold=0.44,
                policy_version=eligibility_policy,
                required_discovery_query_pack_versions={
                    campaign_id: discovery_versions
                },
                required_source_policy_version=_scoring_policy_version(),
            )
            dedupe = bootstrap_safe_dedupe_policy(database, QWEN_SCHEMA)
            active = database.connection.execute(
                """
                SELECT active_frontier_policy_version FROM campaigns
                WHERE campaign_id = ?
                """,
                (campaign_id,),
            ).fetchone()
            frontier = create_user_override_frontier_policy(
                database,
                campaign_id=campaign_id,
                expected_version=active["active_frontier_policy_version"],
                query_pack_version=current_version,
                embedding_schema_version=QWEN_SCHEMA.version,
                dedupe_policy_version=dedupe.version,
                semantic_eligibility_policy_version=eligibility_policy,
                threshold=0.44,
                batch_size=20,
                allowed_camera_pools=("surveillance", "mobile_adjacent"),
                feedback_rerank_policy_version="sign-human-feedback-centroid-v1.0.0",
                feedback_task_weight=0.5,
                feedback_source_weight=0.25,
                reason=(
                    "Require max similarity >=0.44 across frozen sign query vectors; "
                    "human-feedback centroid affects ranking only; small-scale numeric "
                    "and lexical counterexamples remain hard gates"
                ),
            )
            database.connection.execute(
                """
                INSERT INTO campaign_hold_events(
                    event_id, campaign_id, action, reason, run_id, created_at
                ) VALUES (?, ?, 'release', ?, ?, datetime('now'))
                """,
                (
                    str(uuid.uuid4()),
                    campaign_id,
                    "release for first twenty-item incremental convergence batch",
                    run_id,
                ),
            )
            output = {
                "run_id": run_id,
                "campaign_id": campaign_id,
                "index": asdict(index_result),
                "eligibility": _jsonable(asdict(eligibility)),
                "frontier_policy_version": frontier.policy.version,
                "downloads_attempted": False,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id,
                status="failed",
                result={"stage": "sign_scale_activation"},
            )
            raise


def run_fight_scale_activate_command() -> dict[str, Any]:
    """Activate only current-policy fight-control candidates at the frozen 0.40 gate."""

    initialize_state(import_legacy=True)
    run_id = "fight-scale-activate-" + str(uuid.uuid4())
    campaign_id = "fight_confounder_v1"
    current_pack = QUERY_PACKS[campaign_id]
    current_version = _query_pack_version(current_pack)
    discovery_versions = tuple(
        f"fight_confounder_v1.qp.v1.{minor}.0" for minor in range(11)
    )
    eligibility_policy = "fight-control-increment-semantic-0.40-v1.0.0"
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        database.create_run(
            run_id,
            "fight-control-frontier-activation",
            config={
                "campaign_id": campaign_id,
                "query_pack": str(current_pack),
                "threshold": 0.40,
                "batch_size": 20,
            },
        )
        try:
            provider = DashScopeQwenEmbeddingProvider()
            recall = CalibrationSemanticRecallService(
                database, index, provider, QWEN_SCHEMA
            )
            index_result = recall.prepare_index(run_id=run_id)
            semantic = SemanticQueryVectorService(
                database, index, provider, QWEN_SCHEMA
            )
            prepared = semantic.prepare(
                run_id=run_id, query_pack_path=current_pack
            )
            eligibility = recall.apply_threshold_override(
                run_id=run_id,
                campaign_query_vectors={
                    campaign_id: (current_version, prepared.vectors)
                },
                scoring_policy_version=_scoring_policy_version(),
                threshold=0.40,
                policy_version=eligibility_policy,
                required_discovery_query_pack_versions={
                    campaign_id: discovery_versions
                },
                required_source_policy_version=_scoring_policy_version(),
            )
            dedupe = bootstrap_safe_dedupe_policy(database, QWEN_SCHEMA)
            active = database.connection.execute(
                """
                SELECT active_frontier_policy_version FROM campaigns
                WHERE campaign_id = ?
                """,
                (campaign_id,),
            ).fetchone()
            frontier = create_user_override_frontier_policy(
                database,
                campaign_id=campaign_id,
                expected_version=active["active_frontier_policy_version"],
                query_pack_version=current_version,
                embedding_schema_version=QWEN_SCHEMA.version,
                dedupe_policy_version=dedupe.version,
                semantic_eligibility_policy_version=eligibility_policy,
                threshold=0.40,
                batch_size=20,
                allowed_camera_pools=("surveillance",),
                reason=(
                    "Expand only the frozen non-attack fight-control campaign; "
                    "the original 0.40 semantic and surveillance source gates remain"
                ),
            )
            database.connection.execute(
                """
                INSERT INTO campaign_hold_events(
                    event_id, campaign_id, action, reason, run_id, created_at
                ) VALUES (?, ?, 'release', ?, ?, datetime('now'))
                """,
                (
                    str(uuid.uuid4()),
                    campaign_id,
                    "release versioned fight-control discovery pool only",
                    run_id,
                ),
            )
            output = {
                "run_id": run_id,
                "campaign_id": campaign_id,
                "index": asdict(index_result),
                "eligibility": _jsonable(asdict(eligibility)),
                "frontier_policy_version": frontier.policy.version,
                "downloads_attempted": False,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id,
                status="failed",
                result={"stage": "fight_control_activation"},
            )
            raise


def run_fight_positive_activate_command() -> dict[str, Any]:
    """Activate the independent frozen real-fight campaign at the 0.40 gate."""

    initialize_state(import_legacy=True)
    run_id = "fight-positive-activate-" + str(uuid.uuid4())
    campaign_id = "fight_positive_v1"
    current_pack = QUERY_PACKS[campaign_id]
    current_version = _query_pack_version(current_pack)
    eligibility_policy = "fight-positive-semantic-0.40-v1.0.0"
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        database.create_run(
            run_id,
            "fight-positive-frontier-activation",
            config={
                "campaign_id": campaign_id,
                "query_pack": str(current_pack),
                "threshold": 0.40,
                "batch_size": 20,
            },
        )
        try:
            provider = DashScopeQwenEmbeddingProvider()
            recall = CalibrationSemanticRecallService(
                database, index, provider, QWEN_SCHEMA
            )
            index_result = recall.prepare_index(run_id=run_id)
            semantic = SemanticQueryVectorService(
                database, index, provider, QWEN_SCHEMA
            )
            prepared = semantic.prepare(
                run_id=run_id, query_pack_path=current_pack
            )
            eligibility = recall.apply_threshold_override(
                run_id=run_id,
                campaign_query_vectors={
                    campaign_id: (current_version, prepared.vectors)
                },
                scoring_policy_version=_scoring_policy_version(),
                threshold=0.40,
                policy_version=eligibility_policy,
                required_discovery_query_pack_versions={
                    campaign_id: (current_version,)
                },
                required_source_policy_version=_scoring_policy_version(),
            )
            dedupe = bootstrap_safe_dedupe_policy(database, QWEN_SCHEMA)
            active = database.connection.execute(
                """
                SELECT active_frontier_policy_version FROM campaigns
                WHERE campaign_id = ?
                """,
                (campaign_id,),
            ).fetchone()
            frontier = create_user_override_frontier_policy(
                database,
                campaign_id=campaign_id,
                expected_version=active["active_frontier_policy_version"],
                query_pack_version=current_version,
                embedding_schema_version=QWEN_SCHEMA.version,
                dedupe_policy_version=dedupe.version,
                semantic_eligibility_policy_version=eligibility_policy,
                threshold=0.40,
                batch_size=20,
                allowed_camera_pools=("surveillance",),
                reason=(
                    "Independent frozen real-fight campaign; fixed surveillance "
                    "source and original 0.40 semantic gates remain mandatory"
                ),
            )
            database.connection.execute(
                """
                INSERT INTO campaign_hold_events(
                    event_id, campaign_id, action, reason, run_id, created_at
                ) VALUES (?, ?, 'release', ?, ?, datetime('now'))
                """,
                (
                    str(uuid.uuid4()),
                    campaign_id,
                    "release independent fight-positive v1 pool only",
                    run_id,
                ),
            )
            output = {
                "run_id": run_id,
                "campaign_id": campaign_id,
                "index": asdict(index_result),
                "eligibility": _jsonable(asdict(eligibility)),
                "frontier_policy_version": frontier.policy.version,
                "downloads_attempted": False,
            }
            database.finish_run(run_id, status="completed", result=output)
            return output
        except Exception:
            database.finish_run(
                run_id,
                status="failed",
                result={"stage": "fight_positive_activation"},
            )
            raise


def run_batch_command(args) -> dict[str, Any]:
    initialize_state(import_legacy=True)
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        run_id = args.run_id or "campaign-" + str(uuid.uuid4())
        existing = database.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if existing is None:
            frontier = get_frontier_policy(database, args.campaign)
            campaign = get_campaign_policy(database, args.campaign)
            database.create_run(
                run_id,
                "calibrated-campaign",
                config={
                    "campaign_id": args.campaign,
                    "campaign_policy_version": campaign.version,
                    "frontier_policy_version": frontier.policy.version,
                    "embedding_schema_version": QWEN_SCHEMA.version,
                    "dedupe_policy_version": frontier.dedupe_policy_version,
                    "downloads_enabled": args.enable_downloads,
                },
            )
        elif existing["status"] != "running":
            raise ValueError("--run-id must refer to a running campaign run")
        frontier = get_frontier_policy(database, args.campaign)
        dedupe = get_dedupe_policy(database, frontier.dedupe_policy_version)
        provider = DashScopeQwenEmbeddingProvider()
        semantic = SemanticQueryVectorService(
            database,
            index,
            provider,
            QWEN_SCHEMA,
        )
        worker = None
        if args.enable_downloads:
            if args.confirm_downloads != "DOWNLOAD":
                raise ValueError("real downloads require --confirm-downloads DOWNLOAD")
            worker = SerialDownloadWorker(
                database,
                _adapters(tuple(args.peertube_instance)),
                DownloadWorkerConfig(
                    internal_root=INTERNAL_ROOT,
                    output_root=OUTPUT_ROOT,
                ),
                rng=random.Random(args.random_seed),
            )
        batch_path = (
            INTERNAL_ROOT
            / "runs"
            / run_id
            / "secondary-batches"
            / f"{args.campaign}.jsonl"
        )
        summary = run_calibrated_iteration(
            database,
            index,
            semantic,
            QWEN_SCHEMA,
            dedupe,
            run_id=run_id,
            campaign_id=args.campaign,
            query_pack_path=QUERY_PACKS[args.campaign],
            batch_output_path=batch_path,
            download_worker=worker,
            enable_downloads=args.enable_downloads,
        )
        result = _jsonable(asdict(summary))
        result["run_id"] = run_id
        if args.enable_downloads:
            manifest_path = OUTPUT_ROOT / args.campaign / "manifest.jsonl"
            result["manifest_path"] = str(manifest_path)
            result["manifest_record_count"] = export_campaign_manifest(
                database,
                args.campaign,
                manifest_path,
            )
        if args.enable_downloads and args.review:
            # Review export is explicitly opt-in.  Download-only operation keeps
            # SQLite/Manifest as the delivery record and leaves labelling to the
            # user's external platform.
            review = export_pilot_review(
                database,
                OUTPUT_ROOT / f"pilot_review_{args.campaign}_{run_id}.html",
                campaign_ids=(args.campaign,),
                run_ids=(run_id,),
            )
            result["pilot_review"] = _jsonable(asdict(review))
        if args.pilot and summary.status == "downloads_processed":
            result["pilot_stopped_after_one_batch"] = True
            database.finish_run(run_id, status="completed", result=result)
        elif summary.status in {"completed", "stopped", "frontier_exhausted"}:
            database.finish_run(
                run_id,
                status="completed" if summary.status == "completed" else "stopped",
                result=result,
            )
        return result


def status_report() -> dict[str, Any]:
    initialize_state(import_legacy=True)
    with CandidateDatabase(STATE_DB) as database:
        database.initialize()
        return {
            "state_db": str(STATE_DB),
            "candidates": _count(database, "candidates"),
            "legacy_downloads": _count(database, "legacy_downloads"),
            "uploader_priors": _count(database, "uploader_priors"),
            "source_qualified": int(
                database.connection.execute(
                    "SELECT COUNT(*) FROM candidates WHERE status = 'source_qualified'"
                ).fetchone()[0]
            ),
            "resource_eligible": int(
                database.connection.execute(
                    "SELECT COUNT(*) FROM candidates WHERE resource_eligible = 1"
                ).fetchone()[0]
            ),
            "open_batches": int(
                database.connection.execute(
                    """
                    SELECT COUNT(*) FROM secondary_batches
                    WHERE status IN ('open', 'reviewed', 'queued')
                    """
                ).fetchone()[0]
            ),
            "running_runs": int(
                database.connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE status = 'running'"
                ).fetchone()[0]
            ),
            "campaigns": [
                dict(row)
                for row in database.connection.execute(
                    "SELECT * FROM campaigns ORDER BY campaign_id"
                ).fetchall()
            ],
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("init")
    initialize.add_argument("--skip-legacy", action="store_true")
    subparsers.add_parser("status")

    discover = subparsers.add_parser("discover")
    discover.add_argument("--campaign", required=True, choices=sorted(QUERY_PACKS))
    discover.add_argument("--peertube-instance", required=True, action="append")
    discover.add_argument("--probe-limit", type=int, default=150)
    discover.add_argument("--run-id")

    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--campaign", required=True, choices=sorted(QUERY_PACKS))
    calibrate.add_argument("--labels", required=True, type=Path)
    calibrate.add_argument("--probe-limit", type=int, default=150)
    calibrate.add_argument("--batch-size", type=int, default=20)
    subparsers.add_parser("rescore")
    semantic_recall = subparsers.add_parser("semantic-recall")
    semantic_recall.add_argument("--top-n", type=int, default=50)
    semantic_override = subparsers.add_parser("semantic-override")
    semantic_override.add_argument("--threshold", type=float, default=0.40)
    semantic_override.add_argument("--batch-size", type=int, default=5)
    semantic_override.add_argument("--policy-version")
    pilot_feedback = subparsers.add_parser("pilot-feedback")
    pilot_feedback.add_argument("--feedback", required=True, type=Path)
    pilot_feedback_import = subparsers.add_parser("pilot-feedback-import")
    pilot_feedback_import.add_argument("--feedback", required=True, type=Path)
    campaign_hold = subparsers.add_parser("campaign-hold")
    campaign_hold.add_argument("--campaign", required=True, choices=sorted(QUERY_PACKS))
    campaign_hold.add_argument("--action", required=True, choices=("hold", "release"))
    campaign_hold.add_argument("--reason", required=True)
    download_only_capacity = subparsers.add_parser("download-only-capacity")
    download_only_capacity.add_argument("--campaign", required=True, choices=sorted(QUERY_PACKS))
    download_only_capacity.add_argument("--candidate-budget", type=int, required=True)
    download_only_capacity.add_argument("--reason", required=True)
    subparsers.add_parser("sign-expand-capacity")
    subparsers.add_parser("sign-focus")
    subparsers.add_parser("sign-mobile-activate")
    subparsers.add_parser("sign-small-activate")
    subparsers.add_parser("sign-scale-activate")
    subparsers.add_parser("fight-scale-activate")
    subparsers.add_parser("fight-positive-activate")

    batch = subparsers.add_parser("batch")
    batch.add_argument("--campaign", required=True, choices=sorted(QUERY_PACKS))
    batch.add_argument("--run-id")
    batch.add_argument("--enable-downloads", action="store_true")
    batch.add_argument("--confirm-downloads")
    batch.add_argument(
        "--review",
        action="store_true",
        help="also export a local review page; omitted for download-only delivery",
    )
    batch.add_argument("--peertube-instance", action="append", default=[])
    batch.add_argument("--random-seed", type=int, default=0)
    batch.add_argument("--pilot", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_state(import_legacy=not args.skip_legacy)
        elif args.command == "status":
            result = status_report()
        elif args.command == "discover":
            result = run_discovery_command(args)
        elif args.command == "calibrate":
            result = run_calibration_command(args)
        elif args.command == "rescore":
            result = run_rescore_command()
        elif args.command == "semantic-recall":
            result = run_semantic_recall_command(args)
        elif args.command == "semantic-override":
            result = run_semantic_override_command(args)
        elif args.command == "pilot-feedback":
            result = run_pilot_feedback_command(args)
        elif args.command == "pilot-feedback-import":
            result = run_pilot_feedback_import_command(args)
        elif args.command == "campaign-hold":
            result = run_campaign_hold_command(args)
        elif args.command == "download-only-capacity":
            result = run_download_only_capacity_command(args)
        elif args.command == "sign-expand-capacity":
            result = run_sign_expand_capacity_command()
        elif args.command == "sign-focus":
            result = run_sign_focus_command()
        elif args.command == "sign-mobile-activate":
            result = run_sign_mobile_activate_command()
        elif args.command == "sign-small-activate":
            result = run_sign_small_activate_command()
        elif args.command == "sign-scale-activate":
            result = run_sign_scale_activate_command()
        elif args.command == "fight-scale-activate":
            result = run_fight_scale_activate_command()
        elif args.command == "fight-positive-activate":
            result = run_fight_positive_activate_command()
        else:
            result = run_batch_command(args)
    except Exception as error:
        print(
            json.dumps(
                {"ok": False, "error": str(error)},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps({"ok": True, **_jsonable(result)}, ensure_ascii=False, indent=2))
    return 0


def _adapters(peertube_hosts: tuple[str, ...]):
    return {
        "youtube": YouTubeAdapter(),
        "dailymotion": DailymotionAdapter(),
        "peertube": PeerTubeAdapter(allowed_instance_hosts=peertube_hosts),
    }


def _query_pack_version(path: Path) -> str:
    document = json.loads(path.read_text(encoding="utf-8"))
    return str(document["query_pack_version"])


def _scoring_policy_version() -> str:
    document = json.loads(SCORING_POLICY.read_text(encoding="utf-8"))
    return str(document["policy_version"])


def _count(database: CandidateDatabase, table: str) -> int:
    allowed = {"candidates", "legacy_downloads", "uploader_priors"}
    if table not in allowed:
        raise ValueError("unsupported status table")
    return int(database.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
