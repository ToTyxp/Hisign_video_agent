"""Evaluate label-centroid reranking without mutating the Frontier."""

from __future__ import annotations

import json
import math

from surveillance_video_agent.cli import QDRANT_PATH, QWEN_SCHEMA, STATE_DB
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.vector_index import QdrantVectorIndex


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(_dot(vector, vector))
    if norm == 0:
        raise ValueError("cannot normalize a zero vector")
    return [value / norm for value in vector]


def _centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("centroid requires at least one vector")
    return _normalize(
        [sum(values) / len(vectors) for values in zip(*vectors, strict=True)]
    )


def _loo_margin(
    candidate_key: str,
    vector: list[float],
    labels: dict[str, bool | None],
    vectors: dict[str, list[float]],
) -> float:
    positives = [
        vectors[key]
        for key, value in labels.items()
        if value is True and key != candidate_key and key in vectors
    ]
    negatives = [
        vectors[key]
        for key, value in labels.items()
        if value is False and key != candidate_key and key in vectors
    ]
    return _dot(vector, _centroid(positives)) - _dot(vector, _centroid(negatives))


def _rate(keys: list[str], labels: dict[str, bool | None]) -> float:
    return sum(labels.get(key) is True for key in keys) / len(keys)


def main() -> None:
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(QDRANT_PATH) as index:
        rows = database.connection.execute(
            """
            WITH latest AS (
                SELECT l.*, i.imported_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY l.candidate_key, l.campaign_id
                           ORDER BY i.imported_at DESC, l.import_id DESC
                       ) AS row_number
                FROM pilot_feedback_labels l
                JOIN pilot_feedback_imports i ON i.import_id = l.import_id
                WHERE l.campaign_id = 'sign_action_v1'
            )
            SELECT candidate_key, source_correct, task_usable
            FROM latest WHERE row_number = 1 ORDER BY candidate_key
            """
        ).fetchall()
        query = database.connection.execute(
            """
            SELECT query_key FROM subtype_semantic_queries
            WHERE campaign_id = 'sign_action_v1' AND subtype = '举牌/横幅'
              AND query_pack_version = 'sign_action_v1.qp.v1.7.0'
              AND embedding_schema_version = ? AND index_status = 'ready'
            """,
            (QWEN_SCHEMA.version,),
        ).fetchone()
        if query is None:
            raise ValueError("ready sign v1.7 semantic query is required")
        base = index.get_semantic_query_vector(QWEN_SCHEMA, query_key=query["query_key"])
        if base is None:
            raise ValueError("sign v1.7 query vector is missing")
        vectors = {}
        for row in rows:
            vector = index.get_calibration_candidate_vector(
                QWEN_SCHEMA, candidate_key=row["candidate_key"]
            )
            if vector is not None:
                vectors[row["candidate_key"]] = vector
        source_labels = {
            row["candidate_key"]: (
                None if row["source_correct"] is None else bool(row["source_correct"])
            )
            for row in rows
            if row["candidate_key"] in vectors
        }
        task_labels = {
            row["candidate_key"]: (
                None if row["task_usable"] is None else bool(row["task_usable"])
            )
            for row in rows
            if row["candidate_key"] in vectors
        }
        base_scores = {key: _dot(vector, base) for key, vector in vectors.items()}
        source_margins = {
            key: _loo_margin(key, vector, source_labels, vectors)
            for key, vector in vectors.items()
        }
        task_margins = {
            key: _loo_margin(key, vector, task_labels, vectors)
            for key, vector in vectors.items()
        }
        grid = []
        for task_weight in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5):
            for source_weight in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5):
                ordered = sorted(
                    vectors,
                    key=lambda key: (
                        -(
                            base_scores[key]
                            + task_weight * task_margins[key]
                            + source_weight * source_margins[key]
                        ),
                        key,
                    ),
                )
                top = ordered[: min(20, len(ordered))]
                source_rate = _rate(top, source_labels)
                task_rate = _rate(top, task_labels)
                grid.append(
                    {
                        "task_weight": task_weight,
                        "source_weight": source_weight,
                        "task_rate_top20": task_rate,
                        "source_rate_top20": source_rate,
                        "joint_objective": task_rate + source_rate,
                    }
                )
        grid.sort(
            key=lambda item: (
                -item["joint_objective"],
                -item["source_rate_top20"],
                -item["task_rate_top20"],
                item["task_weight"] + item["source_weight"],
            )
        )
        baseline = next(
            item
            for item in grid
            if item["task_weight"] == 0 and item["source_weight"] == 0
        )
        print(
            json.dumps(
                {
                    "label_count": len(rows),
                    "vector_count": len(vectors),
                    "baseline": baseline,
                    "best": grid[0],
                    "top_five": grid[:5],
                    "evaluation": "leave-one-out label centroids; top-20; null labels count as not usable/correct",
                    "mutated_frontier": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
