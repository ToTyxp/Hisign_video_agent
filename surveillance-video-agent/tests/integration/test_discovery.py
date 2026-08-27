from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from surveillance_video_agent.adapters.base import BasePlatformAdapter
from surveillance_video_agent.contracts import (
    AdapterError,
    AdapterErrorKind,
    DownloadRequest,
    DownloadResult,
    ProbeRequest,
    ProbeResult,
    SearchHit,
    SearchRequest,
)
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.discovery import DiscoveryConfig, DiscoveryService
from surveillance_video_agent.scoring import load_scoring_bundle


ROOT = Path(__file__).resolve().parents[2]
QUERY_PACKS = (
    ROOT
    / "query-packs/demand_action_v1/demand_action_v1.qp.v1.0.0.draft.json",
    ROOT
    / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json",
)
FIGHT_PACK = QUERY_PACKS[1]
POLICY = ROOT / "query-packs/scoring-policy.v1.0.0.draft.json"
QUERY_IDS = (
    "fcv1-conflict-no-attack-en-01",
    "fcv1-conflict-no-attack-en-02",
    "fcv1-conflict-no-attack-en-03",
    "fcv1-play-training-en-01",
)


class ConcurrencyTracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active_by_platform: dict[str, int] = {}
        self.max_by_platform: dict[str, int] = {}
        self.active_global = 0
        self.max_global = 0

    def enter(self, platform: str) -> None:
        with self.lock:
            active = self.active_by_platform.get(platform, 0) + 1
            self.active_by_platform[platform] = active
            self.max_by_platform[platform] = max(
                active, self.max_by_platform.get(platform, 0)
            )
            self.active_global += 1
            self.max_global = max(self.max_global, self.active_global)

    def leave(self, platform: str) -> None:
        with self.lock:
            self.active_by_platform[platform] -= 1
            self.active_global -= 1


class FakeDiscoveryAdapter(BasePlatformAdapter):
    def __init__(
        self,
        platform: str,
        tracker: ConcurrencyTracker,
        *,
        result_count: int = 3,
        fail_search: bool = False,
        fail_probe_suffix: str | None = None,
    ) -> None:
        self.platform = platform
        self.tracker = tracker
        self.result_count = result_count
        self.fail_search = fail_search
        self.fail_probe_suffix = fail_probe_suffix
        self.search_calls = 0
        self.probe_calls = 0

    def search(self, request: SearchRequest) -> list[SearchHit]:
        self.tracker.enter(self.platform)
        try:
            self.search_calls += 1
            time.sleep(0.015)
            if self.fail_search:
                raise AdapterError(AdapterErrorKind.NETWORK, "simulated network failure")
            if self.result_count == 3:
                records = (
                    ("good", "CCTV heated argument"),
                    ("weak", "heated argument"),
                    ("hard", "CCTV security camera review heated argument"),
                )
            else:
                records = tuple(
                    (f"good-{index}", f"CCTV heated argument {index}")
                    for index in range(1, self.result_count + 1)
                )
            return [
                SearchHit(
                    platform=self.platform,
                    source_id=f"{self.platform}-{suffix}",
                    candidate_key=f"{self.platform}:{self.platform}-{suffix}",
                    source_url=f"https://example.test/{self.platform}/{suffix}",
                    position=position,
                    query=request.query,
                    lang=request.lang,
                    query_pack_version=request.query_pack_version,
                    title=title,
                    uploader=f"{self.platform} archive",
                    duration_seconds=30,
                    raw_summary={"position": position},
                )
                for position, (suffix, title) in enumerate(records, start=1)
            ][: request.limit]
        finally:
            self.tracker.leave(self.platform)

    def probe(self, request: ProbeRequest) -> ProbeResult:
        self.tracker.enter(self.platform)
        try:
            self.probe_calls += 1
            time.sleep(0.015)
            if self.fail_probe_suffix and request.source_id.endswith(
                self.fail_probe_suffix
            ):
                raise AdapterError(AdapterErrorKind.NOT_FOUND, "simulated missing video")
            weak = request.source_id.endswith("weak")
            return ProbeResult(
                platform=self.platform,
                source_id=request.source_id,
                candidate_key=request.candidate_key,
                source_url=request.source_url,
                canonical_url=request.source_url,
                title="heated argument" if weak else "CCTV heated argument",
                video_description="raw footage" if weak else "security camera raw uncut footage",
                tags=("heated argument",),
                uploader=f"{self.platform} archive",
                uploader_id=f"{self.platform}-archive-id",
                channel="Camera Archive",
                duration_seconds=30,
                availability="public",
                filesize_approx=1024,
                width=640,
                height=360,
                is_live=False,
                live_status="not_live",
                raw_metadata={"token": "safe fake"},
            )
        finally:
            self.tracker.leave(self.platform)

    def download(self, request: DownloadRequest) -> DownloadResult:
        raise AssertionError("discovery must never download")


class DiscoveryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = CandidateDatabase(
            Path(self.temporary.name) / "candidates.sqlite3"
        )
        self.database.initialize()
        self.database.register_frozen_query_pack(FIGHT_PACK)
        self.scoring = load_scoring_bundle(POLICY, QUERY_PACKS)
        self.tracker = ConcurrencyTracker()

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def adapters(self, **kwargs) -> dict[str, FakeDiscoveryAdapter]:
        return {
            platform: FakeDiscoveryAdapter(platform, self.tracker, **kwargs)
            for platform in ("youtube", "dailymotion", "peertube")
        }

    def config(self, **changes) -> DiscoveryConfig:
        values = {
            "campaign_id": "fight_confounder_v1",
            "query_pack_version": "fight_confounder_v1.qp.v1.0.0",
            "query_ids": QUERY_IDS,
        }
        values.update(changes)
        return DiscoveryConfig(**values)

    def test_parallel_discovery_dedupes_hard_gates_scores_and_reuses_caches(self) -> None:
        adapters = self.adapters()
        service = DiscoveryService(self.database, adapters, self.scoring)
        self.database.create_run("discovery-1", "discovery")
        first = service.discover_and_qualify(
            run_id="discovery-1",
            config=self.config(),
        )

        self.assertEqual(first.search_request_count, 12)
        self.assertEqual(first.discovered_hit_count, 36)
        self.assertEqual(first.unique_candidate_count, 9)
        self.assertEqual(first.cheap_hard_excluded_count, 3)
        self.assertEqual(first.probe_selected_count, 6)
        self.assertEqual(first.probe_network_call_count, 6)
        self.assertEqual(first.source_qualified_count, 3)
        self.assertEqual(first.task_qualified_score_count, 3)
        self.assertEqual(first.resource_eligible_count, 6)
        self.assertEqual(first.resource_ineligible_count, 0)
        self.assertFalse(first.probe_budget_exhausted)
        self.assertTrue(all(value <= 2 for value in self.tracker.max_by_platform.values()))
        self.assertGreater(self.tracker.max_global, 2)
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM candidates"
            ).fetchone()[0],
            9,
        )
        hard = self.database.connection.execute(
            "SELECT COUNT(*) FROM candidates WHERE hard_excluded = 1"
        ).fetchone()[0]
        self.assertEqual(hard, 3)
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM adapter_calls WHERE run_id = 'discovery-1'"
            ).fetchone()[0],
            18,
        )

        network_counts = {
            platform: (adapter.search_calls, adapter.probe_calls)
            for platform, adapter in adapters.items()
        }
        self.database.create_run("discovery-2", "discovery")
        second = service.discover_and_qualify(
            run_id="discovery-2",
            config=self.config(),
        )
        self.assertEqual(second.search_cache_hit_count, 12)
        self.assertEqual(second.probe_selected_count, 0)
        self.assertEqual(second.probe_cache_hit_count, 0)
        self.assertEqual(second.probe_network_call_count, 0)
        self.assertEqual(
            network_counts,
            {
                platform: (adapter.search_calls, adapter.probe_calls)
                for platform, adapter in adapters.items()
            },
        )
        cached_calls = self.database.connection.execute(
            """
            SELECT operation, COUNT(*) AS count
            FROM adapter_calls
            WHERE run_id = 'discovery-2' AND cache_hit = 1
            GROUP BY operation ORDER BY operation
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in cached_calls],
            [("search", 12)],
        )
        selections = self.database.connection.execute(
            """
            SELECT status, COUNT(*) FROM probe_selections
            GROUP BY status ORDER BY status
            """
        ).fetchall()
        self.assertEqual([tuple(row) for row in selections], [("probed", 6)])

    def test_probe_budget_is_global_per_campaign_and_never_expands(self) -> None:
        adapters = self.adapters(result_count=5)
        service = DiscoveryService(self.database, adapters, self.scoring)
        self.database.create_run("budget-run", "discovery")
        config = self.config(query_ids=(QUERY_IDS[0],), probe_limit=2)
        discovery = service.discover(
            run_id="budget-run",
            config=config,
        )
        self.assertEqual(discovery.unique_candidate_count, 15)
        self.database.connection.execute(
            """
            INSERT INTO legacy_downloads(
                candidate_key, youtube_id, legacy_status, source_path, imported_at
            ) VALUES ('youtube:youtube-good-1', 'youtube-good-1', 'accepted',
                      'legacy.json', '2026-08-26T00:00:00Z')
            """
        )
        qualification = service.qualify(run_id="budget-run", config=config)
        self.assertEqual(qualification.probe_attempted_count, 2)
        self.assertEqual(qualification.probe_network_call_count, 2)
        self.assertTrue(qualification.probe_budget_exhausted)
        self.assertEqual(sum(adapter.probe_calls for adapter in adapters.values()), 2)
        selected_platforms = self.database.connection.execute(
            """
            SELECT c.platform, COUNT(*)
            FROM probe_selections p
            JOIN candidates c ON c.candidate_key = p.candidate_key
            WHERE p.campaign_id = 'fight_confounder_v1'
            GROUP BY c.platform ORDER BY c.platform
            """
        ).fetchall()
        self.assertEqual(len(selected_platforms), 2)
        self.assertTrue(all(row[1] == 1 for row in selected_platforms))
        self.assertIsNone(
            self.database.connection.execute(
                """
                SELECT 1 FROM probe_selections
                WHERE candidate_key = 'youtube:youtube-good-1'
                """
            ).fetchone()
        )

        self.database.create_run("budget-run-2", "discovery")
        repeated = service.discover_and_qualify(
            run_id="budget-run-2",
            config=self.config(query_ids=(QUERY_IDS[0],), probe_limit=2),
        )
        self.assertEqual(repeated.probe_selected_count, 0)
        self.assertEqual(sum(adapter.probe_calls for adapter in adapters.values()), 2)
        self.assertTrue(repeated.probe_budget_exhausted)

    def test_discover_and_qualify_are_independent_and_pending_probe_uses_cache(self) -> None:
        adapters = self.adapters()
        service = DiscoveryService(self.database, adapters, self.scoring)
        config = self.config(query_ids=(QUERY_IDS[0],), probe_limit=1)
        self.database.create_run("split-run", "discovery")
        discovery = service.discover(run_id="split-run", config=config)
        self.assertEqual(discovery.search_request_count, 3)
        self.assertEqual(sum(adapter.probe_calls for adapter in adapters.values()), 0)

        candidate_key = "youtube:youtube-good"
        self.database.connection.execute(
            """
            INSERT INTO probe_selections(
                campaign_id, query_pack_version, candidate_key, selection_rank,
                status, selected_run_id, selected_at
            ) VALUES ('fight_confounder_v1', 'fight_confounder_v1.qp.v1.0.0',
                      ?, 1, 'selected', 'split-run', '2026-08-26T00:00:00Z')
            """,
            (candidate_key,),
        )
        cached = ProbeResult(
            platform="youtube",
            source_id="youtube-good",
            candidate_key=candidate_key,
            source_url="https://example.test/youtube/good",
            canonical_url="https://example.test/youtube/good",
            title="CCTV heated argument",
            video_description="security camera raw uncut footage",
            tags=("heated argument",),
            uploader="youtube archive",
            uploader_id="youtube-archive-id",
            channel="Camera Archive",
            duration_seconds=30,
            availability="public",
            filesize_approx=1024,
            width=640,
            height=360,
            is_live=False,
            live_status="not_live",
            raw_metadata={"cached": True},
        )
        self.database.upsert_probe_cache(
            cached,
            network_config="default",
            fetched_at="2026-08-26T00:00:00Z",
            expires_at="2099-08-26T00:00:00Z",
        )
        qualification = service.qualify(run_id="split-run", config=config)
        self.assertEqual(qualification.cumulative_probe_selection_count, 1)
        self.assertEqual(qualification.new_probe_selection_count, 0)
        self.assertEqual(qualification.probe_attempted_count, 1)
        self.assertEqual(qualification.probe_cache_hit_count, 1)
        self.assertEqual(qualification.probe_network_call_count, 0)
        self.assertEqual(qualification.source_qualified_count, 1)
        self.assertEqual(sum(adapter.probe_calls for adapter in adapters.values()), 0)

    def test_platform_and_probe_failures_are_audited_without_stopping_peers(self) -> None:
        adapters = self.adapters(fail_probe_suffix="weak")
        adapters["dailymotion"].fail_search = True
        service = DiscoveryService(self.database, adapters, self.scoring)
        self.database.create_run("failure-run", "discovery")
        summary = service.discover_and_qualify(
            run_id="failure-run",
            config=self.config(query_ids=QUERY_IDS[:2]),
        )
        self.assertEqual(summary.search_failure_count, 2)
        self.assertEqual(summary.probe_failure_count, 2)
        self.assertEqual(summary.source_qualified_count, 2)
        errors = self.database.connection.execute(
            """
            SELECT platform, operation, error_kind, COUNT(*) AS count
            FROM adapter_calls
            WHERE run_id = 'failure-run' AND status = 'failed'
            GROUP BY platform, operation, error_kind
            ORDER BY platform, operation
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in errors],
            [
                ("dailymotion", "search", "network", 2),
                ("peertube", "probe", "not_found", 1),
                ("youtube", "probe", "not_found", 1),
            ],
        )
        self.assertGreater(adapters["peertube"].probe_calls, 0)
        self.assertGreater(adapters["youtube"].probe_calls, 0)


if __name__ == "__main__":
    unittest.main()
