"""Surveillance candidate-pool v2 implementation."""

from surveillance_video_agent.contracts import (
    AdapterError,
    AdapterErrorKind,
    DownloadRequest,
    DownloadResult,
    ProbeRequest,
    ProbeResult,
    SearchHit,
    SearchRequest,
    make_candidate_key,
)

__all__ = [
    "AdapterError",
    "AdapterErrorKind",
    "DownloadRequest",
    "DownloadResult",
    "ProbeRequest",
    "ProbeResult",
    "SearchHit",
    "SearchRequest",
    "make_candidate_key",
]
