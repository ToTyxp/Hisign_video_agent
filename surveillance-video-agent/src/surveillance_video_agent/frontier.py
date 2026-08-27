"""Refresh the deterministic Qualified Frontier after vector/dedupe readiness."""

from __future__ import annotations

import json
from dataclasses import dataclass

from surveillance_video_agent.db import CandidateDatabase, utc_now


@dataclass(frozen=True, slots=True)
class FrontierRefreshResult:
    ready_count: int
    suspended_count: int


def refresh_frontier(
    database: CandidateDatabase,
    *,
    run_id: str,
    campaign_id: str,
    query_pack_version: str,
    frontier_policy_version: str,
    embedding_schema_version: str,
    dedupe_policy_version: str,
) -> FrontierRefreshResult:
    refresh = database.connection.execute(
        """
        SELECT status FROM dedupe_refreshes
        WHERE run_id = ? AND campaign_id = ?
          AND embedding_schema_version = ? AND dedupe_policy_version = ?
        """,
        (run_id, campaign_id, embedding_schema_version, dedupe_policy_version),
    ).fetchone()
    if refresh is None or refresh["status"] != "completed":
        raise ValueError("completed duplicate-cluster refresh is required")
    frontier_policy = database.connection.execute(
        """
        SELECT policy_json FROM frontier_policy_versions
        WHERE campaign_id = ? AND frontier_policy_version = ?
        """,
        (campaign_id, frontier_policy_version),
    ).fetchone()
    # Older lexical-only runs register the Frontier policy lazily in the batch
    # generator.  Keep that path valid; only an explicit semantic override
    # needs the additional eligibility policy binding stored in policy_json.
    frontier_payload = (
        json.loads(frontier_policy["policy_json"])
        if frontier_policy is not None
        else {}
    )
    semantic_policy_version = str(
        frontier_payload.get("semantic_eligibility_policy_version") or ""
    )
    dedupe = database.connection.execute(
        """
        SELECT policy_json FROM dedupe_policy_versions
        WHERE dedupe_policy_version = ?
        """,
        (dedupe_policy_version,),
    ).fetchone()
    if dedupe is None:
        raise ValueError("dedupe policy version not found")
    vector_dedupe_enabled = bool(
        json.loads(dedupe["policy_json"]).get("vector_enabled", True)
    )
    allowed_camera_pools = frontier_payload.get(
        "allowed_camera_pools", ["surveillance"]
    )
    allow_mobile_adjacent = int("mobile_adjacent" in allowed_camera_pools)
    rows = database.connection.execute(
        """
        WITH eligible AS (
            SELECT candidate_key, campaign_id, subtype, score AS task_score
            FROM candidate_task_scores
            WHERE campaign_id = ? AND qualified = 1 AND score >= 4
            UNION ALL
            SELECT candidate_key, campaign_id, subtype, 4 AS task_score
            FROM semantic_task_eligibility
            WHERE campaign_id = ? AND query_pack_version = ?
              AND embedding_schema_version = ? AND policy_version = ?
        ), grouped AS (
            SELECT candidate_key, campaign_id, subtype,
                   MAX(task_score) AS task_score
            FROM eligible
            GROUP BY candidate_key, campaign_id, subtype
        )
        SELECT c.candidate_key, c.platform, c.source_score,
               t.campaign_id, t.subtype, t.task_score
        FROM candidates c
        JOIN grouped t ON t.candidate_key = c.candidate_key
        JOIN candidate_embeddings er
          ON er.candidate_key = c.candidate_key
         AND er.embedding_schema_version = ?
         AND er.vector_name = 'relevance'
         AND er.index_status = 'ready'
         AND er.indexed_input_hash = er.current_input_hash
        LEFT JOIN candidate_embeddings ed
          ON ed.candidate_key = c.candidate_key
         AND ed.embedding_schema_version = ?
         AND ed.vector_name = 'duplicate'
         AND ed.index_status = 'ready'
         AND ed.indexed_input_hash = ed.current_input_hash
        WHERE c.status = 'source_qualified'
          AND c.hard_excluded = 0 AND c.source_score >= 4
          AND c.resource_eligible = 1
          AND (
              c.camera_pool = 'surveillance'
              OR (? = 1 AND c.camera_pool = 'mobile_adjacent')
          )
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
          AND (? = 0 OR (
              ed.index_status = 'ready'
              AND ed.indexed_input_hash = ed.current_input_hash
          ))
        ORDER BY c.candidate_key, t.subtype
        """,
        (
            campaign_id,
            campaign_id,
            query_pack_version,
            embedding_schema_version,
            semantic_policy_version,
            embedding_schema_version,
            embedding_schema_version,
            allow_mobile_adjacent,
            campaign_id,
            int(vector_dedupe_enabled),
        ),
    ).fetchall()
    selected: dict[tuple[str, str], dict] = {}
    for row in rows:
        attribution = _attribution(
            database,
            row["candidate_key"],
            campaign_id,
            query_pack_version,
        )
        if attribution is None:
            continue
        selected[(row["candidate_key"], row["subtype"])] = {
            **dict(row),
            "lang": attribution["lang"],
            "query_id": attribution["query_id"],
        }
    now = utc_now()
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE frontier_entries SET status = 'suspended', updated_at = ?
            WHERE run_id = ? AND campaign_id = ? AND status = 'ready'
            """,
            (now, run_id, campaign_id),
        )
        for row in selected.values():
            connection.execute(
                """
                INSERT INTO frontier_entries(
                    candidate_key, campaign_id, subtype, run_id, status,
                    task_score, source_score, platform, lang,
                    attributed_query_id, frontier_policy_version,
                    embedding_schema_version, dedupe_policy_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_key, campaign_id, subtype, run_id)
                DO UPDATE SET
                    status = CASE
                        WHEN frontier_entries.status IN ('leased', 'consumed')
                        THEN frontier_entries.status ELSE 'ready' END,
                    task_score = excluded.task_score,
                    source_score = excluded.source_score,
                    platform = excluded.platform,
                    updated_at = excluded.updated_at
                """,
                (
                    row["candidate_key"],
                    campaign_id,
                    row["subtype"],
                    run_id,
                    row["task_score"],
                    row["source_score"],
                    row["platform"],
                    row["lang"],
                    row["query_id"],
                    frontier_policy_version,
                    embedding_schema_version,
                    dedupe_policy_version,
                    now,
                    now,
                ),
            )
    ready = database.connection.execute(
        """
        SELECT COUNT(*) FROM frontier_entries
        WHERE run_id = ? AND campaign_id = ? AND status = 'ready'
        """,
        (run_id, campaign_id),
    ).fetchone()[0]
    suspended = database.connection.execute(
        """
        SELECT COUNT(*) FROM frontier_entries
        WHERE run_id = ? AND campaign_id = ? AND status = 'suspended'
        """,
        (run_id, campaign_id),
    ).fetchone()[0]
    return FrontierRefreshResult(int(ready), int(suspended))


def _attribution(database, candidate_key, campaign_id, query_pack_version):
    row = database.connection.execute(
        """
        SELECT q.query_id, q.lang
        FROM candidate_discoveries d JOIN queries q ON q.query_id = d.query_id
        WHERE d.candidate_key = ? AND q.campaign_id = ?
        ORDER BY (q.query_pack_version = ?) DESC,
                 d.platform_position, d.discovered_at, q.query_id
        LIMIT 1
        """,
        (candidate_key, campaign_id, query_pack_version),
    ).fetchone()
    if row is not None:
        return row
    return database.connection.execute(
        """
        SELECT q.query_id, q.lang
        FROM candidate_discoveries d JOIN queries q ON q.query_id = d.query_id
        WHERE d.candidate_key = ?
        ORDER BY d.platform_position, d.discovered_at, q.query_id LIMIT 1
        """,
        (candidate_key,),
    ).fetchone()
