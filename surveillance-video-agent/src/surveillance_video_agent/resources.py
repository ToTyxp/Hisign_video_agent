"""Static pre-download resource gate derived only from probe metadata."""

from __future__ import annotations

from dataclasses import dataclass

from surveillance_video_agent.contracts import ProbeResult


RESOURCE_POLICY_VERSION = "video-resource-v1.0.0"
MIN_DURATION_SECONDS = 10.0
MAX_DURATION_SECONDS = 15 * 60.0
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
_LIVE_STATUSES = frozenset({"is_live", "is_upcoming", "post_live", "live"})


@dataclass(frozen=True, slots=True)
class ResourceReason:
    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class ResourceEvaluation:
    candidate_key: str
    policy_version: str
    eligible: bool
    reasons: tuple[ResourceReason, ...]


def evaluate_probe_resources(probe: ProbeResult) -> ResourceEvaluation:
    reasons: list[ResourceReason] = []
    duration = probe.duration_seconds
    if duration is None:
        reasons.append(
            ResourceReason(
                "duration_unknown",
                "duration must be known before a candidate can enter vector indexing",
            )
        )
    elif not MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS:
        reasons.append(
            ResourceReason(
                "duration_out_of_range",
                "duration must be between 10 and 900 seconds inclusive",
            )
        )
    if probe.availability not in {None, "public"}:
        reasons.append(
            ResourceReason(
                "availability_not_public",
                "probe explicitly reported a non-public availability",
            )
        )
    if probe.is_live is True or (probe.live_status or "").casefold() in _LIVE_STATUSES:
        reasons.append(
            ResourceReason("live_video", "live or upcoming videos are not eligible")
        )
    size = probe.filesize_approx
    if size is not None and (size <= 0 or size > MAX_FILE_BYTES):
        reasons.append(
            ResourceReason(
                "estimated_file_size_out_of_range",
                "known estimated file size must be positive and no more than 2 GiB",
            )
        )
    return ResourceEvaluation(
        candidate_key=probe.candidate_key,
        policy_version=RESOURCE_POLICY_VERSION,
        eligible=not reasons,
        reasons=tuple(reasons),
    )
