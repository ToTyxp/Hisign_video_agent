"""Top-K vector retrieval, RRF ranking, and fair Secondary Batch generation."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.vector_index import QdrantVectorIndex
from surveillance_video_agent.yield_control import record_batch_yield


@dataclass(frozen=True, slots=True)
class CampaignPolicy:
    campaign_id: str
    version: str
    subtype_limits: tuple[tuple[str, int], ...]
    max_candidates: int

    def __post_init__(self) -> None:
        if not self.campaign_id or not self.version or not self.subtype_limits:
            raise ValueError("campaign policy identity and subtype limits are required")
        if len({name for name, _ in self.subtype_limits}) != len(self.subtype_limits):
            raise ValueError("campaign subtypes must be unique")
        if any(not name or limit < 0 for name, limit in self.subtype_limits):
            raise ValueError("campaign subtype limits must be non-negative")
        if self.max_candidates < 0 or sum(limit for _, limit in self.subtype_limits) > self.max_candidates:
            raise ValueError("subtype limits exceed campaign max_candidates")


@dataclass(frozen=True, slots=True)
class FrontierPolicy:
    version: str
    batch_size: int = 20
    vector_oversample_factor: int = 5
    semantic_score_threshold: float | None = 0.0
    semantic_score_thresholds: tuple[tuple[str, float], ...] = ()
    rrf_k: int = 60
    uploader_cap: int = 5
    lease_seconds: int = 900
    low_yield_threshold: float = 0.10
    low_yield_consecutive_windows: int = 3
    low_yield_partition_window_size: int = 20
    feedback_rerank_policy_version: str | None = None
    feedback_task_weight: float = 0.0
    feedback_source_weight: float = 0.0
    semantic_eligibility_policy_version: str | None = None

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("frontier policy version is required")
        if not 1 <= self.batch_size <= 20:
            raise ValueError("batch_size must be between 1 and 20")
        if self.vector_oversample_factor <= 0 or self.rrf_k <= 0:
            raise ValueError("oversample factor and rrf_k must be positive")
        if self.semantic_score_threshold is not None and not 0 <= self.semantic_score_threshold <= 1:
            raise ValueError("semantic threshold must be between 0 and 1")
        if len({name for name, _ in self.semantic_score_thresholds}) != len(
            self.semantic_score_thresholds
        ):
            raise ValueError("semantic subtype thresholds must be unique")
        if any(
            not name or not 0 <= threshold <= 1
            for name, threshold in self.semantic_score_thresholds
        ):
            raise ValueError("semantic subtype thresholds must be between 0 and 1")
        if self.semantic_score_threshold is None and not self.semantic_score_thresholds:
            raise ValueError("semantic thresholds are required")
        if self.uploader_cap <= 0 or self.lease_seconds <= 0:
            raise ValueError("uploader cap and lease duration must be positive")
        if not 0 < self.low_yield_threshold < 1:
            raise ValueError("low yield threshold must be between 0 and 1")
        if (
            self.low_yield_consecutive_windows <= 0
            or self.low_yield_partition_window_size <= 0
        ):
            raise ValueError("low yield windows must be positive")
        if self.feedback_task_weight < 0 or self.feedback_source_weight < 0:
            raise ValueError("feedback rerank weights must be non-negative")
        if (
            self.feedback_task_weight > 0 or self.feedback_source_weight > 0
        ) and not self.feedback_rerank_policy_version:
            raise ValueError("weighted feedback rerank requires a policy version")

    def threshold_for(self, subtype: str) -> float:
        configured = dict(self.semantic_score_thresholds)
        if subtype in configured:
            return configured[subtype]
        if self.semantic_score_threshold is None:
            raise ValueError(f"missing calibrated semantic threshold: {subtype}")
        return self.semantic_score_threshold


@dataclass(frozen=True, slots=True)
class BatchItem:
    candidate_key: str
    subtype: str
    platform: str
    lang: str
    vector_similarity: float
    rrf_score: float
    rank: int


@dataclass(frozen=True, slots=True)
class BatchGenerationResult:
    batch_id: str
    items: tuple[BatchItem, ...]
    yield_rate: float


@dataclass(frozen=True, slots=True)
class _RankedCandidate:
    candidate_key: str
    subtype: str
    platform: str
    lang: str
    uploader_identity: str
    task_score: int
    source_score: int
    vector_similarity: float
    rrf_score: float
    semantic_passed: bool


def reciprocal_rank_fusion(
    deterministic_order: Sequence[str],
    vector_order: Sequence[str],
    *,
    rrf_k: int,
) -> dict[str, float]:
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    deterministic_rank = {key: index for index, key in enumerate(deterministic_order, 1)}
    vector_rank = {key: index for index, key in enumerate(vector_order, 1)}
    shared = deterministic_rank.keys() & vector_rank.keys()
    return {
        key: 1 / (rrf_k + deterministic_rank[key]) + 1 / (rrf_k + vector_rank[key])
        for key in shared
    }


def generate_secondary_batch(
    database: CandidateDatabase,
    index: QdrantVectorIndex,
    schema: EmbeddingSchema,
    campaign_policy: CampaignPolicy,
    frontier_policy: FrontierPolicy,
    *,
    run_id: str,
    dedupe_policy_version: str,
    query_vectors: Mapping[str, Sequence[float]],
    eligibility_query_vectors: Mapping[str, Sequence[float]] | None = None,
) -> BatchGenerationResult:
    _register_policies(database, campaign_policy, frontier_policy)
    subtype_order = [name for name, _ in campaign_policy.subtype_limits]
    missing_vectors = [name for name in subtype_order if name not in query_vectors]
    if missing_vectors:
        raise ValueError(f"missing subtype query vectors: {', '.join(missing_vectors)}")
    ready_rows = database.connection.execute(
        """
        SELECT f.*, c.uploader_id, c.uploader, c.channel,
               (
                   SELECT MAX(s.similarity)
                   FROM semantic_task_eligibility s
                   WHERE s.candidate_key = f.candidate_key
                     AND s.campaign_id = f.campaign_id
                     AND s.subtype = f.subtype
                     AND s.embedding_schema_version = f.embedding_schema_version
                     AND (? IS NULL OR s.policy_version = ?)
               ) AS audited_semantic_similarity
        FROM frontier_entries f
        JOIN candidates c ON c.candidate_key = f.candidate_key
        WHERE f.run_id = ? AND f.campaign_id = ? AND f.status = 'ready'
          AND f.frontier_policy_version = ?
          AND f.embedding_schema_version = ?
          AND f.dedupe_policy_version = ?
        """,
        (
            frontier_policy.semantic_eligibility_policy_version,
            frontier_policy.semantic_eligibility_policy_version,
            run_id,
            campaign_policy.campaign_id,
            frontier_policy.version,
            schema.version,
            dedupe_policy_version,
        ),
    ).fetchall()
    by_subtype: dict[str, list] = defaultdict(list)
    for row in ready_rows:
        by_subtype[row["subtype"]].append(row)
    ranked_queues: dict[str, deque[_RankedCandidate]] = {}
    for subtype in subtype_order:
        rows = by_subtype.get(subtype, [])
        candidate_keys = [row["candidate_key"] for row in rows]
        if not candidate_keys:
            ranked_queues[subtype] = deque()
            continue
        semantic_threshold = frontier_policy.threshold_for(subtype)
        matches = index.query_relevance(
            schema,
            query_vectors[subtype],
            candidate_keys=candidate_keys,
            limit=min(
                len(candidate_keys),
                frontier_policy.batch_size * frontier_policy.vector_oversample_factor,
            ),
            score_threshold=None,
        )
        vector_scores = {
            item.candidate_key: item.score
            for item in matches
        }
        if eligibility_query_vectors is None:
            eligibility_scores = vector_scores
        else:
            if subtype not in eligibility_query_vectors:
                raise ValueError(f"missing eligibility query vector: {subtype}")
            eligibility_matches = index.query_relevance(
                schema,
                eligibility_query_vectors[subtype],
                candidate_keys=candidate_keys,
                limit=len(candidate_keys),
                score_threshold=None,
            )
            eligibility_scores = {
                item.candidate_key: item.score for item in eligibility_matches
            }
        for row in rows:
            audited = row["audited_semantic_similarity"]
            if audited is not None:
                eligibility_scores[row["candidate_key"]] = float(audited)
        matched_rows = [row for row in rows if row["candidate_key"] in vector_scores]
        deterministic_order = [
            row["candidate_key"]
            for row in sorted(
                matched_rows,
                key=lambda row: (
                    -row["task_score"],
                    -row["source_score"],
                    row["candidate_key"],
                ),
            )
        ]
        vector_order = [item.candidate_key for item in matches if item.candidate_key in deterministic_order]
        fused = reciprocal_rank_fusion(
            deterministic_order,
            vector_order,
            rrf_k=frontier_policy.rrf_k,
        )
        ranked = [
            _RankedCandidate(
                candidate_key=row["candidate_key"],
                subtype=subtype,
                platform=row["platform"],
                lang=row["lang"],
                uploader_identity=_uploader_identity(row),
                task_score=row["task_score"],
                source_score=row["source_score"],
                vector_similarity=eligibility_scores[row["candidate_key"]],
                rrf_score=fused[row["candidate_key"]],
                semantic_passed=(
                    eligibility_scores[row["candidate_key"]] >= semantic_threshold
                ),
            )
            for row in matched_rows
            if row["candidate_key"] in fused
            and row["candidate_key"] in eligibility_scores
        ]
        ranked.sort(key=lambda item: (-item.rrf_score, item.candidate_key))
        ranked_queues[subtype] = deque(_rotate_platform_language(ranked))

    batch_id = str(uuid.uuid4())
    now = utc_now()
    lease_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=frontier_policy.lease_seconds)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    limits = dict(campaign_policy.subtype_limits)
    with database.transaction() as connection:
        unfinished = connection.execute(
            """
            SELECT batch_id FROM secondary_batches
            WHERE run_id = ? AND campaign_id = ?
              AND status IN ('open', 'reviewed', 'queued')
            LIMIT 1
            """,
            (run_id, campaign_policy.campaign_id),
        ).fetchone()
        if unfinished is not None:
            raise ValueError("an unfinished Secondary Batch already exists")
        remaining = {
            subtype: max(0, limit - _reserved_subtype_count(connection, campaign_policy.campaign_id, subtype))
            for subtype, limit in campaign_policy.subtype_limits
        }
        reserved_keys = _reserved_candidate_keys(connection)
        uploader_counts = _reserved_uploader_counts(connection)
        leased_clusters = _leased_clusters(connection, dedupe_policy_version)
        candidate_clusters = _candidate_clusters(connection, dedupe_policy_version)
        selected: list[_RankedCandidate] = []
        used_keys: set[str] = set()
        used_clusters: set[str] = set()
        while len(selected) < frontier_policy.batch_size:
            progress = False
            for subtype in subtype_order:
                if len(selected) >= frontier_policy.batch_size:
                    break
                if remaining[subtype] <= 0:
                    continue
                queue = ranked_queues[subtype]
                chosen = None
                while queue:
                    candidate = queue.popleft()
                    if candidate.candidate_key in reserved_keys or candidate.candidate_key in used_keys:
                        continue
                    current = connection.execute(
                        """
                        SELECT f.status, c.status AS candidate_status,
                               c.resource_eligible,
                               t.qualified, t.score AS current_task_score,
                               EXISTS (
                                   SELECT 1 FROM semantic_task_eligibility s
                                   WHERE s.candidate_key = f.candidate_key
                                     AND s.campaign_id = f.campaign_id
                                     AND s.subtype = f.subtype
                                     AND s.embedding_schema_version = f.embedding_schema_version
                               ) AS semantic_qualified
                               ,EXISTS (
                                   SELECT 1 FROM candidate_suppressions cs
                                   WHERE cs.candidate_key = f.candidate_key
                                     AND cs.suppression_kind = 'source_hard_exclusion'
                                     AND NOT EXISTS (
                                         SELECT 1 FROM candidate_suppression_releases csr
                                         WHERE csr.suppression_id = cs.suppression_id
                                     )
                               ) AS suppressed
                        FROM frontier_entries f
                        JOIN candidates c ON c.candidate_key = f.candidate_key
                        JOIN candidate_task_scores t
                          ON t.candidate_key = f.candidate_key
                         AND t.campaign_id = f.campaign_id
                         AND t.subtype = f.subtype
                        WHERE f.candidate_key = ? AND f.campaign_id = ?
                          AND f.subtype = ? AND f.run_id = ?
                        """,
                        (
                            candidate.candidate_key,
                            campaign_policy.campaign_id,
                            subtype,
                            run_id,
                        ),
                    ).fetchone()
                    if (
                        current is None
                        or current["status"] != "ready"
                        or current["candidate_status"] != "source_qualified"
                        or current["resource_eligible"] != 1
                        or current["suppressed"] == 1
                        or not (
                            (
                                current["qualified"] == 1
                                and current["current_task_score"] >= 4
                            )
                            or current["semantic_qualified"] == 1
                        )
                    ):
                        continue
                    clusters = candidate_clusters.get(candidate.candidate_key, set())
                    if candidate.semantic_passed:
                        if (
                            uploader_counts.get(candidate.uploader_identity, 0)
                            >= frontier_policy.uploader_cap
                        ):
                            continue
                        if clusters & (leased_clusters | used_clusters):
                            continue
                    chosen = candidate
                    break
                if chosen is None:
                    continue
                selected.append(chosen)
                used_keys.add(chosen.candidate_key)
                if chosen.semantic_passed:
                    uploader_counts[chosen.uploader_identity] = (
                        uploader_counts.get(chosen.uploader_identity, 0) + 1
                    )
                    used_clusters.update(
                        candidate_clusters.get(chosen.candidate_key, set())
                    )
                    remaining[subtype] -= 1
                progress = True
            if not progress:
                break
        if not selected:
            raise ValueError("no candidates passed vector, quota, diversity, and lease checks")
        connection.execute(
            """
            INSERT INTO secondary_batches(
                batch_id, run_id, campaign_id, campaign_policy_version,
                frontier_policy_version,
                status, requested_size, actual_size, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, 'reviewed', ?, ?, ?, NULL)
            """,
            (
                batch_id,
                run_id,
                campaign_policy.campaign_id,
                campaign_policy.version,
                frontier_policy.version,
                frontier_policy.batch_size,
                len(selected),
                now,
            ),
        )
        for rank, candidate in enumerate(selected, 1):
            lease_id = str(uuid.uuid4())
            decision = (
                "download_eligible"
                if candidate.semantic_passed
                else "below_semantic_threshold"
            )
            connection.execute(
                """
                INSERT INTO secondary_batch_items(
                    batch_id, candidate_key, campaign_id, subtype, rank,
                    vector_similarity, rrf_score, lease_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    candidate.candidate_key,
                    campaign_policy.campaign_id,
                    candidate.subtype,
                    rank,
                    candidate.vector_similarity,
                    candidate.rrf_score,
                    lease_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO secondary_filter_decisions(
                    batch_id, candidate_key, decision, decided_campaign_id,
                    decided_subtype, vector_similarity, threshold,
                    reasons_json, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    candidate.candidate_key,
                    decision,
                    campaign_policy.campaign_id,
                    candidate.subtype,
                    candidate.vector_similarity,
                    frontier_policy.threshold_for(candidate.subtype),
                    json.dumps(
                        {
                            "rrf_score": candidate.rrf_score,
                            "task_score": candidate.task_score,
                            "source_score": candidate.source_score,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
            if candidate.semantic_passed:
                connection.execute(
                    """
                    UPDATE frontier_entries
                    SET status = 'leased', lease_id = ?, lease_expires_at = ?, updated_at = ?
                    WHERE candidate_key = ? AND campaign_id = ?
                      AND subtype = ? AND run_id = ? AND status = 'ready'
                    """,
                    (
                        lease_id,
                        lease_expires_at,
                        now,
                        candidate.candidate_key,
                        campaign_policy.campaign_id,
                        candidate.subtype,
                        run_id,
                    ),
                )
                for cluster_id in candidate_clusters.get(candidate.candidate_key, set()):
                    connection.execute(
                        """
                        UPDATE duplicate_cluster_members
                        SET member_status = 'leased', run_id = ?
                        WHERE duplicate_cluster_id = ? AND candidate_key = ?
                        """,
                        (run_id, cluster_id, candidate.candidate_key),
                    )
            else:
                connection.execute(
                    """
                    UPDATE frontier_entries
                    SET status = 'consumed', lease_id = NULL,
                        lease_expires_at = NULL, updated_at = ?
                    WHERE candidate_key = ? AND campaign_id = ?
                      AND subtype = ? AND run_id = ? AND status = 'ready'
                    """,
                    (
                        now,
                        candidate.candidate_key,
                        campaign_policy.campaign_id,
                        candidate.subtype,
                        run_id,
                    ),
                )
    items = tuple(
        BatchItem(
            candidate.candidate_key,
            candidate.subtype,
            candidate.platform,
            candidate.lang,
            candidate.vector_similarity,
            candidate.rrf_score,
            rank,
        )
        for rank, candidate in enumerate(selected, 1)
    )
    yield_evaluation = record_batch_yield(
        database,
        batch_id=batch_id,
        low_yield_threshold=frontier_policy.low_yield_threshold,
        low_yield_consecutive_windows=frontier_policy.low_yield_consecutive_windows,
        partition_window_size=frontier_policy.low_yield_partition_window_size,
    )
    return BatchGenerationResult(batch_id, items, yield_evaluation.yield_rate)


def cancel_secondary_batch(
    database: CandidateDatabase,
    batch_id: str,
    *,
    reason: str,
) -> None:
    if not reason:
        raise ValueError("batch cancellation reason is required")
    now = utc_now()
    with database.transaction() as connection:
        batch = connection.execute(
            "SELECT * FROM secondary_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if batch is None or batch["status"] not in {"reviewed", "queued"}:
            raise ValueError("only an unfinished batch can be cancelled")
        assigned = connection.execute(
            "SELECT 1 FROM queue_assignments WHERE batch_id = ? LIMIT 1",
            (batch_id,),
        ).fetchone()
        if assigned is not None:
            raise ValueError("batch with queued downloads cannot be cancelled")
        items = connection.execute(
            "SELECT candidate_key, campaign_id, subtype FROM secondary_batch_items WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()
        for item in items:
            connection.execute(
                """
                UPDATE frontier_entries
                SET status = 'suspended', lease_id = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE candidate_key = ? AND campaign_id = ?
                  AND subtype = ? AND run_id = ? AND status = 'leased'
                """,
                (
                    now,
                    item["candidate_key"],
                    item["campaign_id"],
                    item["subtype"],
                    batch["run_id"],
                ),
            )
            connection.execute(
                """
                UPDATE duplicate_cluster_members
                SET member_status = 'ready', run_id = ?
                WHERE candidate_key = ? AND member_status = 'leased'
                """,
                (batch["run_id"], item["candidate_key"]),
            )
        connection.execute(
            """
            UPDATE secondary_batches
            SET status = 'completed', completed_at = ? WHERE batch_id = ?
            """,
            (now, batch_id),
        )


def _register_policies(
    database: CandidateDatabase,
    campaign: CampaignPolicy,
    frontier: FrontierPolicy,
) -> None:
    subtype_json = json.dumps(dict(campaign.subtype_limits), ensure_ascii=False, separators=(",", ":"))
    frontier_json = json.dumps(
        {
            "batch_size": frontier.batch_size,
            "vector_oversample_factor": frontier.vector_oversample_factor,
            "semantic_score_threshold": frontier.semantic_score_threshold,
            "semantic_score_thresholds": dict(frontier.semantic_score_thresholds),
            "rrf_k": frontier.rrf_k,
            "uploader_cap": frontier.uploader_cap,
            "lease_seconds": frontier.lease_seconds,
            "low_yield_threshold": frontier.low_yield_threshold,
            "low_yield_consecutive_windows": frontier.low_yield_consecutive_windows,
            "low_yield_partition_window_size": frontier.low_yield_partition_window_size,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO campaigns(campaign_id, created_at)
            VALUES (?, ?)
            """,
            (campaign.campaign_id, utc_now()),
        )
        existing_campaign = connection.execute(
            """
            SELECT subtype_limits_json, max_candidates
            FROM campaign_policy_versions
            WHERE campaign_id = ? AND policy_version = ?
            """,
            (campaign.campaign_id, campaign.version),
        ).fetchone()
        if existing_campaign is None:
            connection.execute(
                """
                INSERT INTO campaign_policy_versions(
                    campaign_id, policy_version, subtype_limits_json,
                    max_candidates, created_at, created_by, reason
                ) VALUES (?, ?, ?, ?, ?, 'system', 'batch generator policy')
                """,
                (
                    campaign.campaign_id,
                    campaign.version,
                    subtype_json,
                    campaign.max_candidates,
                    utc_now(),
                ),
            )
        elif tuple(existing_campaign) != (subtype_json, campaign.max_candidates):
            raise ValueError("campaign policy version already has different settings")
        existing_frontier = connection.execute(
            """
            SELECT policy_json FROM frontier_policy_versions
            WHERE campaign_id = ? AND frontier_policy_version = ?
            """,
            (campaign.campaign_id, frontier.version),
        ).fetchone()
        if existing_frontier is None:
            connection.execute(
                """
                INSERT INTO frontier_policy_versions(
                    campaign_id, frontier_policy_version, policy_json,
                    created_at, created_by, reason
                ) VALUES (?, ?, ?, ?, 'system', 'batch generator policy')
                """,
                (campaign.campaign_id, frontier.version, frontier_json, utc_now()),
            )
        else:
            stored_frontier = json.loads(existing_frontier["policy_json"])
            expected_frontier = json.loads(frontier_json)
            if any(
                stored_frontier.get(key) != value
                for key, value in expected_frontier.items()
            ):
                raise ValueError("frontier policy version already has different settings")
        connection.execute(
            """
            UPDATE campaigns SET
                active_policy_version = COALESCE(active_policy_version, ?),
                active_frontier_policy_version = COALESCE(active_frontier_policy_version, ?)
            WHERE campaign_id = ?
            """,
            (campaign.version, frontier.version, campaign.campaign_id),
        )


def _rotate_platform_language(items: Sequence[_RankedCandidate]) -> list[_RankedCandidate]:
    buckets: dict[tuple[str, str], deque[_RankedCandidate]] = defaultdict(deque)
    for item in items:
        buckets[(item.platform, item.lang)].append(item)
    result: list[_RankedCandidate] = []
    keys = sorted(buckets)
    while True:
        progress = False
        for key in keys:
            if buckets[key]:
                result.append(buckets[key].popleft())
                progress = True
        if not progress:
            return result


def _uploader_identity(row) -> str:
    return row["uploader_id"] or row["uploader"] or row["channel"] or row["candidate_key"]


def _reserved_subtype_count(connection, campaign_id: str, subtype: str) -> int:
    return int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM queue_assignments q
            JOIN candidates c ON c.candidate_key = q.candidate_key
            WHERE q.campaign_id = ? AND q.subtype = ?
              AND c.status IN ('task_queued', 'downloaded')
            """,
            (campaign_id, subtype),
        ).fetchone()[0]
    )


def _reserved_candidate_keys(connection) -> set[str]:
    rows = connection.execute(
        """
        SELECT candidate_key FROM queue_assignments
        UNION
        SELECT i.candidate_key
        FROM secondary_batch_items i
        JOIN secondary_batches b ON b.batch_id = i.batch_id
        WHERE b.status IN ('open', 'reviewed', 'queued')
        """
    ).fetchall()
    return {row[0] for row in rows}


def _reserved_uploader_counts(connection) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT q.candidate_key
        FROM queue_assignments q
        JOIN candidates c ON c.candidate_key = q.candidate_key
        WHERE c.status IN ('task_queued', 'downloaded')
        UNION
        SELECT i.candidate_key
        FROM secondary_batch_items i
        JOIN secondary_batches b ON b.batch_id = i.batch_id
        JOIN secondary_filter_decisions d
          ON d.batch_id = i.batch_id AND d.candidate_key = i.candidate_key
        WHERE b.status IN ('open', 'reviewed', 'queued')
          AND d.decision = 'download_eligible'
        """
    ).fetchall()
    keys = {row[0] for row in rows}
    counts: dict[str, int] = {}
    for candidate_key in keys:
        row = connection.execute(
            "SELECT candidate_key, uploader_id, uploader, channel FROM candidates WHERE candidate_key = ?",
            (candidate_key,),
        ).fetchone()
        if row is not None:
            identity = _uploader_identity(row)
            counts[identity] = counts.get(identity, 0) + 1
    return counts


def _leased_clusters(connection, dedupe_policy_version: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT DISTINCT m.duplicate_cluster_id
        FROM duplicate_cluster_members m
        JOIN duplicate_clusters c ON c.duplicate_cluster_id = m.duplicate_cluster_id
        WHERE c.dedupe_policy_version = ? AND m.member_status = 'leased'
        """,
        (dedupe_policy_version,),
    ).fetchall()
    return {row[0] for row in rows}


def _candidate_clusters(connection, dedupe_policy_version: str) -> dict[str, set[str]]:
    rows = connection.execute(
        """
        SELECT m.candidate_key, m.duplicate_cluster_id
        FROM duplicate_cluster_members m
        JOIN duplicate_clusters c ON c.duplicate_cluster_id = m.duplicate_cluster_id
        WHERE c.dedupe_policy_version = ? AND c.status = 'active'
        """,
        (dedupe_policy_version,),
    ).fetchall()
    result: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        result[row["candidate_key"]].add(row["duplicate_cluster_id"])
    return result
