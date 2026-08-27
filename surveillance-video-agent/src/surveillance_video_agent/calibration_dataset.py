"""Deterministic metadata-only threshold-label export and labeled import."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from surveillance_video_agent.calibration import CalibrationExample
from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.vector_index import QdrantVectorIndex


@dataclass(frozen=True, slots=True)
class CalibrationExportResult:
    export_id: str
    output_path: Path
    content_sha256: str
    record_count: int
    subtype_counts: Mapping[str, int]


def export_calibration_dataset(
    database: CandidateDatabase,
    index: QdrantVectorIndex,
    schema: EmbeddingSchema,
    *,
    run_id: str,
    campaign_id: str,
    query_pack_version: str,
    query_vectors: Mapping[str, Sequence[float]],
    output_path: Path,
) -> CalibrationExportResult:
    run = database.connection.execute(
        "SELECT status FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run is None or run["status"] != "running":
        raise ValueError("an existing running run_id is required")
    rows = database.connection.execute(
        """
        SELECT c.*, t.subtype, t.score AS task_score
        FROM candidates c
        JOIN candidate_task_scores t ON t.candidate_key = c.candidate_key
        WHERE c.status = 'source_qualified'
          AND c.resource_eligible = 1
          AND t.campaign_id = ? AND t.qualified = 1 AND t.score >= 4
          AND 2 = (
              SELECT COUNT(*) FROM candidate_embeddings e
              WHERE e.candidate_key = c.candidate_key
                AND e.embedding_schema_version = ?
                AND e.index_status = 'ready'
                AND e.indexed_input_hash = e.current_input_hash
          )
          AND EXISTS (
              SELECT 1 FROM candidate_discoveries d
              JOIN queries q ON q.query_id = d.query_id
              WHERE d.candidate_key = c.candidate_key
                AND q.query_pack_version = ?
          )
        ORDER BY t.subtype, c.candidate_key
        """,
        (campaign_id, schema.version, query_pack_version),
    ).fetchall()
    documents = []
    subtype_counts: dict[str, int] = {}
    for row in rows:
        subtype = row["subtype"]
        query_vector = query_vectors.get(subtype)
        if query_vector is None:
            raise ValueError(f"missing subtype query vector: {subtype}")
        matches = index.query_relevance(
            schema,
            query_vector,
            candidate_keys=[row["candidate_key"]],
            limit=1,
        )
        if len(matches) != 1 or matches[0].candidate_key != row["candidate_key"]:
            raise RuntimeError("ready candidate relevance vector was not retrievable")
        attribution = database.connection.execute(
            """
            SELECT q.query_id, q.query_text, q.lang, d.platform_position,
                   d.discovered_at
            FROM candidate_discoveries d
            JOIN queries q ON q.query_id = d.query_id
            WHERE d.candidate_key = ? AND q.query_pack_version = ?
            ORDER BY d.platform_position, d.discovered_at, q.query_id
            LIMIT 1
            """,
            (row["candidate_key"], query_pack_version),
        ).fetchone()
        uploader_identity = (
            row["uploader_id"]
            or row["uploader"]
            or row["channel"]
            or row["candidate_key"]
        )
        documents.append(
            {
                "candidate_key": row["candidate_key"],
                "campaign_id": campaign_id,
                "subtype": subtype,
                "uploader_identity": uploader_identity,
                "platform": row["platform"],
                "lang": attribution["lang"],
                "query_id": attribution["query_id"],
                "query": attribution["query_text"],
                "query_pack_version": query_pack_version,
                "title": row["title"],
                "video_description": row["video_description"],
                "tags": json.loads(row["tags_json"]),
                "source_score": row["source_score"],
                "task_score": row["task_score"],
                "duration_seconds": row["duration_seconds"],
                "similarity": matches[0].score,
                "embedding_schema_version": schema.version,
                "usable": None,
                "label_notes": None,
            }
        )
        subtype_counts[subtype] = subtype_counts.get(subtype, 0) + 1
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    hasher = hashlib.sha256()
    with temporary.open("w", encoding="utf-8") as handle:
        for document in documents:
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
    digest = hasher.hexdigest()
    export_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{run_id}:{campaign_id}:{query_pack_version}:"
            f"{schema.version}:{destination}:{digest}",
        )
    )
    subtype_json = json.dumps(
        subtype_counts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO calibration_exports(
                export_id, run_id, campaign_id, query_pack_version,
                embedding_schema_version, output_path, content_sha256,
                record_count, subtype_counts_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                export_id,
                run_id,
                campaign_id,
                query_pack_version,
                schema.version,
                str(destination),
                digest,
                len(documents),
                subtype_json,
                utc_now(),
            ),
        )
    return CalibrationExportResult(
        export_id=export_id,
        output_path=destination,
        content_sha256=digest,
        record_count=len(documents),
        subtype_counts=dict(subtype_counts),
    )


def load_labeled_calibration_dataset(path: Path) -> tuple[CalibrationExample, ...]:
    source = Path(path).resolve()
    examples: list[CalibrationExample] = []
    seen: set[tuple[str, str]] = set()
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        raise ValueError(f"labeled calibration file not found: {source}") from error
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at line {line_number}") from error
        if not isinstance(item, dict) or not isinstance(item.get("usable"), bool):
            raise ValueError(f"line {line_number} requires boolean usable label")
        key = (str(item.get("candidate_key")), str(item.get("subtype")))
        if key in seen:
            raise ValueError("labeled calibration rows must be unique per candidate/subtype")
        seen.add(key)
        examples.append(
            CalibrationExample(
                candidate_key=_text(item, "candidate_key", line_number),
                campaign_id=_text(item, "campaign_id", line_number),
                subtype=_text(item, "subtype", line_number),
                uploader_identity=_text(item, "uploader_identity", line_number),
                platform=_text(item, "platform", line_number),
                lang=_text(item, "lang", line_number),
                similarity=float(item.get("similarity")),
                usable=item["usable"],
            )
        )
    if not examples:
        raise ValueError("labeled calibration dataset is empty")
    return tuple(examples)


def _text(item: dict, key: str, line_number: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"line {line_number} is missing {key}")
    return value
