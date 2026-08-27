"""Versioned text construction and embedding provider boundary."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Protocol, Sequence

from surveillance_video_agent.scoring.models import CandidateMetadata


@dataclass(frozen=True, slots=True)
class EmbeddingSchema:
    version: str
    provider: str
    model: str
    dimensions: int
    distance: str = "cosine"
    text_template_version: str = "metadata-v1"
    normalization_version: str = "unicode-nfc-whitespace-v1"

    def __post_init__(self) -> None:
        if not self.version or not self.provider or not self.model:
            raise ValueError("embedding schema identity fields are required")
        if self.dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")
        if self.distance not in {"cosine", "dot", "euclid"}:
            raise ValueError("unsupported embedding distance")


class EmbeddingProvider(Protocol):
    provider_id: str
    model_id: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class CandidateEmbeddingInput:
    relevance_text: str
    duplicate_text: str
    input_hash: str


def build_candidate_embedding_input(
    candidate: CandidateMetadata,
    schema: EmbeddingSchema,
) -> CandidateEmbeddingInput:
    relevance = _join_fields(
        (
            ("title", candidate.title),
            ("description", candidate.video_description),
            ("tags", " | ".join(candidate.tags)),
            ("channel", candidate.channel),
            ("playlist", candidate.playlist),
        )
    )
    duplicate = _join_fields(
        (
            ("title", candidate.title),
            ("description", candidate.video_description),
        )
    )
    identity = json.dumps(
        {
            "schema": schema.version,
            "template": schema.text_template_version,
            "normalization": schema.normalization_version,
            "relevance": relevance,
            "duplicate": duplicate,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return CandidateEmbeddingInput(
        relevance_text=relevance,
        duplicate_text=duplicate,
        input_hash=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
    )


def validate_provider(schema: EmbeddingSchema, provider: EmbeddingProvider) -> None:
    if provider.provider_id != schema.provider:
        raise ValueError("embedding provider does not match schema")
    if provider.model_id != schema.model:
        raise ValueError("embedding model does not match schema")
    if provider.dimensions != schema.dimensions:
        raise ValueError("embedding dimensions do not match schema")


def _join_fields(fields: Sequence[tuple[str, str]]) -> str:
    normalized = (
        (name, normalize_embedding_text(value)) for name, value in fields
    )
    return "\n".join(f"{name}: {value}" for name, value in normalized if value)


def normalize_embedding_text(value: str) -> str:
    """Normalize text exactly as identified by ``unicode-nfc-whitespace-v1``."""

    if not isinstance(value, str):
        raise TypeError("embedding text must be a string")
    canonical = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    lines = [" ".join(line.split()) for line in canonical.split("\n")]
    return "\n".join(line for line in lines if line)
