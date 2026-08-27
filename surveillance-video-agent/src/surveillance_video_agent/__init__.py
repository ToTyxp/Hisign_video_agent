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
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.discovery import (
    DiscoveryConfig,
    DiscoveryService,
    DiscoverySummary,
    QualificationSummary,
    SearchDiscoverySummary,
)
from surveillance_video_agent.qwen_embedding import (
    QWEN_SCHEMA,
    DashScopeQwenEmbeddingProvider,
    EmbeddingErrorKind,
    EmbeddingProviderError,
)
from surveillance_video_agent.semantic_queries import (
    SemanticQueryPreparationResult,
    SemanticQuerySpec,
    SemanticQueryVectorService,
    build_semantic_query_specs,
)
from surveillance_video_agent.resources import (
    RESOURCE_POLICY_VERSION,
    ResourceEvaluation,
    evaluate_probe_resources,
)
from surveillance_video_agent.policies import (
    FrontierPolicyRecord,
    bootstrap_default_campaign_policies,
    get_campaign_policy,
    get_frontier_policy,
    update_campaign_policy,
    update_frontier_policy,
)

__all__ = [
    "AdapterError",
    "AdapterErrorKind",
    "CandidateDatabase",
    "DownloadRequest",
    "DownloadResult",
    "DiscoveryConfig",
    "DiscoveryService",
    "DiscoverySummary",
    "DashScopeQwenEmbeddingProvider",
    "ProbeRequest",
    "ProbeResult",
    "QualificationSummary",
    "QWEN_SCHEMA",
    "RESOURCE_POLICY_VERSION",
    "ResourceEvaluation",
    "SearchHit",
    "SearchRequest",
    "SearchDiscoverySummary",
    "SemanticQueryPreparationResult",
    "SemanticQuerySpec",
    "SemanticQueryVectorService",
    "FrontierPolicyRecord",
    "EmbeddingErrorKind",
    "EmbeddingProviderError",
    "make_candidate_key",
    "build_semantic_query_specs",
    "bootstrap_default_campaign_policies",
    "evaluate_probe_resources",
    "get_campaign_policy",
    "get_frontier_policy",
    "update_campaign_policy",
    "update_frontier_policy",
]
