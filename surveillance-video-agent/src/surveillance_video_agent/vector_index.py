"""Qdrant local-mode implementation of the derived vector index."""

from __future__ import annotations

import gc
import hashlib
import re
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from qdrant_client import QdrantClient, models

from surveillance_video_agent.embedding import EmbeddingSchema


_POINT_NAMESPACE = uuid.UUID("f57c0147-77c4-4f51-9cd0-99184b70bc04")
_QUERY_POINT_NAMESPACE = uuid.UUID("5b53b85b-27cd-41ad-b4a4-52b30ed94241")
_CALIBRATION_POINT_NAMESPACE = uuid.UUID("c2f72909-4627-4819-83be-60d8da53b77e")


@dataclass(frozen=True, slots=True)
class VectorMatch:
    candidate_key: str
    score: float
    point_id: str


class QdrantVectorIndex:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        self.client = QdrantClient(path=str(self.path))

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "QdrantVectorIndex":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def ensure_collection(self, schema: EmbeddingSchema) -> str:
        name = collection_name(schema.version)
        if not self.client.collection_exists(name):
            params = models.VectorParams(
                size=schema.dimensions,
                distance=_distance(schema.distance),
            )
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="unclosed database.*",
                    category=ResourceWarning,
                )
                self.client.create_collection(
                    collection_name=name,
                    vectors_config={"relevance": params, "duplicate": params},
                )
                # qdrant-client 1.19 local persistence creates a temporary
                # sqlite connection whose ResourceWarning is deferred to GC.
                gc.collect()
        return name

    def ensure_calibration_collection(self, schema: EmbeddingSchema) -> str:
        name = calibration_collection_name(schema.version)
        if not self.client.collection_exists(name):
            params = models.VectorParams(
                size=schema.dimensions,
                distance=_distance(schema.distance),
            )
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="unclosed database.*",
                    category=ResourceWarning,
                )
                self.client.create_collection(
                    collection_name=name,
                    vectors_config={"relevance": params},
                )
                gc.collect()
        return name

    def upsert_candidate(
        self,
        schema: EmbeddingSchema,
        *,
        candidate_key: str,
        relevance_vector: Sequence[float],
        duplicate_vector: Sequence[float],
        payload: Mapping[str, object],
    ) -> str:
        _validate_vector(relevance_vector, schema.dimensions)
        _validate_vector(duplicate_vector, schema.dimensions)
        name = self.ensure_collection(schema)
        point_id = point_id_for(schema.version, candidate_key)
        self.client.upsert(
            collection_name=name,
            wait=True,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={
                        "relevance": list(relevance_vector),
                        "duplicate": list(duplicate_vector),
                    },
                    payload={"candidate_key": candidate_key, **dict(payload)},
                )
            ],
        )
        return point_id

    def query_relevance(
        self,
        schema: EmbeddingSchema,
        query_vector: Sequence[float],
        *,
        candidate_keys: Sequence[str],
        limit: int,
        score_threshold: float | None = None,
    ) -> tuple[VectorMatch, ...]:
        return self._query(
            schema,
            "relevance",
            query_vector,
            candidate_keys=candidate_keys,
            limit=limit,
            score_threshold=score_threshold,
        )

    def upsert_semantic_query(
        self,
        schema: EmbeddingSchema,
        *,
        query_key: str,
        query_vector: Sequence[float],
        payload: Mapping[str, object],
    ) -> str:
        _validate_vector(query_vector, schema.dimensions)
        name = self.ensure_collection(schema)
        point_id = semantic_query_point_id(schema.version, query_key)
        self.client.upsert(
            collection_name=name,
            wait=True,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={"relevance": list(query_vector)},
                    payload={
                        "point_kind": "semantic_query",
                        "query_key": query_key,
                        **dict(payload),
                    },
                )
            ],
        )
        return point_id

    def get_semantic_query_vector(
        self,
        schema: EmbeddingSchema,
        *,
        query_key: str,
    ) -> list[float] | None:
        name = self.ensure_collection(schema)
        point_id = semantic_query_point_id(schema.version, query_key)
        records = self.client.retrieve(
            collection_name=name,
            ids=[point_id],
            with_payload=False,
            with_vectors=["relevance"],
        )
        if not records:
            return None
        vectors = records[0].vector
        if not isinstance(vectors, dict):
            return None
        vector = vectors.get("relevance")
        if not isinstance(vector, list):
            return None
        _validate_vector(vector, schema.dimensions)
        return [float(value) for value in vector]

    def upsert_calibration_candidate(
        self,
        schema: EmbeddingSchema,
        *,
        candidate_key: str,
        relevance_vector: Sequence[float],
        payload: Mapping[str, object],
    ) -> str:
        _validate_vector(relevance_vector, schema.dimensions)
        point_id = calibration_point_id(schema.version, candidate_key)
        self.client.upsert(
            collection_name=self.ensure_calibration_collection(schema),
            wait=True,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={"relevance": list(relevance_vector)},
                    payload={
                        "candidate_key": candidate_key,
                        "point_kind": "calibration_candidate",
                        **dict(payload),
                    },
                )
            ],
        )
        return point_id

    def query_calibration_relevance(
        self,
        schema: EmbeddingSchema,
        query_vector: Sequence[float],
        *,
        candidate_keys: Sequence[str],
        limit: int,
    ) -> tuple[VectorMatch, ...]:
        if limit <= 0 or not candidate_keys:
            return ()
        _validate_vector(query_vector, schema.dimensions)
        response = self.client.query_points(
            collection_name=self.ensure_calibration_collection(schema),
            query=list(query_vector),
            using="relevance",
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="candidate_key",
                        match=models.MatchAny(any=list(candidate_keys)),
                    )
                ]
            ),
            limit=min(limit, len(candidate_keys)),
            with_payload=["candidate_key"],
            with_vectors=False,
        )
        matches = []
        for point in response.points:
            candidate_key = (point.payload or {}).get("candidate_key")
            if isinstance(candidate_key, str):
                matches.append(
                    VectorMatch(candidate_key, float(point.score), str(point.id))
                )
        return tuple(matches)

    def has_calibration_candidate(
        self,
        schema: EmbeddingSchema,
        *,
        candidate_key: str,
    ) -> bool:
        records = self.client.retrieve(
            collection_name=self.ensure_calibration_collection(schema),
            ids=[calibration_point_id(schema.version, candidate_key)],
            with_payload=False,
            with_vectors=False,
        )
        return bool(records)

    def get_calibration_candidate_vector(
        self,
        schema: EmbeddingSchema,
        *,
        candidate_key: str,
    ) -> list[float] | None:
        records = self.client.retrieve(
            collection_name=self.ensure_calibration_collection(schema),
            ids=[calibration_point_id(schema.version, candidate_key)],
            with_payload=False,
            with_vectors=["relevance"],
        )
        if not records or not isinstance(records[0].vector, dict):
            return None
        vector = records[0].vector.get("relevance")
        if not isinstance(vector, list):
            return None
        _validate_vector(vector, schema.dimensions)
        return [float(value) for value in vector]

    def upsert_candidate_relevance_only(
        self,
        schema: EmbeddingSchema,
        *,
        candidate_key: str,
        relevance_vector: Sequence[float],
        payload: Mapping[str, object],
    ) -> str:
        _validate_vector(relevance_vector, schema.dimensions)
        point_id = point_id_for(schema.version, candidate_key)
        self.client.upsert(
            collection_name=self.ensure_collection(schema),
            wait=True,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={"relevance": list(relevance_vector)},
                    payload={"candidate_key": candidate_key, **dict(payload)},
                )
            ],
        )
        return point_id

    def query_duplicate_neighbors(
        self,
        schema: EmbeddingSchema,
        candidate_key: str,
        *,
        candidate_keys: Sequence[str],
        limit: int,
        score_threshold: float | None = None,
    ) -> tuple[VectorMatch, ...]:
        name = self.ensure_collection(schema)
        point_id = point_id_for(schema.version, candidate_key)
        records = self.client.retrieve(
            collection_name=name,
            ids=[point_id],
            with_payload=True,
            with_vectors=["duplicate"],
        )
        if not records or not isinstance(records[0].vector, dict):
            raise ValueError(f"candidate vector not found: {candidate_key}")
        vector = records[0].vector.get("duplicate")
        if not isinstance(vector, list):
            raise ValueError(f"duplicate vector not found: {candidate_key}")
        matches = self._query(
            schema,
            "duplicate",
            vector,
            candidate_keys=candidate_keys,
            limit=limit + 1,
            score_threshold=score_threshold,
        )
        return tuple(item for item in matches if item.candidate_key != candidate_key)[:limit]

    def _query(
        self,
        schema: EmbeddingSchema,
        vector_name: str,
        query_vector: Sequence[float],
        *,
        candidate_keys: Sequence[str],
        limit: int,
        score_threshold: float | None,
    ) -> tuple[VectorMatch, ...]:
        if limit <= 0 or not candidate_keys:
            return ()
        _validate_vector(query_vector, schema.dimensions)
        response = self.client.query_points(
            collection_name=self.ensure_collection(schema),
            query=list(query_vector),
            using=vector_name,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="candidate_key",
                        match=models.MatchAny(any=list(candidate_keys)),
                    )
                ]
            ),
            limit=limit,
            score_threshold=score_threshold,
            with_payload=["candidate_key"],
            with_vectors=False,
        )
        results: list[VectorMatch] = []
        for point in response.points:
            candidate_key = (point.payload or {}).get("candidate_key")
            if isinstance(candidate_key, str):
                results.append(VectorMatch(candidate_key, float(point.score), str(point.id)))
        return tuple(results)


def collection_name(schema_version: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", schema_version).strip("_")[:32]
    digest = hashlib.sha256(schema_version.encode("utf-8")).hexdigest()[:10]
    return f"sva_{slug or 'schema'}_{digest}"


def calibration_collection_name(schema_version: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", schema_version).strip("_")[:24]
    digest = hashlib.sha256(schema_version.encode("utf-8")).hexdigest()[:10]
    return f"sva_cal_{slug or 'schema'}_{digest}"


def point_id_for(schema_version: str, candidate_key: str) -> str:
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{schema_version}:{candidate_key}"))


def semantic_query_point_id(schema_version: str, query_key: str) -> str:
    return str(
        uuid.uuid5(_QUERY_POINT_NAMESPACE, f"{schema_version}:{query_key}")
    )


def calibration_point_id(schema_version: str, candidate_key: str) -> str:
    return str(
        uuid.uuid5(
            _CALIBRATION_POINT_NAMESPACE,
            f"{schema_version}:{candidate_key}",
        )
    )


def _distance(value: str) -> models.Distance:
    return {
        "cosine": models.Distance.COSINE,
        "dot": models.Distance.DOT,
        "euclid": models.Distance.EUCLID,
    }[value]


def _validate_vector(vector: Sequence[float], dimensions: int) -> None:
    if len(vector) != dimensions:
        raise ValueError(f"expected {dimensions} vector dimensions, got {len(vector)}")
    if any(not isinstance(value, (int, float)) for value in vector):
        raise ValueError("vectors must contain only numbers")
