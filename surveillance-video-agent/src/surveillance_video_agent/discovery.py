"""Bounded three-platform discovery with cache and auditable source-first scoring."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from surveillance_video_agent.adapters.base import (
    BasePlatformAdapter,
    sanitize_error_text,
    sanitize_metadata,
)
from surveillance_video_agent.contracts import (
    DEFAULT_NETWORK_CONFIG,
    MAX_SEARCH_RESULTS,
    SUPPORTED_PLATFORMS,
    AdapterError,
    AdapterErrorKind,
    ProbeRequest,
    ProbeResult,
    SearchHit,
    SearchRequest,
)
from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.scoring import (
    CandidateMetadata,
    ScoringBundle,
    score_all_tasks,
    score_sign_mobile_source,
    score_source,
)
from surveillance_video_agent.resources import evaluate_probe_resources


MAX_PROBES_PER_CAMPAIGN = 150


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    campaign_id: str
    query_pack_version: str
    network_config: str = DEFAULT_NETWORK_CONFIG
    query_ids: tuple[str, ...] = ()
    per_query_limit: int = MAX_SEARCH_RESULTS
    probe_limit: int = MAX_PROBES_PER_CAMPAIGN
    max_requests_per_platform: int = 2
    search_cache_ttl_seconds: int = 30 * 60
    probe_cache_ttl_seconds: int = 7 * 24 * 60 * 60
    probe_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not self.campaign_id or not self.query_pack_version:
            raise ValueError("campaign_id and query_pack_version are required")
        if self.network_config != DEFAULT_NETWORK_CONFIG:
            raise ValueError("v1 network_config must be 'default'")
        if not 1 <= self.per_query_limit <= MAX_SEARCH_RESULTS:
            raise ValueError(f"per_query_limit must be between 1 and {MAX_SEARCH_RESULTS}")
        if not 1 <= self.probe_limit <= MAX_PROBES_PER_CAMPAIGN:
            raise ValueError(
                f"probe_limit must be between 1 and {MAX_PROBES_PER_CAMPAIGN}"
            )
        if not 1 <= self.max_requests_per_platform <= 2:
            raise ValueError("max_requests_per_platform must be 1 or 2")
        if self.search_cache_ttl_seconds <= 0 or self.probe_cache_ttl_seconds <= 0:
            raise ValueError("cache TTLs must be positive")
        if self.probe_timeout_seconds <= 0:
            raise ValueError("probe_timeout_seconds must be positive")
        if len(set(self.query_ids)) != len(self.query_ids):
            raise ValueError("query_ids must not contain duplicates")


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    campaign_id: str
    query_count: int
    search_request_count: int
    search_cache_hit_count: int
    search_failure_count: int
    discovered_hit_count: int
    unique_candidate_count: int
    cheap_hard_excluded_count: int
    probe_selected_count: int
    probe_network_call_count: int
    probe_cache_hit_count: int
    probe_failure_count: int
    source_qualified_count: int
    task_qualified_score_count: int
    resource_eligible_count: int
    resource_ineligible_count: int
    probe_budget_exhausted: bool


@dataclass(frozen=True, slots=True)
class SearchDiscoverySummary:
    campaign_id: str
    query_count: int
    search_request_count: int
    search_cache_hit_count: int
    search_failure_count: int
    discovered_hit_count: int
    unique_candidate_count: int
    cheap_hard_excluded_count: int


@dataclass(frozen=True, slots=True)
class QualificationSummary:
    campaign_id: str
    cumulative_probe_selection_count: int
    new_probe_selection_count: int
    probe_attempted_count: int
    probe_network_call_count: int
    probe_cache_hit_count: int
    probe_failure_count: int
    source_qualified_count: int
    task_qualified_score_count: int
    resource_eligible_count: int
    resource_ineligible_count: int
    probe_budget_exhausted: bool


@dataclass(frozen=True, slots=True)
class _Query:
    query_id: str
    campaign_id: str
    lang: str
    query_text: str


@dataclass(frozen=True, slots=True)
class _SearchOutcome:
    request: SearchRequest
    query_id: str
    started_at: str
    finished_at: str
    hits: tuple[SearchHit, ...] = ()
    error_kind: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class _ProbeOutcome:
    request: ProbeRequest
    started_at: str
    finished_at: str
    probe: ProbeResult | None = None
    error_kind: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class _ProbeCandidate:
    platform: str
    source_id: str
    candidate_key: str
    source_url: str
    position: int
    cheap_source_score: int


class DiscoveryService:
    """Discover broadly, but probe only a ranked and explicitly bounded work set.

    Adapter work happens in worker threads. All SQLite reads and writes remain in
    the caller thread so a single connection stays deterministic and auditable.
    """

    def __init__(
        self,
        database: CandidateDatabase,
        adapters: Mapping[str, BasePlatformAdapter],
        scoring: ScoringBundle,
    ) -> None:
        if set(adapters) != SUPPORTED_PLATFORMS:
            raise ValueError("discovery requires youtube, dailymotion, and peertube")
        for platform, adapter in adapters.items():
            if adapter.platform != platform:
                raise ValueError(f"adapter mapping mismatch for {platform}")
        self.database = database
        self.adapters = dict(adapters)
        self.scoring = scoring

    def discover_and_qualify(
        self,
        *,
        run_id: str,
        config: DiscoveryConfig,
    ) -> DiscoverySummary:
        """Convenience composition; ``discover`` and ``qualify`` stay restartable."""

        discovery = self.discover(run_id=run_id, config=config)
        qualification = self.qualify(run_id=run_id, config=config)
        return DiscoverySummary(
            campaign_id=config.campaign_id,
            query_count=discovery.query_count,
            search_request_count=discovery.search_request_count,
            search_cache_hit_count=discovery.search_cache_hit_count,
            search_failure_count=discovery.search_failure_count,
            discovered_hit_count=discovery.discovered_hit_count,
            unique_candidate_count=discovery.unique_candidate_count,
            cheap_hard_excluded_count=discovery.cheap_hard_excluded_count,
            probe_selected_count=qualification.probe_attempted_count,
            probe_network_call_count=qualification.probe_network_call_count,
            probe_cache_hit_count=qualification.probe_cache_hit_count,
            probe_failure_count=qualification.probe_failure_count,
            source_qualified_count=qualification.source_qualified_count,
            task_qualified_score_count=qualification.task_qualified_score_count,
            resource_eligible_count=qualification.resource_eligible_count,
            resource_ineligible_count=qualification.resource_ineligible_count,
            probe_budget_exhausted=qualification.probe_budget_exhausted,
        )

    def discover(
        self,
        *,
        run_id: str,
        config: DiscoveryConfig,
    ) -> SearchDiscoverySummary:
        """Search and persist discovery facts without issuing any probe."""

        self._validate_run(run_id)
        queries = self._load_queries(config)
        semaphores = {
            platform: threading.BoundedSemaphore(config.max_requests_per_platform)
            for platform in self.adapters
        }
        search_outcomes = self._search_all(
            run_id=run_id,
            config=config,
            queries=queries,
            semaphores=semaphores,
        )

        all_candidate_keys: set[str] = set()
        discovered_hit_count = 0
        cheap_hard_excluded: set[str] = set()
        search_cache_hits = 0
        search_failures = 0
        for outcome, cache_hit in search_outcomes:
            self._audit_search(outcome, run_id=run_id, cache_hit=cache_hit)
            if cache_hit:
                search_cache_hits += 1
            if outcome.error_kind is not None:
                search_failures += 1
                continue
            if not cache_hit:
                fetched_at = outcome.finished_at
                self.database.upsert_search_cache(
                    platform=outcome.request.platform,
                    query=outcome.request.query,
                    lang=outcome.request.lang,
                    query_pack_version=outcome.request.query_pack_version,
                    network_config=outcome.request.network_config,
                    payload={"entries": [_search_hit_to_json(hit) for hit in outcome.hits]},
                    fetched_at=fetched_at,
                    expires_at=_after(
                        fetched_at, config.search_cache_ttl_seconds
                    ),
                )
            for hit in outcome.hits:
                discovered_hit_count += 1
                all_candidate_keys.add(hit.candidate_key)
                self.database.insert_search_hit(hit, run_id=run_id)
                self.database.record_discovery(
                    hit,
                    query_id=outcome.query_id,
                    run_id=run_id,
                )
                row = self.database.get_candidate(hit.candidate_key)
                if row is None:
                    raise RuntimeError("candidate disappeared after discovery insert")
                if row["status"] != "discovered" or row["hard_excluded"]:
                    if row["hard_excluded"]:
                        cheap_hard_excluded.add(hit.candidate_key)
                    continue
                cheap_metadata = CandidateMetadata(
                        candidate_key=hit.candidate_key,
                        title=hit.title or "",
                        uploader=hit.uploader or "",
                    )
                cheap_source = (
                    score_sign_mobile_source(
                        cheap_metadata,
                        self.scoring,
                        width=None,
                        height=None,
                        duration_seconds=hit.duration_seconds,
                        discovered_by_mobile_query=True,
                    )
                    if config.campaign_id == "sign_action_v1"
                    else score_source(cheap_metadata, self.scoring)
                )
                if cheap_source.hard_excluded:
                    self.database.record_source_score(cheap_source, run_id=run_id)
                    cheap_hard_excluded.add(hit.candidate_key)
                    continue
        return SearchDiscoverySummary(
            campaign_id=config.campaign_id,
            query_count=len(queries),
            search_request_count=len(search_outcomes),
            search_cache_hit_count=search_cache_hits,
            search_failure_count=search_failures,
            discovered_hit_count=discovered_hit_count,
            unique_candidate_count=len(all_candidate_keys),
            cheap_hard_excluded_count=len(cheap_hard_excluded),
        )

    def qualify(
        self,
        *,
        run_id: str,
        config: DiscoveryConfig,
    ) -> QualificationSummary:
        """Consume the persistent probe selection frontier within its total budget."""

        self._validate_run(run_id)
        self._load_queries(config)
        ranked = self._rank_probe_candidates(config)
        selected, cumulative_count, new_count, budget_exhausted = (
            self._reserve_probe_candidates(
                run_id=run_id,
                config=config,
                ranked=ranked,
            )
        )
        semaphores = {
            platform: threading.BoundedSemaphore(config.max_requests_per_platform)
            for platform in self.adapters
        }
        probe_outcomes = self._probe_all(
            run_id=run_id,
            config=config,
            candidates=selected,
            semaphores=semaphores,
        )

        probe_cache_hits = 0
        probe_failures = 0
        source_qualified = 0
        task_qualified = 0
        resource_eligible = 0
        resource_ineligible = 0
        for outcome, cache_hit in probe_outcomes:
            self._audit_probe(outcome, run_id=run_id, cache_hit=cache_hit)
            if cache_hit:
                probe_cache_hits += 1
            if outcome.error_kind is not None or outcome.probe is None:
                probe_failures += 1
                self._complete_probe_selection(
                    config=config,
                    candidate_key=outcome.request.candidate_key,
                    run_id=run_id,
                    status="failed",
                    completed_at=outcome.finished_at,
                )
                continue
            if not cache_hit:
                fetched_at = outcome.finished_at
                safe_probe = _sanitized_probe(outcome.probe)
                self.database.upsert_probe_cache(
                    safe_probe,
                    network_config=config.network_config,
                    fetched_at=fetched_at,
                    expires_at=_after(fetched_at, config.probe_cache_ttl_seconds),
                )
            else:
                safe_probe = outcome.probe
            self.database.insert_candidate(safe_probe, run_id=run_id)
            resource_result = evaluate_probe_resources(safe_probe)
            self.database.record_resource_evaluation(resource_result, run_id=run_id)
            if resource_result.eligible:
                resource_eligible += 1
            else:
                resource_ineligible += 1
            metadata = CandidateMetadata.from_probe(safe_probe)
            prior = self._legacy_uploader_prior(safe_probe)
            source_result = (
                score_sign_mobile_source(
                    metadata,
                    self.scoring,
                    width=safe_probe.width,
                    height=safe_probe.height,
                    duration_seconds=safe_probe.duration_seconds,
                    discovered_by_mobile_query=True,
                    legacy_uploader_prior=prior,
                )
                if config.campaign_id == "sign_action_v1"
                else score_source(
                    metadata,
                    self.scoring,
                    legacy_uploader_prior=prior,
                )
            )
            task_results = (
                score_all_tasks(metadata, source_result, self.scoring)
                if source_result.qualified
                else ()
            )
            self.database.record_qualification(
                source_result,
                task_results,
                run_id=run_id,
            )
            if source_result.qualified:
                source_qualified += 1
                task_qualified += sum(result.qualified for result in task_results)
            self._complete_probe_selection(
                config=config,
                candidate_key=outcome.request.candidate_key,
                run_id=run_id,
                status="probed",
                completed_at=outcome.finished_at,
            )

        return QualificationSummary(
            campaign_id=config.campaign_id,
            cumulative_probe_selection_count=cumulative_count,
            new_probe_selection_count=new_count,
            probe_attempted_count=len(selected),
            probe_network_call_count=len(probe_outcomes) - probe_cache_hits,
            probe_cache_hit_count=probe_cache_hits,
            probe_failure_count=probe_failures,
            source_qualified_count=source_qualified,
            task_qualified_score_count=task_qualified,
            resource_eligible_count=resource_eligible,
            resource_ineligible_count=resource_ineligible,
            probe_budget_exhausted=budget_exhausted,
        )

    def _load_queries(self, config: DiscoveryConfig) -> tuple[_Query, ...]:
        pack = self.database.connection.execute(
            """
            SELECT campaign_id, status FROM query_packs
            WHERE query_pack_version = ?
            """,
            (config.query_pack_version,),
        ).fetchone()
        if pack is None or pack["status"] != "frozen":
            raise ValueError("registered frozen query pack is required")
        if pack["campaign_id"] != config.campaign_id:
            raise ValueError("campaign does not match query pack")
        if config.query_pack_version not in self.scoring.query_pack_versions:
            raise ValueError("scoring bundle does not contain the selected query pack")
        parameters: list[Any] = [config.query_pack_version, config.campaign_id]
        sql = """
            SELECT query_id, campaign_id, lang, query_text
            FROM queries
            WHERE query_pack_version = ? AND campaign_id = ?
        """
        if config.query_ids:
            placeholders = ",".join("?" for _ in config.query_ids)
            sql += f" AND query_id IN ({placeholders})"
            parameters.extend(config.query_ids)
        sql += " ORDER BY query_id"
        rows = self.database.connection.execute(sql, parameters).fetchall()
        if config.query_ids and {row["query_id"] for row in rows} != set(
            config.query_ids
        ):
            raise ValueError("one or more query_ids are outside the selected query pack")
        if not rows:
            raise ValueError("selected query set is empty")
        return tuple(
            _Query(row["query_id"], row["campaign_id"], row["lang"], row["query_text"])
            for row in rows
        )

    def _validate_run(self, run_id: str) -> None:
        row = self.database.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or row["status"] != "running":
            raise ValueError("an existing running run_id is required")

    def _search_all(
        self,
        *,
        run_id: str,
        config: DiscoveryConfig,
        queries: Sequence[_Query],
        semaphores: Mapping[str, threading.BoundedSemaphore],
    ) -> list[tuple[_SearchOutcome, bool]]:
        results: list[tuple[_SearchOutcome, bool]] = []
        pending: dict[Future[_SearchOutcome], None] = {}
        workers = len(self.adapters) * config.max_requests_per_platform
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for query in queries:
                for platform in sorted(self.adapters):
                    request = SearchRequest(
                        platform=platform,
                        query=query.query_text,
                        lang=query.lang,
                        query_pack_version=config.query_pack_version,
                        network_config=config.network_config,
                        limit=config.per_query_limit,
                        request_id=str(uuid.uuid4()),
                        run_id=run_id,
                    )
                    started_at = utc_now()
                    cached = self.database.get_search_cache(
                        platform=platform,
                        query=query.query_text,
                        lang=query.lang,
                        query_pack_version=config.query_pack_version,
                        network_config=config.network_config,
                        now=started_at,
                    )
                    if cached is not None:
                        try:
                            hits = _search_hits_from_cache(cached, request)
                        except (KeyError, TypeError, ValueError):
                            cached = None
                        else:
                            results.append(
                                (
                                    _SearchOutcome(
                                        request,
                                        query.query_id,
                                        started_at,
                                        utc_now(),
                                        hits=hits,
                                    ),
                                    True,
                                )
                            )
                    if cached is None:
                        future = executor.submit(
                            self._search_one,
                            request,
                            query.query_id,
                            semaphores[platform],
                        )
                        pending[future] = None
            for future in as_completed(pending):
                results.append((future.result(), False))
        return results

    def _search_one(
        self,
        request: SearchRequest,
        query_id: str,
        semaphore: threading.BoundedSemaphore,
    ) -> _SearchOutcome:
        started_at = utc_now()
        try:
            with semaphore:
                hits = tuple(self.adapters[request.platform].search(request))
            _validate_search_hits(hits, request)
            return _SearchOutcome(
                request,
                query_id,
                started_at,
                utc_now(),
                hits=hits,
            )
        except Exception as error:
            kind, message = _error_details(error)
            return _SearchOutcome(
                request,
                query_id,
                started_at,
                utc_now(),
                error_kind=kind,
                error_message=message,
            )

    def _probe_all(
        self,
        *,
        run_id: str,
        config: DiscoveryConfig,
        candidates: Sequence[_ProbeCandidate],
        semaphores: Mapping[str, threading.BoundedSemaphore],
    ) -> list[tuple[_ProbeOutcome, bool]]:
        results: list[tuple[_ProbeOutcome, bool]] = []
        pending: dict[Future[_ProbeOutcome], None] = {}
        workers = len(self.adapters) * config.max_requests_per_platform
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for candidate in candidates:
                request = ProbeRequest(
                    platform=candidate.platform,
                    source_id=candidate.source_id,
                    candidate_key=candidate.candidate_key,
                    source_url=candidate.source_url,
                    network_config=config.network_config,
                    request_id=str(uuid.uuid4()),
                    run_id=run_id,
                    timeout_seconds=config.probe_timeout_seconds,
                )
                started_at = utc_now()
                cached = self.database.get_probe_cache(
                    platform=candidate.platform,
                    source_id=candidate.source_id,
                    network_config=config.network_config,
                    now=started_at,
                )
                if cached is not None:
                    results.append(
                        (
                            _ProbeOutcome(
                                request,
                                started_at,
                                utc_now(),
                                probe=cached,
                            ),
                            True,
                        )
                    )
                else:
                    future = executor.submit(
                        self._probe_one,
                        request,
                        semaphores[candidate.platform],
                    )
                    pending[future] = None
            for future in as_completed(pending):
                results.append((future.result(), False))
        return results

    def _probe_one(
        self,
        request: ProbeRequest,
        semaphore: threading.BoundedSemaphore,
    ) -> _ProbeOutcome:
        started_at = utc_now()
        try:
            with semaphore:
                probe = self.adapters[request.platform].probe(request)
            if probe.candidate_key != request.candidate_key or probe.platform != request.platform:
                raise ValueError("probe result identity does not match request")
            return _ProbeOutcome(request, started_at, utc_now(), probe=probe)
        except Exception as error:
            kind, message = _error_details(error)
            return _ProbeOutcome(
                request,
                started_at,
                utc_now(),
                error_kind=kind,
                error_message=message,
            )

    def _audit_search(
        self,
        outcome: _SearchOutcome,
        *,
        run_id: str,
        cache_hit: bool,
    ) -> None:
        self.database.record_adapter_call(
            request_id=outcome.request.request_id,
            run_id=run_id,
            platform=outcome.request.platform,
            operation="search",
            query_id=outcome.query_id,
            cache_hit=cache_hit,
            status="failed" if outcome.error_kind else "succeeded",
            error_kind=outcome.error_kind,
            error_message=outcome.error_message,
            started_at=outcome.started_at,
            finished_at=outcome.finished_at,
        )

    def _audit_probe(
        self,
        outcome: _ProbeOutcome,
        *,
        run_id: str,
        cache_hit: bool,
    ) -> None:
        self.database.record_adapter_call(
            request_id=outcome.request.request_id,
            run_id=run_id,
            platform=outcome.request.platform,
            operation="probe",
            candidate_key=outcome.request.candidate_key,
            cache_hit=cache_hit,
            status="failed" if outcome.error_kind else "succeeded",
            error_kind=outcome.error_kind,
            error_message=outcome.error_message,
            started_at=outcome.started_at,
            finished_at=outcome.finished_at,
        )

    def _rank_probe_candidates(
        self, config: DiscoveryConfig
    ) -> tuple[_ProbeCandidate, ...]:
        parameters: list[Any] = [config.query_pack_version, config.campaign_id]
        sql = """
            SELECT c.platform, c.source_id, c.candidate_key, c.source_url,
                   c.title, c.uploader, c.duration_seconds,
                   MIN(d.platform_position) AS position
            FROM candidates c
            JOIN candidate_discoveries d ON d.candidate_key = c.candidate_key
            JOIN queries q ON q.query_id = d.query_id
            WHERE q.query_pack_version = ? AND q.campaign_id = ?
              AND c.status = 'discovered' AND c.hard_excluded = 0
              AND c.source_policy_version IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM legacy_downloads l
                  WHERE l.candidate_key = c.candidate_key
              )
              AND NOT EXISTS (
                  SELECT 1 FROM adapter_calls a
                  WHERE a.candidate_key = c.candidate_key
                    AND a.operation = 'probe' AND a.status = 'failed'
                    AND a.error_kind IN ('not_found', 'private', 'unsupported')
              )
        """
        if config.query_ids:
            placeholders = ",".join("?" for _ in config.query_ids)
            sql += f" AND q.query_id IN ({placeholders})"
            parameters.extend(config.query_ids)
        sql += " GROUP BY c.candidate_key"
        rows = self.database.connection.execute(sql, parameters).fetchall()
        candidates: list[_ProbeCandidate] = []
        for row in rows:
            cheap_metadata = CandidateMetadata(
                    candidate_key=row["candidate_key"],
                    title=row["title"] or "",
                    uploader=row["uploader"] or "",
                )
            cheap_source = (
                score_sign_mobile_source(
                    cheap_metadata,
                    self.scoring,
                    width=None,
                    height=None,
                    duration_seconds=row["duration_seconds"],
                    discovered_by_mobile_query=True,
                )
                if config.campaign_id == "sign_action_v1"
                else score_source(cheap_metadata, self.scoring)
            )
            if cheap_source.hard_excluded:
                continue
            candidates.append(
                _ProbeCandidate(
                    platform=row["platform"],
                    source_id=row["source_id"],
                    candidate_key=row["candidate_key"],
                    source_url=row["source_url"],
                    position=int(row["position"]),
                    cheap_source_score=cheap_source.score,
                )
            )
        candidates.sort(
            key=lambda item: (
                -item.cheap_source_score,
                item.position,
                item.candidate_key,
            )
        )
        return _rotate_probe_platforms(candidates)

    def _reserve_probe_candidates(
        self,
        *,
        run_id: str,
        config: DiscoveryConfig,
        ranked: Sequence[_ProbeCandidate],
    ) -> tuple[tuple[_ProbeCandidate, ...], int, int, bool]:
        now = utc_now()
        ranked_by_key = {item.candidate_key: item for item in ranked}
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE probe_selections
                SET status = 'probed', completed_run_id = ?, completed_at = ?
                WHERE campaign_id = ? AND query_pack_version = ?
                  AND status = 'selected'
                  AND EXISTS (
                      SELECT 1 FROM candidates c
                      WHERE c.candidate_key = probe_selections.candidate_key
                        AND c.source_policy_version = ?
                  )
                """,
                (
                    run_id,
                    now,
                    config.campaign_id,
                    config.query_pack_version,
                    self.scoring.policy_version,
                ),
            )
            connection.execute(
                """
                UPDATE probe_selections
                SET status = 'blocked', completed_run_id = ?, completed_at = ?
                WHERE campaign_id = ? AND query_pack_version = ?
                  AND status = 'selected'
                  AND EXISTS (
                      SELECT 1 FROM candidates c
                      WHERE c.candidate_key = probe_selections.candidate_key
                        AND (c.status <> 'discovered' OR c.hard_excluded = 1)
                  )
                """,
                (
                    run_id,
                    now,
                    config.campaign_id,
                    config.query_pack_version,
                ),
            )
            selection_rows = connection.execute(
                """
                SELECT candidate_key, status, selection_rank
                FROM probe_selections
                WHERE campaign_id = ? AND query_pack_version = ?
                ORDER BY selection_rank
                """,
                (config.campaign_id, config.query_pack_version),
            ).fetchall()
            existing_keys = {row["candidate_key"] for row in selection_rows}
            pending_keys = [
                row["candidate_key"]
                for row in selection_rows
                if row["status"] == "selected" and row["candidate_key"] in ranked_by_key
            ]
            unselected = [
                item for item in ranked if item.candidate_key not in existing_keys
            ]
            remaining = max(0, config.probe_limit - len(selection_rows))
            newly_selected = unselected[:remaining]
            next_rank = max(
                (int(row["selection_rank"]) for row in selection_rows),
                default=0,
            )
            for offset, candidate in enumerate(newly_selected, start=1):
                connection.execute(
                    """
                    INSERT INTO probe_selections(
                        campaign_id, query_pack_version, candidate_key,
                        selection_rank, status, selected_run_id, selected_at
                    ) VALUES (?, ?, ?, ?, 'selected', ?, ?)
                    """,
                    (
                        config.campaign_id,
                        config.query_pack_version,
                        candidate.candidate_key,
                        next_rank + offset,
                        run_id,
                        now,
                    ),
                )
        pending = tuple(ranked_by_key[key] for key in pending_keys)
        work = pending + tuple(newly_selected)
        cumulative = len(selection_rows) + len(newly_selected)
        exhausted = len(unselected) > len(newly_selected)
        return work, cumulative, len(newly_selected), exhausted

    def _complete_probe_selection(
        self,
        *,
        config: DiscoveryConfig,
        candidate_key: str,
        run_id: str,
        status: str,
        completed_at: str,
    ) -> None:
        if status not in {"probed", "failed"}:
            raise ValueError("probe completion status must be probed or failed")
        with self.database.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE probe_selections
                SET status = ?, completed_run_id = ?, completed_at = ?
                WHERE campaign_id = ? AND query_pack_version = ?
                  AND candidate_key = ? AND status = 'selected'
                """,
                (
                    status,
                    run_id,
                    completed_at,
                    config.campaign_id,
                    config.query_pack_version,
                    candidate_key,
                ),
            ).rowcount
        if changed != 1:
            raise RuntimeError("probe selection was not pending at completion")

    def _legacy_uploader_prior(self, probe: ProbeResult) -> int:
        if not probe.uploader_id:
            return 0
        row = self.database.connection.execute(
            """
            SELECT prior_points FROM uploader_priors
            WHERE platform = ? AND uploader_id = ?
            """,
            (probe.platform, probe.uploader_id),
        ).fetchone()
        return 0 if row is None else int(row["prior_points"])


def _validate_search_hits(hits: Sequence[SearchHit], request: SearchRequest) -> None:
    if len(hits) > request.limit:
        raise ValueError("adapter returned more hits than requested")
    positions: set[int] = set()
    for hit in hits:
        if hit.platform != request.platform:
            raise ValueError("search hit platform does not match request")
        if (
            hit.query != request.query
            or hit.lang != request.lang
            or hit.query_pack_version != request.query_pack_version
        ):
            raise ValueError("search hit attribution does not match request")
        if hit.position > request.limit or hit.position in positions:
            raise ValueError("search hit positions must be unique and within limit")
        positions.add(hit.position)


def _search_hit_to_json(hit: SearchHit) -> dict[str, Any]:
    return {
        "platform": hit.platform,
        "source_id": hit.source_id,
        "candidate_key": hit.candidate_key,
        "source_url": hit.source_url,
        "position": hit.position,
        "query": hit.query,
        "lang": hit.lang,
        "query_pack_version": hit.query_pack_version,
        "title": hit.title,
        "uploader": hit.uploader,
        "duration_seconds": hit.duration_seconds,
        "raw_summary": sanitize_metadata(hit.raw_summary),
    }


def _search_hits_from_cache(
    payload: Any,
    request: SearchRequest,
) -> tuple[SearchHit, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError("invalid search cache payload")
    hits = tuple(SearchHit(**entry) for entry in payload["entries"])
    _validate_search_hits(hits, request)
    return hits


def _sanitized_probe(probe: ProbeResult) -> ProbeResult:
    return ProbeResult(
        platform=probe.platform,
        source_id=probe.source_id,
        candidate_key=probe.candidate_key,
        source_url=probe.source_url,
        canonical_url=probe.canonical_url,
        title=probe.title,
        video_description=probe.video_description,
        tags=probe.tags,
        uploader=probe.uploader,
        uploader_id=probe.uploader_id,
        channel=probe.channel,
        playlist=probe.playlist,
        duration_seconds=probe.duration_seconds,
        upload_date=probe.upload_date,
        availability=probe.availability,
        filesize_approx=probe.filesize_approx,
        width=probe.width,
        height=probe.height,
        is_live=probe.is_live,
        live_status=probe.live_status,
        raw_metadata=sanitize_metadata(probe.raw_metadata),
    )


def _error_details(error: Exception) -> tuple[str, str]:
    if isinstance(error, AdapterError):
        return error.kind.value, sanitize_error_text(error.message)
    return AdapterErrorKind.TOOL_ERROR.value, sanitize_error_text(str(error) or type(error).__name__)


def _after(timestamp: str, seconds: int) -> str:
    value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (value + timedelta(seconds=seconds)).astimezone(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def _rotate_probe_platforms(
    candidates: Sequence[_ProbeCandidate],
) -> tuple[_ProbeCandidate, ...]:
    """Rank within each platform, then round-robin platforms without quotas."""

    buckets: dict[str, list[_ProbeCandidate]] = {}
    for candidate in candidates:
        buckets.setdefault(candidate.platform, []).append(candidate)
    result: list[_ProbeCandidate] = []
    offsets = {platform: 0 for platform in buckets}
    while True:
        progressed = False
        for platform in sorted(buckets):
            offset = offsets[platform]
            if offset < len(buckets[platform]):
                result.append(buckets[platform][offset])
                offsets[platform] += 1
                progressed = True
        if not progressed:
            break
    return tuple(result)
