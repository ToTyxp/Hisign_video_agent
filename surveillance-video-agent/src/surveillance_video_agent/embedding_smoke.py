"""Synthetic-only API smoke for qwen3.7 text embedding and local Qdrant."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any, Sequence

from surveillance_video_agent.qwen_embedding import (
    QWEN_SCHEMA,
    DashScopeQwenEmbeddingProvider,
    EmbeddingProviderError,
)
from surveillance_video_agent.smoke import network_environment_summary
from surveillance_video_agent.vector_index import QdrantVectorIndex


_SYNTHETIC_DOCUMENTS = (
    "title: CCTV heated argument without physical attack\ndescription: two people argue and walk away",
    "title: Cámara de seguridad muestra una discusión verbal\ndescription: no hubo agresión física",
    "title: Caméra de surveillance, dispute sans attaque\ndescription: les personnes se séparent",
    "title: 监控录像中的拥抱\ndescription: 两人友好接触，没有攻击",
)
_SYNTHETIC_QUERY = (
    "campaign: fight_confounder_v1\nsubtype: 冲突但未攻击\n"
    "definition: verbal confrontation or heated argument without physical attack"
)


def run_embedding_smoke(
    *,
    provider: DashScopeQwenEmbeddingProvider | Any | None = None,
    keep_temp: bool = False,
    environment=None,
) -> dict[str, Any]:
    active_provider = provider or DashScopeQwenEmbeddingProvider()
    temp_root = Path(tempfile.mkdtemp(prefix="sva-embedding-smoke-")).resolve()
    report: dict[str, Any] = {
        "ok": False,
        "model": active_provider.model_id,
        "provider": active_provider.provider_id,
        "dimensions": active_provider.dimensions,
        "schema_version": QWEN_SCHEMA.version,
        "synthetic_only": True,
        "real_candidate_metadata_sent": False,
        "network_environment": network_environment_summary(environment),
        "temp_root": str(temp_root),
        "temp_cleaned": False,
    }
    index: QdrantVectorIndex | None = None
    try:
        document_vectors = active_provider.embed_documents(_SYNTHETIC_DOCUMENTS)
        query_vector = active_provider.embed_queries((_SYNTHETIC_QUERY,))[0]
        _validate_vectors(document_vectors, query_vector, active_provider.dimensions)
        index = QdrantVectorIndex(temp_root / "qdrant")
        keys = []
        for number, vector in enumerate(document_vectors, start=1):
            key = f"youtube:synthetic{number:02d}"
            keys.append(key)
            index.upsert_candidate(
                QWEN_SCHEMA,
                candidate_key=key,
                relevance_vector=vector,
                duplicate_vector=vector,
                payload={"synthetic": True, "ordinal": number},
            )
        matches = index.query_relevance(
            QWEN_SCHEMA,
            query_vector,
            candidate_keys=keys,
            limit=len(keys),
        )
        report["document_count"] = len(document_vectors)
        report["query_count"] = 1
        report["qdrant_match_count"] = len(matches)
        report["ranked_matches"] = [
            {
                "candidate_key": item.candidate_key,
                "score": round(item.score, 6),
            }
            for item in matches
        ]
        report["ok"] = len(matches) == len(keys)
    finally:
        if index is not None:
            index.close()
        if not keep_temp:
            shutil.rmtree(temp_root)
            report["temp_cleaned"] = True
    return report


def _validate_vectors(
    document_vectors: Sequence[Sequence[float]],
    query_vector: Sequence[float],
    dimensions: int,
) -> None:
    vectors = tuple(document_vectors) + (query_vector,)
    if len(document_vectors) != len(_SYNTHETIC_DOCUMENTS):
        raise ValueError("embedding smoke document count mismatch")
    for vector in vectors:
        if len(vector) != dimensions:
            raise ValueError("embedding smoke dimension mismatch")
        norm = math.sqrt(sum(float(value) ** 2 for value in vector))
        if not math.isfinite(norm) or not math.isclose(norm, 1.0, abs_tol=1e-5):
            raise ValueError("embedding smoke vectors must be L2 normalized")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-temp", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_embedding_smoke(keep_temp=args.keep_temp)
    except EmbeddingProviderError as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_kind": error.kind.value,
                    "error": error.message,
                    "status_code": error.status_code,
                },
                ensure_ascii=False,
            )
        )
        return 2
    except (ValueError, RuntimeError) as error:
        print(
            json.dumps(
                {"ok": False, "error_kind": "smoke", "error": str(error)},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
