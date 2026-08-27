"""Human-feedback centroid direction used only for Frontier ranking."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.vector_index import QdrantVectorIndex


def build_feedback_ranking_vectors(
    database: CandidateDatabase,
    index: QdrantVectorIndex,
    schema: EmbeddingSchema,
    *,
    campaign_id: str,
    base_vectors: Mapping[str, Sequence[float]],
    task_weight: float,
    source_weight: float,
) -> dict[str, list[float]]:
    """Blend label centroids with base vectors without changing gate vectors."""

    if task_weight < 0 or source_weight < 0:
        raise ValueError("feedback weights must be non-negative")
    if task_weight == 0 and source_weight == 0:
        return {key: list(vector) for key, vector in base_vectors.items()}
    rows = database.connection.execute(
        """
        WITH latest AS (
            SELECT l.*, i.imported_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY l.candidate_key, l.campaign_id, l.shown_subtype
                       ORDER BY i.imported_at DESC, l.import_id DESC
                   ) AS row_number
            FROM pilot_feedback_labels l
            JOIN pilot_feedback_imports i ON i.import_id = l.import_id
            WHERE l.campaign_id = ?
        )
        SELECT candidate_key, shown_subtype, source_correct, task_usable
        FROM latest WHERE row_number = 1
        ORDER BY shown_subtype, candidate_key
        """,
        (campaign_id,),
    ).fetchall()
    by_subtype: dict[str, list[tuple[object, list[float]]]] = {}
    for row in rows:
        vector = index.get_calibration_candidate_vector(
            schema, candidate_key=row["candidate_key"]
        )
        if vector is not None:
            by_subtype.setdefault(row["shown_subtype"], []).append((row, vector))
    result = {}
    for subtype, base in base_vectors.items():
        labeled = by_subtype.get(subtype, [])
        task_positive = [v for row, v in labeled if row["task_usable"] == 1]
        task_negative = [v for row, v in labeled if row["task_usable"] == 0]
        source_positive = [v for row, v in labeled if row["source_correct"] == 1]
        source_negative = [v for row, v in labeled if row["source_correct"] == 0]
        if min(
            len(task_positive),
            len(task_negative),
            len(source_positive),
            len(source_negative),
        ) < 2:
            result[subtype] = list(base)
            continue
        task_direction = _subtract(_centroid(task_positive), _centroid(task_negative))
        source_direction = _subtract(
            _centroid(source_positive), _centroid(source_negative)
        )
        result[subtype] = _normalize(
            [
                float(value)
                + task_weight * task_direction[dimension]
                + source_weight * source_direction[dimension]
                for dimension, value in enumerate(base)
            ]
        )
    return result


def _centroid(vectors: list[list[float]]) -> list[float]:
    return _normalize(
        [sum(values) / len(vectors) for values in zip(*vectors, strict=True)]
    )


def _subtract(left: list[float], right: list[float]) -> list[float]:
    return [a - b for a, b in zip(left, right, strict=True)]


def _normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) ** 2 for value in vector))
    if norm == 0:
        raise ValueError("cannot normalize a zero vector")
    return [float(value) / norm for value in vector]
