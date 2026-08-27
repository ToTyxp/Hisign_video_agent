from __future__ import annotations

import unittest
from pathlib import Path

from surveillance_video_agent.contracts import (
    AdapterError,
    AdapterErrorKind,
    ProbeResult,
    SearchHit,
)
from surveillance_video_agent.discovery_smoke import (
    DiscoverySmokeConfig,
    run_discovery_smoke,
)


ROOT = Path(__file__).resolve().parents[2]
FIGHT_PACK = (
    ROOT
    / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json"
)
QUERY_PACKS = (
    ROOT
    / "query-packs/demand_action_v1/demand_action_v1.qp.v1.0.0.draft.json",
    FIGHT_PACK,
)
POLICY = ROOT / "query-packs/scoring-policy.v1.0.0.draft.json"


class FakeAdapter:
    def __init__(self, platform: str, *, fail_search: bool = False) -> None:
        self.platform = platform
        self.fail_search = fail_search
        self.download_calls = 0

    def search(self, request):
        if self.fail_search:
            raise AdapterError(AdapterErrorKind.NETWORK, "simulated network error")
        source_id = f"fake-{self.platform}"
        return [
            SearchHit(
                platform=self.platform,
                source_id=source_id,
                candidate_key=f"{self.platform}:{source_id}",
                source_url=f"https://example.test/{self.platform}/{source_id}",
                position=1,
                query=request.query,
                lang=request.lang,
                query_pack_version=request.query_pack_version,
                title="CCTV heated argument",
                uploader="Camera Archive",
                duration_seconds=30,
            )
        ]

    def probe(self, request):
        return ProbeResult(
            platform=self.platform,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            canonical_url=request.source_url,
            title="CCTV heated argument",
            video_description="security camera raw uncut footage",
            tags=("heated argument",),
            uploader="Camera Archive",
            uploader_id=f"{self.platform}-archive",
            channel="Camera Archive",
            duration_seconds=30,
            availability="public",
            filesize_approx=1024,
            width=640,
            height=360,
            is_live=False,
            live_status="not_live",
        )

    def download(self, request):
        self.download_calls += 1
        raise AssertionError("discovery smoke must never download")


class DiscoverySmokeTests(unittest.TestCase):
    def config(self) -> DiscoverySmokeConfig:
        return DiscoverySmokeConfig(
            query_pack_path=FIGHT_PACK,
            scoring_policy_path=POLICY,
            scoring_query_pack_paths=QUERY_PACKS,
            query_id="fcv1-conflict-no-attack-en-01",
            peertube_instance_hosts=("peertube.social",),
            search_limit=1,
            probe_limit=3,
            run_id="fake-online-discovery-smoke",
        )

    def adapters(self):
        return {
            platform: FakeAdapter(platform)
            for platform in ("youtube", "dailymotion", "peertube")
        }

    def test_full_persisted_path_is_reported_and_temp_state_is_cleaned(self) -> None:
        adapters = self.adapters()
        report = run_discovery_smoke(
            self.config(),
            adapters=adapters,
            environment={"HTTPS_PROXY": "http://secret-proxy.test:1234"},
        )
        self.assertTrue(report["ok"])
        self.assertTrue(report["temp_cleaned"])
        self.assertFalse(Path(report["temp_root"]).exists())
        self.assertFalse(report["download_attempted"])
        self.assertEqual(report["database"]["candidates"], 3)
        self.assertEqual(report["database"]["adapter_calls"], 6)
        self.assertEqual(report["qualification"]["probe_attempted_count"], 3)
        self.assertTrue(all(item["ok"] for item in report["platforms"]))
        self.assertNotIn("secret-proxy", str(report))
        self.assertTrue(all(adapter.download_calls == 0 for adapter in adapters.values()))

    def test_one_platform_failure_is_visible_without_hiding_peer_evidence(self) -> None:
        adapters = self.adapters()
        adapters["dailymotion"].fail_search = True
        report = run_discovery_smoke(
            self.config(), adapters=adapters, environment={}
        )
        self.assertFalse(report["ok"])
        platforms = {item["platform"]: item for item in report["platforms"]}
        self.assertFalse(platforms["dailymotion"]["ok"])
        self.assertEqual(
            platforms["dailymotion"]["failures"],
            [{"operation": "search", "error_kind": "network", "count": 1}],
        )
        self.assertTrue(platforms["youtube"]["ok"])
        self.assertTrue(platforms["peertube"]["ok"])
        self.assertEqual(report["database"]["failed_adapter_calls"], 1)


if __name__ == "__main__":
    unittest.main()
