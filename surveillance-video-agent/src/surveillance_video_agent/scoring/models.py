"""Immutable scoring inputs and audit results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from surveillance_video_agent.contracts import ProbeResult


@dataclass(frozen=True, slots=True)
class CandidateMetadata:
    candidate_key: str
    title: str = ""
    video_description: str = ""
    tags: tuple[str, ...] = ()
    uploader: str = ""
    channel: str = ""
    playlist: str = ""

    @classmethod
    def from_probe(cls, probe: ProbeResult) -> "CandidateMetadata":
        return cls(
            candidate_key=probe.candidate_key,
            title=probe.title or "",
            video_description=probe.video_description or "",
            tags=probe.tags,
            uploader=probe.uploader or "",
            channel=probe.channel or "",
            playlist=probe.playlist or "",
        )

    def fields(self) -> Mapping[str, str]:
        return {
            "title": self.title,
            "video_description": self.video_description,
            "tags": " ".join(self.tags),
            "uploader": self.uploader,
            "channel": self.channel,
            "playlist": self.playlist,
        }


@dataclass(frozen=True, slots=True)
class ScoreEvidence:
    rule_code: str
    points: int
    matched_fields: tuple[str, ...]
    matched_terms: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class HardExclusionEvidence:
    category: str
    matched_fields: tuple[str, ...]
    matched_terms: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class SourceScoreResult:
    candidate_key: str
    policy_version: str
    score: int
    qualified: bool
    hard_excluded: bool
    camera_pool: str | None
    evidence: tuple[ScoreEvidence, ...]
    hard_exclusions: tuple[HardExclusionEvidence, ...]


@dataclass(frozen=True, slots=True)
class TaskScoreResult:
    candidate_key: str
    policy_version: str
    campaign_id: str
    subtype: str
    score: int
    qualified: bool
    blocked_by_source_gate: bool
    evidence: tuple[ScoreEvidence, ...]
