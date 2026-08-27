"""Vector-suspect duplicate evidence and deterministic cluster refresh."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher

from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.scoring.matching import normalize_text
from surveillance_video_agent.vector_index import QdrantVectorIndex


_CLUSTER_NAMESPACE = uuid.UUID("8592964d-9daa-4fb6-bc6d-301b364e22e7")


@dataclass(frozen=True, slots=True)
class DedupePolicy:
    version: str
    similarity_threshold: float
    title_similarity_threshold: float
    duration_tolerance_seconds: float
    neighbor_limit: int = 10
    vector_enabled: bool = True

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("dedupe policy version is required")
        if not 0 <= self.similarity_threshold <= 1:
            raise ValueError("similarity_threshold must be between 0 and 1")
        if not 0 <= self.title_similarity_threshold <= 1:
            raise ValueError("title_similarity_threshold must be between 0 and 1")
        if self.duration_tolerance_seconds < 0 or self.neighbor_limit <= 0:
            raise ValueError("dedupe duration tolerance and neighbor limit must be positive")


@dataclass(frozen=True, slots=True)
class DedupeRefreshResult:
    edge_count: int
    cluster_count: int


def refresh_vector_duplicate_clusters(
    database: CandidateDatabase,
    index: QdrantVectorIndex,
    schema: EmbeddingSchema,
    policy: DedupePolicy,
    *,
    run_id: str,
    campaign_id: str,
) -> DedupeRefreshResult:
    _register_policy(database, schema, policy)
    started_at = utc_now()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO dedupe_refreshes(
                run_id, campaign_id, embedding_schema_version,
                dedupe_policy_version, status, started_at
            ) VALUES (?, ?, ?, ?, 'running', ?)
            ON CONFLICT(run_id, campaign_id, embedding_schema_version, dedupe_policy_version)
            DO UPDATE SET status = 'running', edge_count = 0, cluster_count = 0,
                          started_at = excluded.started_at, completed_at = NULL
            """,
            (run_id, campaign_id, schema.version, policy.version, started_at),
        )
    try:
        rows = database.connection.execute(
            """
            SELECT c.*
            FROM candidates c
            WHERE c.status = 'source_qualified'
              AND c.resource_eligible = 1
              AND EXISTS (
                  SELECT 1 FROM candidate_task_scores t
                  WHERE t.candidate_key = c.candidate_key
                    AND t.campaign_id = ? AND t.qualified = 1 AND t.score >= 4
              )
              AND 2 = (
                  SELECT COUNT(*) FROM candidate_embeddings e
                  WHERE e.candidate_key = c.candidate_key
                    AND e.embedding_schema_version = ?
                    AND e.index_status = 'ready'
                    AND e.indexed_input_hash = e.current_input_hash
              )
            ORDER BY c.candidate_key
            """,
            (campaign_id, schema.version),
        ).fetchall()
        by_key = {row["candidate_key"]: row for row in rows}
        keys = tuple(by_key)
        edge_pairs: set[tuple[str, str]] = set()
        for candidate_key in keys if policy.vector_enabled else ():
            matches = index.query_duplicate_neighbors(
                schema,
                candidate_key,
                candidate_keys=keys,
                limit=policy.neighbor_limit,
                score_threshold=policy.similarity_threshold,
            )
            for match in matches:
                pair = tuple(sorted((candidate_key, match.candidate_key)))
                if pair[0] == pair[1] or pair in edge_pairs:
                    continue
                left = by_key[pair[0]]
                right = by_key[pair[1]]
                title_similarity = _title_similarity(left["title"], right["title"])
                if title_similarity < policy.title_similarity_threshold:
                    continue
                duration_delta = _duration_delta(
                    left["duration_seconds"], right["duration_seconds"]
                )
                if duration_delta is None or duration_delta > policy.duration_tolerance_seconds:
                    continue
                edge_pairs.add(pair)
                with database.transaction() as connection:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO duplicate_edges(
                            edge_id, left_candidate_key, right_candidate_key, kind,
                            evidence_version, similarity, evidence_json, created_at
                        ) VALUES (?, ?, ?, 'vector_suspect', ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            pair[0],
                            pair[1],
                            policy.version,
                            match.score,
                            json.dumps(
                                {
                                    "embedding_schema_version": schema.version,
                                    "title_similarity": title_similarity,
                                    "duration_delta_seconds": duration_delta,
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            utc_now(),
                        ),
                    )
        components = _connected_components(keys, edge_pairs)
        clusters = [component for component in components if len(component) > 1]
        with database.transaction() as connection:
            for component in clusters:
                cluster_id = str(
                    uuid.uuid5(
                        _CLUSTER_NAMESPACE,
                        f"{policy.version}:{'|'.join(component)}",
                    )
                )
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO duplicate_clusters(
                        duplicate_cluster_id, dedupe_policy_version,
                        cluster_kind, status, created_at, updated_at
                    ) VALUES (?, ?, 'vector_suspect', 'active', ?, ?)
                    ON CONFLICT(duplicate_cluster_id) DO UPDATE SET updated_at = excluded.updated_at
                    """,
                    (cluster_id, policy.version, now, now),
                )
                for candidate_key in component:
                    connection.execute(
                        """
                        INSERT INTO duplicate_cluster_members(
                            duplicate_cluster_id, candidate_key, member_status, run_id
                        ) VALUES (?, ?, 'ready', ?)
                        ON CONFLICT(duplicate_cluster_id, candidate_key) DO UPDATE SET
                            member_status = CASE
                                WHEN duplicate_cluster_members.member_status = 'leased'
                                THEN 'leased' ELSE 'ready' END,
                            run_id = excluded.run_id
                        """,
                        (cluster_id, candidate_key, run_id),
                    )
            connection.execute(
                """
                UPDATE dedupe_refreshes
                SET status = 'completed', edge_count = ?, cluster_count = ?, completed_at = ?
                WHERE run_id = ? AND campaign_id = ?
                  AND embedding_schema_version = ? AND dedupe_policy_version = ?
                """,
                (
                    len(edge_pairs),
                    len(clusters),
                    utc_now(),
                    run_id,
                    campaign_id,
                    schema.version,
                    policy.version,
                ),
            )
        return DedupeRefreshResult(len(edge_pairs), len(clusters))
    except Exception:
        with database.transaction() as connection:
            connection.execute(
                """
                UPDATE dedupe_refreshes SET status = 'failed', completed_at = ?
                WHERE run_id = ? AND campaign_id = ?
                  AND embedding_schema_version = ? AND dedupe_policy_version = ?
                """,
                (utc_now(), run_id, campaign_id, schema.version, policy.version),
            )
        raise


def _register_policy(
    database: CandidateDatabase, schema: EmbeddingSchema, policy: DedupePolicy
) -> None:
    payload = json.dumps(
        {
            "similarity_threshold": policy.similarity_threshold,
            "title_similarity_threshold": policy.title_similarity_threshold,
            "duration_tolerance_seconds": policy.duration_tolerance_seconds,
            "neighbor_limit": policy.neighbor_limit,
            "vector_enabled": policy.vector_enabled,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with database.transaction() as connection:
        existing = connection.execute(
            "SELECT embedding_schema_version, policy_json FROM dedupe_policy_versions WHERE dedupe_policy_version = ?",
            (policy.version,),
        ).fetchone()
        if existing is not None:
            if existing["embedding_schema_version"] != schema.version or existing["policy_json"] != payload:
                raise ValueError("dedupe policy version already has different settings")
            return
        connection.execute(
            """
            INSERT INTO dedupe_policy_versions(
                dedupe_policy_version, embedding_schema_version, policy_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (policy.version, schema.version, payload, utc_now()),
        )


def register_dedupe_policy(
    database: CandidateDatabase,
    schema: EmbeddingSchema,
    policy: DedupePolicy,
) -> None:
    _register_policy(database, schema, policy)


def get_dedupe_policy(
    database: CandidateDatabase,
    dedupe_policy_version: str,
) -> DedupePolicy:
    row = database.connection.execute(
        """
        SELECT policy_json FROM dedupe_policy_versions
        WHERE dedupe_policy_version = ?
        """,
        (dedupe_policy_version,),
    ).fetchone()
    if row is None:
        raise ValueError("dedupe policy version not found")
    payload = json.loads(row["policy_json"])
    return DedupePolicy(
        version=dedupe_policy_version,
        similarity_threshold=float(payload["similarity_threshold"]),
        title_similarity_threshold=float(payload["title_similarity_threshold"]),
        duration_tolerance_seconds=float(payload["duration_tolerance_seconds"]),
        neighbor_limit=int(payload["neighbor_limit"]),
        vector_enabled=bool(payload.get("vector_enabled", True)),
    )


def _title_similarity(left: str | None, right: str | None) -> float:
    left_normalized = normalize_text(left or "")
    right_normalized = normalize_text(right or "")
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _duration_delta(left, right) -> float | None:
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    return abs(float(left) - float(right))


def _connected_components(
    keys: tuple[str, ...], edges: set[tuple[str, str]]
) -> list[tuple[str, ...]]:
    graph = {key: set() for key in keys}
    for left, right in edges:
        graph[left].add(right)
        graph[right].add(left)
    components: list[tuple[str, ...]] = []
    unseen = set(keys)
    while unseen:
        root = min(unseen)
        stack = [root]
        members: set[str] = set()
        while stack:
            item = stack.pop()
            if item in members:
                continue
            members.add(item)
            unseen.discard(item)
            stack.extend(graph[item] - members)
        components.append(tuple(sorted(members)))
    return components
