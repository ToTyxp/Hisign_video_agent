"""Deterministic metadata-only source gate."""

from __future__ import annotations

from surveillance_video_agent.scoring.matching import find_matches
from surveillance_video_agent.scoring.models import (
    CandidateMetadata,
    HardExclusionEvidence,
    ScoreEvidence,
    SourceScoreResult,
)
from surveillance_video_agent.scoring.policy import ScoringBundle


MOBILE_SOURCE_TERMS = (
    "phone video",
    "cell phone video",
    "cellphone video",
    "mobile video",
    "smartphone video",
    "shot on phone",
    "filmed on phone",
    "vertical video",
    "shorts",
    "short video",
    "reel",
    "grabado con celular",
    "video de teléfono",
    "vídeo móvil",
    "grabado con móvil",
    "video de smartphone",
    "video vertical",
    "video corto",
    "filmé au téléphone",
    "vidéo mobile",
    "vidéo smartphone",
    "filmée au smartphone",
    "vidéo verticale",
    "vidéo courte",
    "手机拍摄",
    "手机录像",
    "竖屏视频",
)


def score_source(
    candidate: CandidateMetadata,
    policy: ScoringBundle,
    *,
    legacy_uploader_prior: int = 0,
) -> SourceScoreResult:
    if isinstance(legacy_uploader_prior, bool) or not 0 <= legacy_uploader_prior <= 2:
        raise ValueError("legacy_uploader_prior must be an integer between 0 and 2")
    fields = candidate.fields()
    hard_exclusions: list[HardExclusionEvidence] = []
    for category, terms in policy.hard_exclusions.items():
        matched_fields, matched_terms = find_matches(fields, terms)
        if matched_terms:
            hard_exclusions.append(
                HardExclusionEvidence(
                    category=category,
                    matched_fields=matched_fields,
                    matched_terms=matched_terms,
                    reason=f"hard exclusion matched: {category}",
                )
            )
    if hard_exclusions:
        return SourceScoreResult(
            candidate_key=candidate.candidate_key,
            policy_version=policy.policy_version,
            score=0,
            qualified=False,
            hard_excluded=True,
            camera_pool=None,
            evidence=(),
            hard_exclusions=tuple(hard_exclusions),
        )

    evidence: list[ScoreEvidence] = []
    title_fields, title_terms = find_matches(
        {"title": candidate.title}, policy.source_title_anchors
    )
    if title_terms:
        evidence.append(
            ScoreEvidence(
                "source.title_strong_anchor",
                4,
                title_fields,
                title_terms,
                "title contains a strong surveillance-source anchor",
            )
        )
    metadata_fields = {
        key: fields[key]
        for key in ("video_description", "tags", "uploader", "channel", "playlist")
    }
    metadata_matches, metadata_terms = find_matches(
        metadata_fields, policy.source_metadata_terms
    )
    if metadata_terms:
        evidence.append(
            ScoreEvidence(
                "source.metadata_evidence",
                2,
                metadata_matches,
                metadata_terms,
                "metadata contains surveillance-source evidence",
            )
        )
    raw_fields, raw_terms = find_matches(fields, policy.rawness_terms)
    if raw_terms:
        evidence.append(
            ScoreEvidence(
                "source.rawness",
                1,
                raw_fields,
                raw_terms,
                "metadata contains rawness or continuity evidence",
            )
        )
    if legacy_uploader_prior:
        evidence.append(
            ScoreEvidence(
                "source.legacy_uploader_prior",
                legacy_uploader_prior,
                ("uploader",),
                (),
                "versioned legacy completed-uploader prior",
            )
        )
    packaging_fields, packaging_terms = find_matches(
        {
            key: fields[key]
            for key in (
                "title",
                "video_description",
                "tags",
                "uploader",
                "channel",
                "playlist",
            )
        },
        policy.packaging_terms,
    )
    if packaging_terms:
        evidence.append(
            ScoreEvidence(
                "source.packaging_penalty",
                -3,
                packaging_fields,
                packaging_terms,
                "news, commentary, reaction, or compilation packaging",
            )
        )
    score = sum(item.points for item in evidence)
    qualified = score >= 4
    return SourceScoreResult(
        candidate_key=candidate.candidate_key,
        policy_version=policy.policy_version,
        score=score,
        qualified=qualified,
        hard_excluded=False,
        camera_pool="surveillance" if qualified else None,
        evidence=tuple(evidence),
        hard_exclusions=(),
    )


def score_sign_mobile_source(
    candidate: CandidateMetadata,
    policy: ScoringBundle,
    *,
    width: int | None,
    height: int | None,
    duration_seconds: float | None = None,
    discovered_by_mobile_query: bool = False,
    legacy_uploader_prior: int = 0,
) -> SourceScoreResult:
    """Campaign-scoped source gate that permits real-world mobile capture."""

    if isinstance(legacy_uploader_prior, bool) or not 0 <= legacy_uploader_prior <= 2:
        raise ValueError("legacy_uploader_prior must be an integer between 0 and 2")
    fields = candidate.fields()
    hard_exclusions: list[HardExclusionEvidence] = []
    for category, terms in policy.hard_exclusions.items():
        if category == "mobile_capture":
            continue
        matched_fields, matched_terms = find_matches(fields, terms)
        if matched_terms:
            hard_exclusions.append(
                HardExclusionEvidence(
                    category=category,
                    matched_fields=matched_fields,
                    matched_terms=matched_terms,
                    reason=f"hard exclusion matched: {category}",
                )
            )
    if hard_exclusions:
        return SourceScoreResult(
            candidate.candidate_key,
            policy.policy_version,
            0,
            False,
            True,
            None,
            (),
            tuple(hard_exclusions),
        )

    evidence: list[ScoreEvidence] = []
    surveillance_fields, surveillance_terms = find_matches(
        {"title": candidate.title}, policy.source_title_anchors
    )
    mobile_title_fields, mobile_title_terms = find_matches(
        {"title": candidate.title}, MOBILE_SOURCE_TERMS
    )
    is_vertical = (
        isinstance(width, int)
        and isinstance(height, int)
        and width > 0
        and height > width
    )
    if surveillance_terms:
        evidence.append(
            ScoreEvidence(
                "source.title_strong_anchor",
                4,
                surveillance_fields,
                surveillance_terms,
                "title contains a strong surveillance-source anchor",
            )
        )
        camera_pool = "surveillance"
    elif mobile_title_terms:
        evidence.append(
            ScoreEvidence(
                "source.mobile_title_anchor",
                4,
                mobile_title_fields,
                mobile_title_terms,
                "title explicitly identifies mobile capture",
            )
        )
        camera_pool = "mobile_adjacent"
    elif is_vertical:
        evidence.append(
            ScoreEvidence(
                "source.mobile_vertical_geometry",
                4,
                ("resolution",),
                (f"{width}x{height}",),
                "portrait video geometry is a mobile-adjacent source anchor",
            )
        )
        camera_pool = "mobile_adjacent"
    elif (
        discovered_by_mobile_query
        and isinstance(duration_seconds, (int, float))
        and 10 <= float(duration_seconds) <= 90
    ):
        evidence.append(
            ScoreEvidence(
                "source.mobile_query_short_duration",
                4,
                ("query_provenance", "duration_seconds"),
                ("mobile_adjacent_query", f"{float(duration_seconds):g}s"),
                "mobile-anchored discovery plus short duration",
            )
        )
        camera_pool = "mobile_adjacent"
    else:
        camera_pool = None

    metadata_fields = {
        key: fields[key]
        for key in ("video_description", "tags", "uploader", "channel", "playlist")
    }
    metadata_matches, metadata_terms = find_matches(
        metadata_fields, policy.source_metadata_terms
    )
    mobile_metadata_fields, mobile_metadata_terms = find_matches(
        metadata_fields, MOBILE_SOURCE_TERMS
    )
    combined_fields = tuple(dict.fromkeys((*metadata_matches, *mobile_metadata_fields)))
    combined_terms = tuple(dict.fromkeys((*metadata_terms, *mobile_metadata_terms)))
    if combined_terms:
        evidence.append(
            ScoreEvidence(
                "source.metadata_evidence",
                2,
                combined_fields,
                combined_terms,
                "metadata contains surveillance or mobile-source evidence",
            )
        )
        if camera_pool is None and mobile_metadata_terms:
            camera_pool = "mobile_adjacent"
    raw_fields, raw_terms = find_matches(fields, policy.rawness_terms)
    if raw_terms:
        evidence.append(
            ScoreEvidence(
                "source.rawness",
                1,
                raw_fields,
                raw_terms,
                "metadata contains rawness or continuity evidence",
            )
        )
    if legacy_uploader_prior:
        evidence.append(
            ScoreEvidence(
                "source.legacy_uploader_prior",
                legacy_uploader_prior,
                ("uploader",),
                (),
                "versioned legacy completed-uploader prior",
            )
        )
    packaging_fields, packaging_terms = find_matches(
        {
            key: fields[key]
            for key in (
                "title",
                "video_description",
                "tags",
                "uploader",
                "channel",
                "playlist",
            )
        },
        policy.packaging_terms,
    )
    if packaging_terms:
        evidence.append(
            ScoreEvidence(
                "source.packaging_penalty",
                -3,
                packaging_fields,
                packaging_terms,
                "news, commentary, reaction, or compilation packaging",
            )
        )
    score = sum(item.points for item in evidence)
    qualified = score >= 4 and camera_pool is not None
    return SourceScoreResult(
        candidate.candidate_key,
        policy.policy_version,
        score,
        qualified,
        False,
        camera_pool if qualified else None,
        tuple(evidence),
        (),
    )
