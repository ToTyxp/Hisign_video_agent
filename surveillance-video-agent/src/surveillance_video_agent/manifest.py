"""Deterministic, atomic campaign Manifest export from SQLite."""

from __future__ import annotations

import json
import os
from pathlib import Path

from surveillance_video_agent.db import CandidateDatabase


REQUIRED_MANIFEST_KEYS = frozenset(
    {
        "candidate_key",
        "platform",
        "source_id",
        "source_url",
        "title",
        "video_description",
        "uploader",
        "uploader_id",
        "query",
        "lang",
        "query_pack_version",
        "camera_pool",
        "campaign_id",
        "campaign_policy_version",
        "subtype",
        "frontier_policy_version",
        "embedding_schema_version",
        "dedupe_policy_version",
        "secondary_batch_id",
        "frontier_priority",
        "vector_similarity",
        "rrf_score",
        "secondary_decision",
        "secondary_filter_reasons",
        "source_score",
        "source_score_reasons",
        "task_score",
        "task_score_reasons",
        "resource_eligible",
        "resource_policy_version",
        "resource_reasons",
        "duration_seconds",
        "resolution",
        "sha256",
        "technical_status",
        "technical_checks",
        "discovered_at",
        "queued_at",
        "downloaded_or_failed_at",
        "media_path",
        "run_id",
        "adapter_version",
        "network_config",
    }
)


def export_campaign_manifest(
    database: CandidateDatabase,
    campaign_id: str,
    output_path: Path,
) -> int:
    rows = database.connection.execute(
        """
        SELECT q.*, c.*, b.campaign_policy_version,
               b.frontier_policy_version AS batch_frontier_policy_version,
               f.attributed_query_id, f.embedding_schema_version,
               f.dedupe_policy_version, f.task_score AS frontier_task_score,
               f.source_score AS frontier_source_score,
               i.vector_similarity, i.rrf_score,
               d.decision AS secondary_decision, d.reasons_json,
               qu.query_text, qu.lang, qu.query_pack_version
        FROM queue_assignments q
        JOIN candidates c ON c.candidate_key = q.candidate_key
        JOIN secondary_batches b ON b.batch_id = q.batch_id
        JOIN secondary_batch_items i
          ON i.batch_id = q.batch_id AND i.candidate_key = q.candidate_key
        JOIN secondary_filter_decisions d
          ON d.batch_id = q.batch_id AND d.candidate_key = q.candidate_key
        LEFT JOIN frontier_entries f
          ON f.candidate_key = q.candidate_key
         AND f.campaign_id = q.campaign_id
         AND f.subtype = q.subtype
         AND f.run_id = q.run_id
        LEFT JOIN queries qu ON qu.query_id = f.attributed_query_id
        WHERE q.campaign_id = ?
        ORDER BY q.queued_at, q.rank, q.candidate_key
        """,
        (campaign_id,),
    ).fetchall()
    documents = [_manifest_row(database, row) for row in rows]
    for document in documents:
        missing = REQUIRED_MANIFEST_KEYS - document.keys()
        if missing:
            raise RuntimeError(f"manifest row is missing keys: {sorted(missing)}")
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for document in documents:
            handle.write(
                json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    descriptor = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return len(documents)


def _manifest_row(database: CandidateDatabase, row) -> dict:
    attempt = database.connection.execute(
        """
        SELECT * FROM download_attempts
        WHERE candidate_key = ?
        ORDER BY started_at DESC, attempt_id DESC LIMIT 1
        """,
        (row["candidate_key"],),
    ).fetchone()
    technical = None
    if attempt is not None:
        technical = database.connection.execute(
            "SELECT * FROM technical_checks WHERE attempt_id = ?",
            (attempt["attempt_id"],),
        ).fetchone()
    media = database.connection.execute(
        "SELECT * FROM media_objects WHERE candidate_key = ? ORDER BY created_at DESC LIMIT 1",
        (row["candidate_key"],),
    ).fetchone()
    source_reasons = _score_reasons(
        database, row["candidate_key"], "source", None, None
    )
    task_reasons = _score_reasons(
        database,
        row["candidate_key"],
        "task",
        row["campaign_id"],
        row["subtype"],
    )
    discovered = database.connection.execute(
        "SELECT MIN(discovered_at) FROM candidate_discoveries WHERE candidate_key = ?",
        (row["candidate_key"],),
    ).fetchone()[0]
    resolution = None
    if technical is not None and technical["width"] and technical["height"]:
        resolution = {"width": technical["width"], "height": technical["height"]}
    elif row["width"] and row["height"]:
        resolution = {"width": row["width"], "height": row["height"]}
    technical_checks = None
    if technical is not None:
        technical_checks = {
            "ffprobe_passed": bool(technical["ffprobe_passed"]),
            "video_stream_present": bool(technical["video_stream_present"]),
            "decode_first_passed": bool(technical["decode_first_passed"]),
            "decode_middle_passed": bool(technical["decode_middle_passed"]),
            "decode_last_passed": bool(technical["decode_last_passed"]),
        }
    media_path = None
    if media is not None and media["publish_status"] == "published":
        media_path = media["final_path"]
    elif attempt is not None:
        media_path = attempt["final_path"] or attempt["temp_path"]
    task_score = database.connection.execute(
        """
        SELECT score FROM candidate_task_scores
        WHERE candidate_key = ? AND campaign_id = ? AND subtype = ?
        """,
        (row["candidate_key"], row["campaign_id"], row["subtype"]),
    ).fetchone()
    return {
        "candidate_key": row["candidate_key"],
        "platform": row["platform"],
        "source_id": row["source_id"],
        "source_url": row["source_url"],
        "title": row["title"],
        "video_description": row["video_description"],
        "uploader": row["uploader"],
        "uploader_id": row["uploader_id"],
        "query": row["query_text"],
        "lang": row["lang"],
        "query_pack_version": row["query_pack_version"],
        "camera_pool": row["camera_pool"],
        "campaign_id": row["campaign_id"],
        "campaign_policy_version": row["campaign_policy_version"],
        "subtype": row["subtype"],
        "frontier_policy_version": row["batch_frontier_policy_version"],
        "embedding_schema_version": row["embedding_schema_version"],
        "dedupe_policy_version": row["dedupe_policy_version"],
        "secondary_batch_id": row["batch_id"],
        "frontier_priority": {
            "rank": row["rank"],
            "task_score": row["frontier_task_score"],
            "source_score": row["frontier_source_score"],
        },
        "vector_similarity": row["vector_similarity"],
        "rrf_score": row["rrf_score"],
        "secondary_decision": row["secondary_decision"],
        "secondary_filter_reasons": json.loads(row["reasons_json"]),
        "source_score": row["source_score"],
        "source_score_reasons": source_reasons,
        "task_score": task_score["score"] if task_score is not None else None,
        "task_score_reasons": task_reasons,
        "resource_eligible": bool(row["resource_eligible"]),
        "resource_policy_version": row["resource_policy_version"],
        "resource_reasons": json.loads(row["resource_reasons_json"]),
        "duration_seconds": (
            technical["duration_seconds"] if technical is not None else row["duration_seconds"]
        ),
        "resolution": resolution,
        "sha256": technical["sha256"] if technical is not None else None,
        "technical_status": row["status"],
        "technical_checks": technical_checks,
        "discovered_at": discovered,
        "queued_at": row["queued_at"],
        "downloaded_or_failed_at": attempt["finished_at"] if attempt is not None else None,
        "media_path": media_path,
        "run_id": row["run_id"],
        "adapter_version": attempt["adapter_version"] if attempt is not None else None,
        "network_config": attempt["network_config"] if attempt is not None else "default",
    }


def _score_reasons(
    database: CandidateDatabase,
    candidate_key: str,
    score_kind: str,
    campaign_id: str | None,
    subtype: str | None,
) -> list[dict]:
    rows = database.connection.execute(
        """
        SELECT rule_code, points, matched_fields_json, matched_terms_json,
               reason, policy_version
        FROM score_evidence
        WHERE candidate_key = ? AND score_kind = ?
          AND (? IS NULL OR campaign_id = ?)
          AND (? IS NULL OR subtype = ?)
        ORDER BY created_at, evidence_id
        """,
        (candidate_key, score_kind, campaign_id, campaign_id, subtype, subtype),
    ).fetchall()
    return [
        {
            "rule_code": item["rule_code"],
            "points": item["points"],
            "matched_fields": json.loads(item["matched_fields_json"]),
            "matched_terms": json.loads(item["matched_terms_json"]),
            "reason": item["reason"],
            "policy_version": item["policy_version"],
        }
        for item in rows
    ]
