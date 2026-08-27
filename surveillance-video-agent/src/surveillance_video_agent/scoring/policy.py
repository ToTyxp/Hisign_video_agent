"""Load a versioned scoring policy plus frozen query-pack vocabularies."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class TaskConjunctionGroup:
    left_terms: tuple[str, ...]
    right_terms: tuple[str, ...]
    marker: str


@dataclass(frozen=True, slots=True)
class TaskVocabulary:
    campaign_id: str
    subtype: str
    direct_terms: tuple[str, ...]
    scene_terms_zh: tuple[str, ...] = ()
    ordinary_terms_zh: tuple[str, ...] = ()
    conjunction_groups: tuple[TaskConjunctionGroup, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    max_direct_participants: int | None = None


@dataclass(frozen=True, slots=True)
class ScoringBundle:
    policy_version: str
    concept_pack_version: str
    source_title_anchors: tuple[str, ...]
    source_metadata_terms: tuple[str, ...]
    rawness_terms: tuple[str, ...]
    packaging_terms: tuple[str, ...]
    hard_exclusions: Mapping[str, tuple[str, ...]]
    tasks: Mapping[tuple[str, str], TaskVocabulary]
    query_pack_versions: tuple[str, ...]


def load_scoring_bundle(
    policy_path: Path,
    query_pack_paths: tuple[Path, ...],
    *,
    allow_draft_policy: bool = False,
) -> ScoringBundle:
    policy = _read_json(Path(policy_path))
    expected_status = {"frozen"} | ({"draft"} if allow_draft_policy else set())
    if policy.get("status") not in expected_status:
        raise ValueError("scoring policy must be frozen")
    if policy.get("status") == "frozen":
        _verify_policy_hash(policy)
    version = _required_text(policy, "policy_version")
    concept_version = _required_text(policy, "concept_pack_version")
    concept_hash = _required_text(policy, "concept_source_sha256")
    compatible_concepts = policy.get("compatible_concept_packs") or {}
    if not isinstance(compatible_concepts, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in compatible_concepts.items()
    ):
        raise ValueError("compatible_concept_packs must map versions to hashes")
    concept_hashes = {concept_version: concept_hash, **compatible_concepts}
    source = policy.get("source")
    hard = policy.get("hard_exclusions")
    if not isinstance(source, dict) or not isinstance(hard, dict):
        raise ValueError("scoring policy is missing source or hard-exclusion rules")

    title_anchors = list(_string_tuple(source.get("title_strong_anchors")))
    metadata_terms = list(_string_tuple(source.get("metadata_evidence")))
    rawness_terms = _string_tuple(source.get("rawness"))
    packaging_terms = _string_tuple(source.get("packaging_penalties"))
    hard_exclusions = {
        str(category): _string_tuple(terms) for category, terms in hard.items()
    }
    if not hard_exclusions or any(not terms for terms in hard_exclusions.values()):
        raise ValueError("every hard-exclusion category requires terms")

    task_terms: dict[tuple[str, str], list[str]] = {}
    task_scene_zh: dict[tuple[str, str], tuple[str, ...]] = {}
    task_ordinary_zh: dict[tuple[str, str], tuple[str, ...]] = {}
    query_versions: list[str] = []
    for query_pack_path in query_pack_paths:
        query_pack = _read_json(Path(query_pack_path))
        if query_pack.get("status") != "frozen":
            raise ValueError("task vocabularies require frozen query packs")
        query_concept_version = query_pack.get("concept_pack_version")
        if query_concept_version not in concept_hashes:
            raise ValueError("query pack concept version does not match scoring policy")
        if query_pack.get("source_sha256") != concept_hashes[query_concept_version]:
            raise ValueError("query pack concept hash does not match scoring policy")
        campaign_id = _required_text(query_pack, "campaign_id")
        query_versions.append(_required_text(query_pack, "query_pack_version"))
        for item in query_pack.get("queries", []):
            if not isinstance(item, dict) or item.get("campaign_id") != campaign_id:
                raise ValueError("query record campaign identity is invalid")
            subtype = _required_text(item, "subtype")
            source_anchor = _required_text(item, "source_anchor")
            action_term = _required_text(item, "action_or_scene_term")
            source_pool = item.get("source_pool", "surveillance")
            if source_pool == "surveillance":
                _append_unique(title_anchors, source_anchor)
                _append_unique(metadata_terms, source_anchor)
            elif source_pool != "mobile_adjacent":
                raise ValueError("query source_pool is invalid")
            task_terms.setdefault((campaign_id, subtype), [])
            _append_unique(task_terms[(campaign_id, subtype)], action_term)

        semantics = query_pack.get("frozen_semantics_zh")
        subtypes = semantics.get("subtypes") if isinstance(semantics, dict) else None
        if not isinstance(subtypes, list):
            raise ValueError("query pack is missing frozen Chinese subtype semantics")
        for subtype_policy in subtypes:
            if not isinstance(subtype_policy, dict):
                continue
            subtype = _required_text(subtype_policy, "subtype")
            key = (campaign_id, subtype)
            task_terms.setdefault(key, [])
            for term in _string_tuple(subtype_policy.get("core_concepts")):
                _append_unique(task_terms[key], term)
            scene_terms = _string_tuple(subtype_policy.get("scene_concepts"))
            ordinary_terms = _string_tuple(subtype_policy.get("ordinary_behavior_concepts"))
            if scene_terms or ordinary_terms:
                if not scene_terms or not ordinary_terms:
                    raise ValueError("scene-prior semantics require scene and ordinary terms")
                task_scene_zh[key] = scene_terms
                task_ordinary_zh[key] = ordinary_terms

    task_matching = policy.get("task_matching") or {}
    if not isinstance(task_matching, dict):
        raise ValueError("task_matching must be an object")
    tasks = {}
    for key, terms in task_terms.items():
        campaign_rules = task_matching.get(key[0], {})
        if not isinstance(campaign_rules, dict):
            raise ValueError("campaign task matching rules must be an object")
        rule = campaign_rules.get(key[1], {})
        if not isinstance(rule, dict):
            raise ValueError("subtype task matching rule must be an object")
        merged_terms = list(terms)
        for alias in _string_tuple(rule.get("direct_aliases")):
            _append_unique(merged_terms, alias)
        groups = _conjunction_groups(rule.get("conjunction_groups"))
        tasks[key] = TaskVocabulary(
            campaign_id=key[0],
            subtype=key[1],
            direct_terms=tuple(merged_terms),
            scene_terms_zh=task_scene_zh.get(key, ()),
            ordinary_terms_zh=task_ordinary_zh.get(key, ()),
            conjunction_groups=groups,
            forbidden_terms=_string_tuple(rule.get("forbidden_terms")),
            max_direct_participants=(
                int(rule["max_direct_participants"])
                if rule.get("max_direct_participants") is not None
                else None
            ),
        )
    if not tasks:
        raise ValueError("no task vocabularies were loaded")
    return ScoringBundle(
        policy_version=version,
        concept_pack_version=concept_version,
        source_title_anchors=tuple(title_anchors),
        source_metadata_terms=tuple(metadata_terms),
        rawness_terms=rawness_terms,
        packaging_terms=packaging_terms,
        hard_exclusions=MappingProxyType(hard_exclusions),
        tasks=MappingProxyType(tasks),
        query_pack_versions=tuple(query_versions),
    )


def _read_json(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {path}") from error
    if not isinstance(document, dict):
        raise ValueError(f"expected JSON object: {path}")
    return document


def _required_text(document: Mapping, key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing text field: {key}")
    return value


def _string_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError("policy term lists must contain non-empty strings")
    return tuple(dict.fromkeys(value))


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _conjunction_groups(value) -> tuple[TaskConjunctionGroup, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("conjunction_groups must be a list")
    groups = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("conjunction group must be an object")
        groups.append(
            TaskConjunctionGroup(
                left_terms=_string_tuple(item.get("left")),
                right_terms=_string_tuple(item.get("right")),
                marker=_required_text(item, "marker"),
            )
        )
        if not groups[-1].left_terms or not groups[-1].right_terms:
            raise ValueError("conjunction group sides cannot be empty")
    return tuple(groups)


def _verify_policy_hash(document: dict) -> None:
    expected = _required_text(document, "content_sha256")
    content = dict(document)
    for key in (
        "status",
        "frozen_at",
        "frozen_by",
        "content_sha256",
        "content_sha256_scope",
    ):
        content.pop(key, None)
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if actual != expected:
        raise ValueError("frozen scoring policy content hash does not match")
