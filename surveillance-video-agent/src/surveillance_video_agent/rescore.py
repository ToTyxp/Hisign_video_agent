"""Atomic offline rescoring of already source-qualified candidate metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass

from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.scoring import (
    CandidateMetadata,
    ScoringBundle,
    score_all_tasks,
    score_sign_mobile_source,
    score_source,
)


@dataclass(frozen=True, slots=True)
class RescoreResult:
    policy_version: str
    candidate_count: int
    rescored_count: int
    skipped_current_count: int
    qualified_counts: dict[str, dict[str, int]]


def rescore_source_qualified_candidates(
    database: CandidateDatabase,
    policy: ScoringBundle,
    *,
    run_id: str,
) -> RescoreResult:
    run = database.connection.execute(
        "SELECT status FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run is None or run["status"] != "running":
        raise ValueError("an existing running run_id is required")
    rows = database.connection.execute(
        """
        SELECT * FROM candidates
        WHERE status = 'source_qualified'
        ORDER BY candidate_key
        """
    ).fetchall()
    rescored = 0
    skipped = 0
    for row in rows:
        current_task_rows = database.connection.execute(
            """
            SELECT COUNT(*) AS count,
                   SUM(policy_version = ?) AS current_count
            FROM candidate_task_scores WHERE candidate_key = ?
            """,
            (policy.policy_version, row["candidate_key"]),
        ).fetchone()
        if (
            row["source_policy_version"] == policy.policy_version
            and current_task_rows["count"] == len(policy.tasks)
            and current_task_rows["current_count"] == len(policy.tasks)
        ):
            skipped += 1
            continue
        metadata = CandidateMetadata(
            candidate_key=row["candidate_key"],
            title=row["title"] or "",
            video_description=row["video_description"] or "",
            tags=tuple(json.loads(row["tags_json"])),
            uploader=row["uploader"] or "",
            channel=row["channel"] or "",
            playlist=row["playlist"] or "",
        )
        prior = 0
        if row["uploader_id"]:
            prior_row = database.connection.execute(
                """
                SELECT prior_points FROM uploader_priors
                WHERE platform = ? AND uploader_id = ?
                """,
                (row["platform"], row["uploader_id"]),
            ).fetchone()
            if prior_row is not None:
                prior = int(prior_row["prior_points"])
        source_result = (
            score_sign_mobile_source(
                metadata,
                policy,
                width=row["width"],
                height=row["height"],
                duration_seconds=row["duration_seconds"],
                discovered_by_mobile_query=True,
                legacy_uploader_prior=prior,
            )
            if row["camera_pool"] == "mobile_adjacent"
            else score_source(
                metadata,
                policy,
                legacy_uploader_prior=prior,
            )
        )
        if not source_result.qualified:
            if not source_result.hard_excluded:
                # Query-pack vocabulary revisions must not revoke a historical
                # source qualification merely because an old anchor vanished.
                skipped += 1
                continue
            # A later hard exclusion cannot rewrite a historical state edge.
            # Record a separate append-only suppression consumed by Frontier.
            database.record_candidate_suppression(
                row["candidate_key"],
                suppression_kind="source_hard_exclusion",
                policy_version=policy.policy_version,
                reasons=[
                    {
                        "category": item.category,
                        "fields": item.matched_fields,
                        "terms": item.matched_terms,
                        "reason": item.reason,
                    }
                    for item in source_result.hard_exclusions
                ],
                run_id=run_id,
            )
            rescored += 1
            continue
        database.record_qualification(
            source_result,
            score_all_tasks(metadata, source_result, policy),
            run_id=run_id,
        )
        rescored += 1
    counts: dict[str, dict[str, int]] = {}
    for row in database.connection.execute(
        """
        SELECT campaign_id, subtype, COUNT(*) AS count
        FROM candidate_task_scores
        WHERE qualified = 1 AND policy_version = ?
        GROUP BY campaign_id, subtype
        ORDER BY campaign_id, subtype
        """,
        (policy.policy_version,),
    ).fetchall():
        counts.setdefault(row["campaign_id"], {})[row["subtype"]] = int(
            row["count"]
        )
    return RescoreResult(
        policy_version=policy.policy_version,
        candidate_count=len(rows),
        rescored_count=rescored,
        skipped_current_count=skipped,
        qualified_counts=counts,
    )
