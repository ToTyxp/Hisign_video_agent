"""Immutable campaign/frontier policy APIs with optimistic version checks."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Mapping

from surveillance_video_agent.batch_generator import CampaignPolicy, FrontierPolicy
from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.dedupe import DedupePolicy, register_dedupe_policy
from surveillance_video_agent.embedding import EmbeddingSchema


DEFAULT_CAMPAIGN_POLICIES = (
    CampaignPolicy(
        campaign_id="demand_action_v1",
        version="demand_action_v1.capacity.v1.0.0",
        subtype_limits=(("举牌/横幅", 20), ("下跪", 15), ("静坐", 15)),
        max_candidates=50,
    ),
    CampaignPolicy(
        campaign_id="fight_confounder_v1",
        version="fight_confounder_v1.capacity.v1.0.0",
        subtype_limits=(
            ("冲突但未攻击", 13),
            ("舞蹈/玩闹/训练", 13),
            ("非攻击性身体接触", 12),
            ("场景先验", 12),
        ),
        max_candidates=50,
    ),
    CampaignPolicy(
        campaign_id="sign_action_v1",
        version="sign_action_v1.capacity.v1.0.0",
        subtype_limits=(("举牌/横幅", 60),),
        max_candidates=60,
    ),
)


@dataclass(frozen=True, slots=True)
class FrontierPolicyRecord:
    campaign_id: str
    policy: FrontierPolicy
    probe_budget: int
    embedding_schema_version: str
    dedupe_policy_version: str
    calibration_id: str
    low_yield_threshold: float
    low_yield_consecutive_windows: int
    low_yield_partition_window_size: int
    created_at: str
    created_by: str
    reason: str


def bootstrap_safe_dedupe_policy(
    database: CandidateDatabase,
    schema: EmbeddingSchema,
) -> DedupePolicy:
    """Register identity/SHA-only operation until vector thresholds are calibrated."""

    policy = DedupePolicy(
        version=f"dedupe-vector-disabled-{schema.version}",
        similarity_threshold=1.0,
        title_similarity_threshold=1.0,
        duration_tolerance_seconds=0.0,
        neighbor_limit=1,
        vector_enabled=False,
    )
    database.register_embedding_schema(schema)
    register_dedupe_policy(database, schema, policy)
    return policy


def bootstrap_default_campaign_policies(
    database: CandidateDatabase,
    *,
    created_by: str = "system",
) -> tuple[CampaignPolicy, ...]:
    now = utc_now()
    with database.transaction() as connection:
        for policy in DEFAULT_CAMPAIGN_POLICIES:
            connection.execute(
                """
                INSERT OR IGNORE INTO campaigns(campaign_id, created_at)
                VALUES (?, ?)
                """,
                (policy.campaign_id, now),
            )
            subtype_json = _limits_json(policy.subtype_limits)
            existing = connection.execute(
                """
                SELECT subtype_limits_json, max_candidates
                FROM campaign_policy_versions
                WHERE campaign_id = ? AND policy_version = ?
                """,
                (policy.campaign_id, policy.version),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO campaign_policy_versions(
                        campaign_id, policy_version, subtype_limits_json,
                        max_candidates, created_at, created_by, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, 'frozen v1 default capacity')
                    """,
                    (
                        policy.campaign_id,
                        policy.version,
                        subtype_json,
                        policy.max_candidates,
                        now,
                        created_by,
                    ),
                )
            elif tuple(existing) != (subtype_json, policy.max_candidates):
                raise ValueError("default campaign policy version changed content")
            connection.execute(
                """
                UPDATE campaigns SET active_policy_version = COALESCE(
                    active_policy_version, ?
                ) WHERE campaign_id = ?
                """,
                (policy.version, policy.campaign_id),
            )
    return DEFAULT_CAMPAIGN_POLICIES


def get_campaign_policy(
    database: CandidateDatabase,
    campaign_id: str,
    policy_version: str | None = None,
) -> CampaignPolicy:
    if policy_version is None:
        campaign = database.connection.execute(
            "SELECT active_policy_version FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if campaign is None or campaign["active_policy_version"] is None:
            raise ValueError(f"campaign has no active capacity policy: {campaign_id}")
        policy_version = campaign["active_policy_version"]
    row = database.connection.execute(
        """
        SELECT * FROM campaign_policy_versions
        WHERE campaign_id = ? AND policy_version = ?
        """,
        (campaign_id, policy_version),
    ).fetchone()
    if row is None:
        raise ValueError("campaign policy version not found")
    limits = json.loads(row["subtype_limits_json"])
    if not isinstance(limits, dict):
        raise RuntimeError("stored campaign subtype limits are invalid")
    return CampaignPolicy(
        campaign_id=campaign_id,
        version=policy_version,
        subtype_limits=tuple((str(key), int(value)) for key, value in limits.items()),
        max_candidates=int(row["max_candidates"]),
    )


def update_campaign_policy(
    database: CandidateDatabase,
    *,
    campaign_id: str,
    expected_version: str,
    subtype_limits: Mapping[str, int],
    max_candidates: int,
    reason: str,
    created_by: str = "user",
) -> CampaignPolicy:
    if not reason:
        raise ValueError("campaign policy update reason is required")
    version = f"{campaign_id}.capacity.{uuid.uuid4()}"
    policy = CampaignPolicy(
        campaign_id=campaign_id,
        version=version,
        subtype_limits=tuple(subtype_limits.items()),
        max_candidates=max_candidates,
    )
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT active_policy_version FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None or row["active_policy_version"] != expected_version:
            raise ValueError("campaign policy expected_version conflict")
        connection.execute(
            """
            INSERT INTO campaign_policy_versions(
                campaign_id, policy_version, subtype_limits_json,
                max_candidates, created_at, created_by, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                version,
                _limits_json(policy.subtype_limits),
                max_candidates,
                utc_now(),
                created_by,
                reason,
            ),
        )
        connection.execute(
            "UPDATE campaigns SET active_policy_version = ? WHERE campaign_id = ?",
            (version, campaign_id),
        )
    return policy


def get_frontier_policy(
    database: CandidateDatabase,
    campaign_id: str,
    frontier_policy_version: str | None = None,
) -> FrontierPolicyRecord:
    if frontier_policy_version is None:
        campaign = database.connection.execute(
            "SELECT active_frontier_policy_version FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if campaign is None or campaign["active_frontier_policy_version"] is None:
            raise ValueError(f"campaign has no calibrated frontier policy: {campaign_id}")
        frontier_policy_version = campaign["active_frontier_policy_version"]
    row = database.connection.execute(
        """
        SELECT * FROM frontier_policy_versions
        WHERE campaign_id = ? AND frontier_policy_version = ?
        """,
        (campaign_id, frontier_policy_version),
    ).fetchone()
    if row is None:
        raise ValueError("frontier policy version not found")
    payload = json.loads(row["policy_json"])
    thresholds = payload.get("semantic_score_thresholds")
    if not isinstance(thresholds, dict) or not thresholds:
        raise RuntimeError("frontier policy has no calibrated subtype thresholds")
    policy = FrontierPolicy(
        version=frontier_policy_version,
        batch_size=int(payload["batch_size"]),
        vector_oversample_factor=int(payload["vector_oversample_factor"]),
        semantic_score_threshold=None,
        semantic_score_thresholds=tuple(
            (str(key), float(value)) for key, value in thresholds.items()
        ),
        rrf_k=int(payload["rrf_k"]),
        uploader_cap=int(payload["uploader_cap"]),
        lease_seconds=int(payload["lease_seconds"]),
        low_yield_threshold=float(payload["low_yield_threshold"]),
        low_yield_consecutive_windows=int(payload["low_yield_consecutive_windows"]),
        low_yield_partition_window_size=int(payload["low_yield_partition_window_size"]),
        feedback_rerank_policy_version=payload.get("feedback_rerank_policy_version"),
        feedback_task_weight=float(payload.get("feedback_task_weight", 0.0)),
        feedback_source_weight=float(payload.get("feedback_source_weight", 0.0)),
    )
    return FrontierPolicyRecord(
        campaign_id=campaign_id,
        policy=policy,
        probe_budget=int(payload["probe_budget"]),
        embedding_schema_version=str(payload["embedding_schema_version"]),
        dedupe_policy_version=str(payload["dedupe_policy_version"]),
        calibration_id=str(payload["calibration_id"]),
        low_yield_threshold=float(payload["low_yield_threshold"]),
        low_yield_consecutive_windows=int(payload["low_yield_consecutive_windows"]),
        low_yield_partition_window_size=int(payload["low_yield_partition_window_size"]),
        created_at=row["created_at"],
        created_by=row["created_by"],
        reason=row["reason"],
    )


def update_frontier_policy(
    database: CandidateDatabase,
    *,
    campaign_id: str,
    expected_version: str | None,
    calibration_id: str,
    probe_budget: int,
    batch_size: int,
    vector_oversample_factor: int,
    embedding_schema_version: str,
    rrf_k: int,
    dedupe_policy_version: str,
    low_yield_threshold: float,
    low_yield_consecutive_windows: int,
    low_yield_partition_window_size: int,
    reason: str,
    created_by: str = "user",
    uploader_cap: int = 5,
    lease_seconds: int = 900,
) -> FrontierPolicyRecord:
    if not reason:
        raise ValueError("frontier policy update reason is required")
    if not 1 <= probe_budget <= 150:
        raise ValueError("probe budget must be between 1 and 150")
    if not 0 < low_yield_threshold < 1:
        raise ValueError("low yield threshold must be between 0 and 1")
    if low_yield_consecutive_windows <= 0 or low_yield_partition_window_size <= 0:
        raise ValueError("low yield window settings must be positive")
    calibration = database.connection.execute(
        """
        SELECT * FROM threshold_calibrations
        WHERE calibration_id = ? AND campaign_id = ?
          AND embedding_schema_version = ? AND status = 'passed'
        """,
        (calibration_id, campaign_id, embedding_schema_version),
    ).fetchone()
    if calibration is None:
        raise ValueError("a passed calibration for this campaign/schema is required")
    thresholds = json.loads(calibration["thresholds_json"])
    campaign_policy = get_campaign_policy(database, campaign_id)
    if set(thresholds) != {name for name, _ in campaign_policy.subtype_limits}:
        raise ValueError("calibration thresholds do not cover active campaign subtypes")
    dedupe = database.connection.execute(
        """
        SELECT embedding_schema_version FROM dedupe_policy_versions
        WHERE dedupe_policy_version = ?
        """,
        (dedupe_policy_version,),
    ).fetchone()
    if dedupe is None or dedupe["embedding_schema_version"] != embedding_schema_version:
        raise ValueError("dedupe policy does not match embedding schema")
    version = f"{campaign_id}.frontier.{uuid.uuid4()}"
    policy = FrontierPolicy(
        version=version,
        batch_size=batch_size,
        vector_oversample_factor=vector_oversample_factor,
        semantic_score_threshold=None,
        semantic_score_thresholds=tuple(
            (str(key), float(value)) for key, value in thresholds.items()
        ),
        rrf_k=rrf_k,
        uploader_cap=uploader_cap,
        lease_seconds=lease_seconds,
        low_yield_threshold=low_yield_threshold,
        low_yield_consecutive_windows=low_yield_consecutive_windows,
        low_yield_partition_window_size=low_yield_partition_window_size,
    )
    payload = {
        "batch_size": batch_size,
        "vector_oversample_factor": vector_oversample_factor,
        "semantic_score_thresholds": dict(policy.semantic_score_thresholds),
        "rrf_k": rrf_k,
        "uploader_cap": uploader_cap,
        "lease_seconds": lease_seconds,
        "probe_budget": probe_budget,
        "embedding_schema_version": embedding_schema_version,
        "dedupe_policy_version": dedupe_policy_version,
        "calibration_id": calibration_id,
        "low_yield_threshold": low_yield_threshold,
        "low_yield_consecutive_windows": low_yield_consecutive_windows,
        "low_yield_partition_window_size": low_yield_partition_window_size,
    }
    with database.transaction() as connection:
        campaign = connection.execute(
            """
            SELECT active_frontier_policy_version FROM campaigns
            WHERE campaign_id = ?
            """,
            (campaign_id,),
        ).fetchone()
        active = campaign["active_frontier_policy_version"] if campaign else None
        if campaign is None or active != expected_version:
            raise ValueError("frontier policy expected_version conflict")
        connection.execute(
            """
            INSERT INTO frontier_policy_versions(
                campaign_id, frontier_policy_version, policy_json,
                created_at, created_by, reason
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                version,
                _json(payload),
                utc_now(),
                created_by,
                reason,
            ),
        )
        connection.execute(
            """
            UPDATE campaigns SET active_frontier_policy_version = ?
            WHERE campaign_id = ?
            """,
            (version, campaign_id),
        )
    return get_frontier_policy(database, campaign_id, version)


def create_user_override_frontier_policy(
    database: CandidateDatabase,
    *,
    campaign_id: str,
    expected_version: str | None,
    query_pack_version: str,
    embedding_schema_version: str,
    dedupe_policy_version: str,
    semantic_eligibility_policy_version: str,
    threshold: float,
    reason: str,
    probe_budget: int = 150,
    batch_size: int = 20,
    vector_oversample_factor: int = 5,
    rrf_k: int = 60,
    uploader_cap: int = 5,
    lease_seconds: int = 900,
    allowed_camera_pools: tuple[str, ...] = ("surveillance",),
    feedback_rerank_policy_version: str | None = None,
    feedback_task_weight: float = 0.0,
    feedback_source_weight: float = 0.0,
) -> FrontierPolicyRecord:
    if not reason or not 0 <= threshold <= 1:
        raise ValueError("user override requires reason and bounded threshold")
    if not allowed_camera_pools or not set(allowed_camera_pools) <= {
        "surveillance",
        "mobile_adjacent",
    }:
        raise ValueError("user override camera pools are invalid")
    campaign_policy = get_campaign_policy(database, campaign_id)
    subtypes = [name for name, _ in campaign_policy.subtype_limits]
    for subtype in subtypes:
        exists = database.connection.execute(
            """
            SELECT 1 FROM semantic_task_eligibility
            WHERE campaign_id = ? AND subtype = ? AND query_pack_version = ?
              AND embedding_schema_version = ? AND policy_version = ?
              AND threshold = ? LIMIT 1
            """,
            (
                campaign_id,
                subtype,
                query_pack_version,
                embedding_schema_version,
                semantic_eligibility_policy_version,
                threshold,
            ),
        ).fetchone()
        if exists is None:
            raise ValueError(f"semantic override has no candidates for subtype: {subtype}")
    dedupe = database.connection.execute(
        """
        SELECT embedding_schema_version FROM dedupe_policy_versions
        WHERE dedupe_policy_version = ?
        """,
        (dedupe_policy_version,),
    ).fetchone()
    if dedupe is None or dedupe["embedding_schema_version"] != embedding_schema_version:
        raise ValueError("dedupe policy does not match override embedding schema")
    version = f"{campaign_id}.frontier.user-override.{uuid.uuid4()}"
    thresholds = {subtype: threshold for subtype in subtypes}
    policy = FrontierPolicy(
        version=version,
        batch_size=batch_size,
        vector_oversample_factor=vector_oversample_factor,
        semantic_score_threshold=None,
        semantic_score_thresholds=tuple(thresholds.items()),
        rrf_k=rrf_k,
        uploader_cap=uploader_cap,
        lease_seconds=lease_seconds,
        low_yield_threshold=0.10,
        low_yield_consecutive_windows=3,
        low_yield_partition_window_size=20,
        feedback_rerank_policy_version=feedback_rerank_policy_version,
        feedback_task_weight=feedback_task_weight,
        feedback_source_weight=feedback_source_weight,
    )
    payload = {
        "batch_size": batch_size,
        "vector_oversample_factor": vector_oversample_factor,
        "semantic_score_threshold": None,
        "semantic_score_thresholds": thresholds,
        "rrf_k": rrf_k,
        "uploader_cap": uploader_cap,
        "lease_seconds": lease_seconds,
        "probe_budget": probe_budget,
        "embedding_schema_version": embedding_schema_version,
        "dedupe_policy_version": dedupe_policy_version,
        "calibration_id": f"user_override:{semantic_eligibility_policy_version}",
        "threshold_origin": "explicit_user_override",
        "semantic_eligibility_policy_version": semantic_eligibility_policy_version,
        "query_pack_version": query_pack_version,
        "allowed_camera_pools": list(allowed_camera_pools),
        "low_yield_threshold": 0.10,
        "low_yield_consecutive_windows": 3,
        "low_yield_partition_window_size": 20,
        "feedback_rerank_policy_version": feedback_rerank_policy_version,
        "feedback_task_weight": feedback_task_weight,
        "feedback_source_weight": feedback_source_weight,
    }
    with database.transaction() as connection:
        row = connection.execute(
            """
            SELECT active_frontier_policy_version FROM campaigns
            WHERE campaign_id = ?
            """,
            (campaign_id,),
        ).fetchone()
        if row is None or row["active_frontier_policy_version"] != expected_version:
            raise ValueError("frontier user override expected_version conflict")
        connection.execute(
            """
            INSERT INTO frontier_policy_versions(
                campaign_id, frontier_policy_version, policy_json,
                created_at, created_by, reason
            ) VALUES (?, ?, ?, ?, 'user', ?)
            """,
            (campaign_id, version, _json(payload), utc_now(), reason),
        )
        connection.execute(
            """
            UPDATE campaigns SET active_frontier_policy_version = ?
            WHERE campaign_id = ?
            """,
            (version, campaign_id),
        )
    return get_frontier_policy(database, campaign_id, version)


def create_focused_frontier_policy(
    database: CandidateDatabase,
    *,
    campaign_id: str,
    expected_version: str | None,
    focused_subtype: str,
    query_pack_version: str,
    embedding_schema_version: str,
    dedupe_policy_version: str,
    semantic_eligibility_policy_version: str,
    threshold: float,
    reason: str,
    batch_size: int = 5,
) -> FrontierPolicyRecord:
    if not reason or not 0 <= threshold <= 1:
        raise ValueError("focused frontier requires a reason and bounded threshold")
    campaign = get_campaign_policy(database, campaign_id)
    subtypes = [name for name, _ in campaign.subtype_limits]
    if focused_subtype not in subtypes:
        raise ValueError("focused subtype is not in the active campaign")
    exists = database.connection.execute(
        """
        SELECT 1 FROM semantic_task_eligibility
        WHERE campaign_id = ? AND subtype = ? AND query_pack_version = ?
          AND embedding_schema_version = ? AND policy_version = ?
          AND threshold = ? LIMIT 1
        """,
        (
            campaign_id,
            focused_subtype,
            query_pack_version,
            embedding_schema_version,
            semantic_eligibility_policy_version,
            threshold,
        ),
    ).fetchone()
    if exists is None:
        raise ValueError("focused semantic policy has no eligible candidates")
    dedupe = database.connection.execute(
        """
        SELECT embedding_schema_version FROM dedupe_policy_versions
        WHERE dedupe_policy_version = ?
        """,
        (dedupe_policy_version,),
    ).fetchone()
    if dedupe is None or dedupe["embedding_schema_version"] != embedding_schema_version:
        raise ValueError("dedupe policy does not match focused embedding schema")
    version = f"{campaign_id}.frontier.focused.{uuid.uuid4()}"
    thresholds = {
        subtype: threshold if subtype == focused_subtype else 1.0
        for subtype in subtypes
    }
    payload = {
        "batch_size": batch_size,
        "vector_oversample_factor": 5,
        "semantic_score_threshold": None,
        "semantic_score_thresholds": thresholds,
        "rrf_k": 60,
        "uploader_cap": 5,
        "lease_seconds": 900,
        "probe_budget": 150,
        "embedding_schema_version": embedding_schema_version,
        "dedupe_policy_version": dedupe_policy_version,
        "calibration_id": f"focused:{semantic_eligibility_policy_version}",
        "threshold_origin": "focused_user_direction",
        "focused_subtypes": [focused_subtype],
        "semantic_eligibility_policy_version": semantic_eligibility_policy_version,
        "query_pack_version": query_pack_version,
        "low_yield_threshold": 0.10,
        "low_yield_consecutive_windows": 3,
        "low_yield_partition_window_size": 20,
    }
    with database.transaction() as connection:
        row = connection.execute(
            """
            SELECT active_frontier_policy_version FROM campaigns
            WHERE campaign_id = ?
            """,
            (campaign_id,),
        ).fetchone()
        if row is None or row["active_frontier_policy_version"] != expected_version:
            raise ValueError("focused frontier expected_version conflict")
        connection.execute(
            """
            INSERT INTO frontier_policy_versions(
                campaign_id, frontier_policy_version, policy_json,
                created_at, created_by, reason
            ) VALUES (?, ?, ?, ?, 'user', ?)
            """,
            (campaign_id, version, _json(payload), utc_now(), reason),
        )
        connection.execute(
            """
            UPDATE campaigns SET active_frontier_policy_version = ?
            WHERE campaign_id = ?
            """,
            (version, campaign_id),
        )
    return get_frontier_policy(database, campaign_id, version)


def _json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _limits_json(values) -> str:
    return json.dumps(
        dict(values),
        ensure_ascii=False,
        separators=(",", ":"),
    )
