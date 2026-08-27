"""Independent campaign/subtype task scoring after the source gate."""

from __future__ import annotations

import re

from surveillance_video_agent.scoring.matching import (
    field_has_conjunction,
    find_matches,
)
from surveillance_video_agent.scoring.models import (
    CandidateMetadata,
    ScoreEvidence,
    SourceScoreResult,
    TaskScoreResult,
)
from surveillance_video_agent.scoring.policy import ScoringBundle, TaskVocabulary


def score_task(
    candidate: CandidateMetadata,
    source_result: SourceScoreResult,
    policy: ScoringBundle,
    *,
    campaign_id: str,
    subtype: str,
) -> TaskScoreResult:
    key = (campaign_id, subtype)
    vocabulary = policy.tasks.get(key)
    if vocabulary is None:
        raise ValueError(f"unknown campaign/subtype: {campaign_id}/{subtype}")
    if source_result.candidate_key != candidate.candidate_key:
        raise ValueError("source result belongs to a different candidate")
    if source_result.policy_version != policy.policy_version:
        raise ValueError("source result was produced by a different scoring policy")
    if not source_result.qualified:
        return TaskScoreResult(
            candidate.candidate_key,
            policy.policy_version,
            campaign_id,
            subtype,
            0,
            False,
            True,
            (),
        )

    all_fields = candidate.fields()
    scale_fields, scale_terms = _numeric_scale_matches(
        all_fields,
        vocabulary.max_direct_participants,
    )
    if scale_terms:
        return TaskScoreResult(
            candidate.candidate_key,
            policy.policy_version,
            campaign_id,
            subtype,
            0,
            False,
            False,
            (
                ScoreEvidence(
                    "task.forbidden_semantics",
                    0,
                    scale_fields,
                    scale_terms,
                    "metadata participant count exceeds the frozen small-scale maximum",
                ),
            ),
        )
    blocked_fields, blocked_terms = find_matches(
        all_fields, vocabulary.forbidden_terms
    )
    if blocked_terms:
        return TaskScoreResult(
            candidate.candidate_key,
            policy.policy_version,
            campaign_id,
            subtype,
            0,
            False,
            False,
            (
                ScoreEvidence(
                    "task.forbidden_semantics",
                    0,
                    blocked_fields,
                    blocked_terms,
                    "metadata contains an explicit semantic counterexample",
                ),
            ),
        )

    evidence: list[ScoreEvidence] = []
    title_terms = _matches_vocabulary({"title": candidate.title}, vocabulary)
    if title_terms[1]:
        evidence.append(
            ScoreEvidence(
                "task.title_action",
                4,
                title_terms[0],
                title_terms[1],
                "title matches the subtype action or scene definition",
            )
        )
    metadata = {
        "video_description": candidate.video_description,
        "tags": " ".join(candidate.tags),
    }
    metadata_terms = _matches_vocabulary(metadata, vocabulary)
    if metadata_terms[1]:
        evidence.append(
            ScoreEvidence(
                "task.metadata_action",
                2,
                metadata_terms[0],
                metadata_terms[1],
                "description or tags match the subtype action or scene definition",
            )
        )
    score = sum(item.points for item in evidence)
    return TaskScoreResult(
        candidate.candidate_key,
        policy.policy_version,
        campaign_id,
        subtype,
        score,
        score >= 4,
        False,
        tuple(evidence),
    )


def score_all_tasks(
    candidate: CandidateMetadata,
    source_result: SourceScoreResult,
    policy: ScoringBundle,
) -> tuple[TaskScoreResult, ...]:
    return tuple(
        score_task(
            candidate,
            source_result,
            policy,
            campaign_id=campaign_id,
            subtype=subtype,
        )
        for campaign_id, subtype in sorted(policy.tasks)
    )


def _matches_vocabulary(
    fields: dict[str, str], vocabulary: TaskVocabulary
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    matched_fields, matched_terms = find_matches(fields, vocabulary.direct_terms)
    field_names = list(matched_fields)
    terms = list(matched_terms)
    if vocabulary.scene_terms_zh:
        for field_name, text in fields.items():
            if field_has_conjunction(
                text,
                vocabulary.scene_terms_zh,
                vocabulary.ordinary_terms_zh,
            ):
                if field_name not in field_names:
                    field_names.append(field_name)
                marker = "中文场景词+普通非攻击行为词"
                if marker not in terms:
                    terms.append(marker)
    for group in vocabulary.conjunction_groups:
        for field_name, text in fields.items():
            if field_has_conjunction(
                text,
                group.left_terms,
                group.right_terms,
            ):
                if field_name not in field_names:
                    field_names.append(field_name)
                if group.marker not in terms:
                    terms.append(group.marker)
    return tuple(field_names), tuple(terms)


_PARTICIPANT_COUNT_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[.,\s]\d{3})*|\d+)\s*"
    r"(people|persons|protesters|demonstrators|personas|manifestantes|"
    r"personnes|manifestants|participants|人|名抗议者|名示威者)",
    re.IGNORECASE,
)


def _numeric_scale_matches(fields, maximum: int | None):
    if maximum is None:
        return (), ()
    matched_fields = []
    matched_terms = []
    for field_name, text in fields.items():
        for match in _PARTICIPANT_COUNT_RE.finditer(text):
            value = int(re.sub(r"[.,\s]", "", match.group(1)))
            if value <= maximum:
                continue
            if field_name not in matched_fields:
                matched_fields.append(field_name)
            term = match.group(0)
            if term not in matched_terms:
                matched_terms.append(term)
    return tuple(matched_fields), tuple(matched_terms)
