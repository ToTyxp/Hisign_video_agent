"""Metadata-only source and task scoring."""

from surveillance_video_agent.scoring.models import (
    CandidateMetadata,
    HardExclusionEvidence,
    ScoreEvidence,
    SourceScoreResult,
    TaskScoreResult,
)
from surveillance_video_agent.scoring.policy import (
    ScoringBundle,
    TaskConjunctionGroup,
    TaskVocabulary,
    load_scoring_bundle,
)
from surveillance_video_agent.scoring.source import score_sign_mobile_source, score_source
from surveillance_video_agent.scoring.task import score_all_tasks, score_task

__all__ = [
    "CandidateMetadata",
    "HardExclusionEvidence",
    "ScoreEvidence",
    "ScoringBundle",
    "SourceScoreResult",
    "TaskScoreResult",
    "TaskConjunctionGroup",
    "TaskVocabulary",
    "load_scoring_bundle",
    "score_all_tasks",
    "score_source",
    "score_sign_mobile_source",
    "score_task",
]
