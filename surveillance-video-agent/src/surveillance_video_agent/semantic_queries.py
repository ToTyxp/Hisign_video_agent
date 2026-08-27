"""Versioned subtype query text and persistent Qdrant query-vector cache."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.embedding import (
    EmbeddingSchema,
    normalize_embedding_text,
    validate_provider,
)
from surveillance_video_agent.qwen_embedding import (
    DEFAULT_QUERY_INSTRUCTION,
    EmbeddingProviderError,
)
from surveillance_video_agent.vector_index import (
    QdrantVectorIndex,
    semantic_query_point_id,
)


SEMANTIC_QUERY_TEMPLATE_VERSION = "semantic-subtype-query-v1.0.0"
QUERY_INSTRUCTION_VERSION = "surveillance-retrieval-instruct-v1.0.0"
_QUERY_NAMESPACE = uuid.UUID("1ecee398-ce07-4c4e-a611-fb1d52a8709c")
_TEMPLATE_DEFINITION = {
    "template_version": SEMANTIC_QUERY_TEMPLATE_VERSION,
    "field_order": [
        "task_family",
        "campaign_id",
        "campaign_definition_zh",
        "subtype",
        "target_definition_zh",
        "core_concepts_zh",
        "scene_concepts_zh",
        "ordinary_behavior_concepts_zh",
        "action_terms_en",
        "action_terms_es",
        "action_terms_fr",
    ],
    "separator": "newline fields; list values joined by ' | '",
    "source_anchor_policy": "excluded because source gate already passed",
    "normalization": "embedding schema normalization version",
}
_TEMPLATE_JSON = json.dumps(
    _TEMPLATE_DEFINITION,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
SEMANTIC_QUERY_TEMPLATE_SHA256 = hashlib.sha256(
    _TEMPLATE_JSON.encode("utf-8")
).hexdigest()


class QueryEmbeddingProvider(Protocol):
    provider_id: str
    model_id: str
    dimensions: int

    def embed_queries(
        self,
        texts: Sequence[str],
        *,
        instruct: str,
    ) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class SemanticQuerySpec:
    query_key: str
    campaign_id: str
    subtype: str
    query_pack_version: str
    template_version: str
    embedding_schema_version: str
    instruction_version: str
    instruction_text: str
    query_text: str
    input_hash: str
    qdrant_point_id: str


@dataclass(frozen=True, slots=True)
class SemanticQueryPreparationResult:
    campaign_id: str
    query_pack_version: str
    embedding_schema_version: str
    vectors: Mapping[str, tuple[float, ...]]
    cached_count: int
    generated_count: int


def build_semantic_query_specs(
    query_pack_path: Path,
    schema: EmbeddingSchema,
) -> tuple[SemanticQuerySpec, ...]:
    document = _read_frozen_query_pack(Path(query_pack_path))
    campaign_id = _required_text(document, "campaign_id")
    query_pack_version = _required_text(document, "query_pack_version")
    semantics = document.get("frozen_semantics_zh")
    subtype_policies = semantics.get("subtypes") if isinstance(semantics, dict) else None
    queries = document.get("queries")
    if not isinstance(subtype_policies, list) or not subtype_policies:
        raise ValueError("query pack has no frozen subtype semantics")
    campaign_definition = _required_text(semantics, "campaign_definition")
    if not isinstance(queries, list) or not queries:
        raise ValueError("query pack has no multilingual queries")

    specs: list[SemanticQuerySpec] = []
    for policy in subtype_policies:
        if not isinstance(policy, dict):
            raise ValueError("subtype semantics must be objects")
        subtype = _required_text(policy, "subtype")
        target_definition = _optional_text(policy.get("target_definition"))
        terms_by_language: dict[str, list[str]] = {"en": [], "es": [], "fr": []}
        for query in queries:
            if not isinstance(query, dict) or query.get("subtype") != subtype:
                continue
            lang = query.get("lang")
            if lang not in terms_by_language:
                raise ValueError("semantic query contains an unsupported language")
            _append_unique(
                terms_by_language[lang],
                _required_text(query, "action_or_scene_term"),
            )
        if any(not terms for terms in terms_by_language.values()):
            raise ValueError(f"subtype lacks en/es/fr semantic terms: {subtype}")
        fields = (
            ("task_family", "surveillance video event relevance"),
            ("campaign_id", campaign_id),
            ("campaign_definition_zh", campaign_definition),
            ("subtype", subtype),
            ("target_definition_zh", target_definition),
            ("core_concepts_zh", _joined_terms(policy.get("core_concepts"))),
            ("scene_concepts_zh", _joined_terms(policy.get("scene_concepts"))),
            (
                "ordinary_behavior_concepts_zh",
                _joined_terms(policy.get("ordinary_behavior_concepts")),
            ),
            ("action_terms_en", " | ".join(terms_by_language["en"])),
            ("action_terms_es", " | ".join(terms_by_language["es"])),
            ("action_terms_fr", " | ".join(terms_by_language["fr"])),
        )
        query_text = normalize_embedding_text(
            "\n".join(f"{name}: {value}" for name, value in fields if value)
        )
        identity = json.dumps(
            {
                "embedding_schema_version": schema.version,
                "instruction_text": DEFAULT_QUERY_INSTRUCTION,
                "instruction_version": QUERY_INSTRUCTION_VERSION,
                "query_pack_version": query_pack_version,
                "query_text": query_text,
                "template_sha256": SEMANTIC_QUERY_TEMPLATE_SHA256,
                "template_version": SEMANTIC_QUERY_TEMPLATE_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        input_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        query_key = "semantic-query:" + str(
            uuid.uuid5(
                _QUERY_NAMESPACE,
                f"{campaign_id}:{subtype}:{query_pack_version}:"
                f"{SEMANTIC_QUERY_TEMPLATE_VERSION}:{schema.version}",
            )
        )
        specs.append(
            SemanticQuerySpec(
                query_key=query_key,
                campaign_id=campaign_id,
                subtype=subtype,
                query_pack_version=query_pack_version,
                template_version=SEMANTIC_QUERY_TEMPLATE_VERSION,
                embedding_schema_version=schema.version,
                instruction_version=QUERY_INSTRUCTION_VERSION,
                instruction_text=DEFAULT_QUERY_INSTRUCTION,
                query_text=query_text,
                input_hash=input_hash,
                qdrant_point_id=semantic_query_point_id(schema.version, query_key),
            )
        )
    return tuple(specs)


class SemanticQueryVectorService:
    def __init__(
        self,
        database: CandidateDatabase,
        index: QdrantVectorIndex,
        provider: QueryEmbeddingProvider,
        schema: EmbeddingSchema,
    ) -> None:
        validate_provider(schema, provider)
        self.database = database
        self.index = index
        self.provider = provider
        self.schema = schema
        self.database.register_embedding_schema(schema)

    def prepare(
        self,
        *,
        run_id: str,
        query_pack_path: Path,
    ) -> SemanticQueryPreparationResult:
        self._validate_run(run_id)
        query_pack_version = self.database.register_frozen_query_pack(query_pack_path)
        specs = build_semantic_query_specs(query_pack_path, self.schema)
        if any(spec.query_pack_version != query_pack_version for spec in specs):
            raise ValueError("registered query pack identity changed during preparation")
        self._register_template()
        self._reserve_specs(specs, run_id=run_id)

        vectors: dict[str, tuple[float, ...]] = {}
        stale: list[SemanticQuerySpec] = []
        for spec in specs:
            row = self.database.connection.execute(
                "SELECT * FROM subtype_semantic_queries WHERE query_key = ?",
                (spec.query_key,),
            ).fetchone()
            if row is None or row["input_hash"] != spec.input_hash:
                raise RuntimeError("semantic query control row is inconsistent")
            cached = None
            if row["index_status"] == "ready":
                cached = self.index.get_semantic_query_vector(
                    self.schema,
                    query_key=spec.query_key,
                )
            if cached is None:
                stale.append(spec)
            else:
                vectors[spec.subtype] = tuple(cached)

        if stale:
            generated = self._generate_and_index(stale, run_id=run_id)
            vectors.update(generated)
        return SemanticQueryPreparationResult(
            campaign_id=specs[0].campaign_id,
            query_pack_version=query_pack_version,
            embedding_schema_version=self.schema.version,
            vectors=MappingProxyType(dict(vectors)),
            cached_count=len(specs) - len(stale),
            generated_count=len(stale),
        )

    def _register_template(self) -> None:
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT content_sha256 FROM semantic_query_templates
                WHERE template_version = ?
                """,
                (SEMANTIC_QUERY_TEMPLATE_VERSION,),
            ).fetchone()
            if existing is not None:
                if existing["content_sha256"] != SEMANTIC_QUERY_TEMPLATE_SHA256:
                    raise ValueError("semantic query template version changed content")
                return
            connection.execute(
                """
                INSERT INTO semantic_query_templates(
                    template_version, status, content_sha256,
                    content_json, created_at
                ) VALUES (?, 'frozen', ?, ?, ?)
                """,
                (
                    SEMANTIC_QUERY_TEMPLATE_VERSION,
                    SEMANTIC_QUERY_TEMPLATE_SHA256,
                    _TEMPLATE_JSON,
                    utc_now(),
                ),
            )

    def _reserve_specs(
        self,
        specs: Sequence[SemanticQuerySpec],
        *,
        run_id: str,
    ) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            for spec in specs:
                existing = connection.execute(
                    "SELECT input_hash FROM subtype_semantic_queries WHERE query_key = ?",
                    (spec.query_key,),
                ).fetchone()
                if existing is not None:
                    if existing["input_hash"] != spec.input_hash:
                        raise ValueError("semantic query key changed input content")
                    continue
                connection.execute(
                    """
                    INSERT INTO subtype_semantic_queries(
                        query_key, campaign_id, subtype, query_pack_version,
                        template_version, embedding_schema_version,
                        instruction_version, instruction_text, query_text,
                        input_hash, qdrant_point_id, index_status,
                        created_run_id, updated_run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        spec.query_key,
                        spec.campaign_id,
                        spec.subtype,
                        spec.query_pack_version,
                        spec.template_version,
                        spec.embedding_schema_version,
                        spec.instruction_version,
                        spec.instruction_text,
                        spec.query_text,
                        spec.input_hash,
                        spec.qdrant_point_id,
                        run_id,
                        run_id,
                        now,
                    ),
                )

    def _generate_and_index(
        self,
        specs: Sequence[SemanticQuerySpec],
        *,
        run_id: str,
    ) -> dict[str, tuple[float, ...]]:
        with self.database.transaction() as connection:
            for spec in specs:
                connection.execute(
                    """
                    UPDATE subtype_semantic_queries
                    SET index_status = 'pending', indexed_at = NULL,
                        error_kind = NULL, updated_run_id = ?
                    WHERE query_key = ?
                    """,
                    (run_id, spec.query_key),
                )
        call_id = str(uuid.uuid4())
        started_at = utc_now()
        try:
            generated = self.provider.embed_queries(
                [spec.query_text for spec in specs],
                instruct=DEFAULT_QUERY_INSTRUCTION,
            )
            if len(generated) != len(specs):
                raise ValueError("query embedding provider returned wrong vector count")
            for vector in generated:
                if len(vector) != self.schema.dimensions:
                    raise ValueError("query embedding provider returned wrong dimensions")
        except Exception as error:
            kind = (
                error.kind.value
                if isinstance(error, EmbeddingProviderError)
                else "provider_error"
            )
            status_code = (
                error.status_code if isinstance(error, EmbeddingProviderError) else None
            )
            finished_at = utc_now()
            self.database.record_embedding_call(
                call_id=call_id,
                run_id=run_id,
                embedding_schema_version=self.schema.version,
                provider=self.schema.provider,
                model=self.schema.model,
                operation="subtype_queries",
                input_hashes=[spec.input_hash for spec in specs],
                status="failed",
                error_kind=kind,
                status_code=status_code,
                started_at=started_at,
                finished_at=finished_at,
            )
            with self.database.transaction() as connection:
                for spec in specs:
                    connection.execute(
                        """
                        UPDATE subtype_semantic_queries
                        SET index_status = 'failed', indexed_at = NULL,
                            error_kind = ?, updated_run_id = ?
                        WHERE query_key = ?
                        """,
                        (kind, run_id, spec.query_key),
                    )
            raise

        self.database.record_embedding_call(
            call_id=call_id,
            run_id=run_id,
            embedding_schema_version=self.schema.version,
            provider=self.schema.provider,
            model=self.schema.model,
            operation="subtype_queries",
            input_hashes=[spec.input_hash for spec in specs],
            status="succeeded",
            started_at=started_at,
            finished_at=utc_now(),
        )
        result: dict[str, tuple[float, ...]] = {}
        for spec, vector in zip(specs, generated, strict=True):
            try:
                point_id = self.index.upsert_semantic_query(
                    self.schema,
                    query_key=spec.query_key,
                    query_vector=vector,
                    payload={
                        "campaign_id": spec.campaign_id,
                        "subtype": spec.subtype,
                        "query_pack_version": spec.query_pack_version,
                        "template_version": spec.template_version,
                        "input_hash": spec.input_hash,
                    },
                )
                if point_id != spec.qdrant_point_id:
                    raise RuntimeError("semantic query point identity changed")
            except Exception:
                with self.database.transaction() as connection:
                    connection.execute(
                        """
                        UPDATE subtype_semantic_queries
                        SET index_status = 'failed', indexed_at = NULL,
                            error_kind = 'qdrant', updated_run_id = ?
                        WHERE query_key = ?
                        """,
                        (run_id, spec.query_key),
                    )
                raise
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE subtype_semantic_queries
                    SET index_status = 'ready', indexed_at = ?,
                        error_kind = NULL, updated_run_id = ?
                    WHERE query_key = ?
                    """,
                    (utc_now(), run_id, spec.query_key),
                )
            result[spec.subtype] = tuple(float(value) for value in vector)
        return result

    def _validate_run(self, run_id: str) -> None:
        row = self.database.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or row["status"] != "running":
            raise ValueError("an existing running run_id is required")


def _read_frozen_query_pack(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"query pack not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid query pack JSON: {path}") from error
    if not isinstance(document, dict) or document.get("status") != "frozen":
        raise ValueError("semantic queries require a frozen query pack")
    queries = document.get("queries")
    if not isinstance(queries, list):
        raise ValueError("query pack has no queries")
    canonical = json.dumps(
        queries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != _required_text(
        document, "content_sha256"
    ):
        raise ValueError("frozen query pack hash does not match")
    return document


def _required_text(document: Mapping[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing text field: {key}")
    return value


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or not value:
        raise ValueError("optional semantic definition must be non-empty text")
    return value


def _joined_terms(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError("semantic concept terms must be non-empty strings")
    return " | ".join(dict.fromkeys(value))


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
