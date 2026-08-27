"""Atomic JSONL audit export for an immutable Secondary Batch."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from surveillance_video_agent.db import CandidateDatabase


@dataclass(frozen=True, slots=True)
class BatchExportResult:
    batch_id: str
    output_path: Path
    record_count: int
    content_sha256: str


def export_secondary_batch(
    database: CandidateDatabase,
    batch_id: str,
    output_path: Path,
) -> BatchExportResult:
    rows = database.connection.execute(
        """
        SELECT b.run_id, b.campaign_id, b.campaign_policy_version,
               b.frontier_policy_version, i.*, d.decision,
               d.threshold, d.reasons_json, c.platform, c.source_id,
               c.source_url, c.title, c.video_description, c.uploader,
               c.uploader_id, c.source_score, c.resource_policy_version,
               f.task_score, f.attributed_query_id, f.embedding_schema_version,
               f.dedupe_policy_version, q.lang, q.query_pack_version
        FROM secondary_batches b
        JOIN secondary_batch_items i ON i.batch_id = b.batch_id
        JOIN secondary_filter_decisions d
          ON d.batch_id = i.batch_id AND d.candidate_key = i.candidate_key
        JOIN candidates c ON c.candidate_key = i.candidate_key
        LEFT JOIN frontier_entries f
          ON f.candidate_key = i.candidate_key
         AND f.campaign_id = i.campaign_id
         AND f.subtype = i.subtype
         AND f.run_id = b.run_id
        LEFT JOIN queries q ON q.query_id = f.attributed_query_id
        WHERE b.batch_id = ?
        ORDER BY i.rank, i.candidate_key
        """,
        (batch_id,),
    ).fetchall()
    if not rows:
        raise ValueError("secondary batch has no auditable items")
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    hasher = hashlib.sha256()
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            document = {
                "batch_id": batch_id,
                "run_id": row["run_id"],
                "campaign_id": row["campaign_id"],
                "campaign_policy_version": row["campaign_policy_version"],
                "frontier_policy_version": row["frontier_policy_version"],
                "candidate_key": row["candidate_key"],
                "platform": row["platform"],
                "source_id": row["source_id"],
                "source_url": row["source_url"],
                "title": row["title"],
                "video_description": row["video_description"],
                "uploader": row["uploader"],
                "uploader_id": row["uploader_id"],
                "subtype": row["subtype"],
                "rank": row["rank"],
                "source_score": row["source_score"],
                "task_score": row["task_score"],
                "vector_similarity": row["vector_similarity"],
                "rrf_score": row["rrf_score"],
                "decision": row["decision"],
                "threshold": row["threshold"],
                "decision_reasons": json.loads(row["reasons_json"]),
                "query_id": row["attributed_query_id"],
                "lang": row["lang"],
                "query_pack_version": row["query_pack_version"],
                "embedding_schema_version": row["embedding_schema_version"],
                "dedupe_policy_version": row["dedupe_policy_version"],
                "resource_policy_version": row["resource_policy_version"],
            }
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
    return BatchExportResult(
        batch_id=batch_id,
        output_path=destination,
        record_count=len(rows),
        content_sha256=hasher.hexdigest(),
    )
