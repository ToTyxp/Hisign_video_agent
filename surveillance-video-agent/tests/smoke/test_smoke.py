from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from surveillance_video_agent.contracts import (
    AdapterError,
    AdapterErrorKind,
    DownloadResult,
    ProbeResult,
    SearchHit,
)
from surveillance_video_agent.smoke import (
    SmokeConfig,
    load_frozen_query,
    network_environment_summary,
    run_smoke,
)


class FakeAdapter:
    def __init__(self, platform: str, log: list[str], *, search_error: bool = False) -> None:
        self.platform = platform
        self.log = log
        self.search_error = search_error

    def search(self, request):
        self.log.append(f"search:{self.platform}")
        if self.search_error:
            raise AdapterError(AdapterErrorKind.NETWORK, "network failed")
        return [
            SearchHit(
                platform=self.platform,
                source_id=f"id-{self.platform}",
                candidate_key=f"{self.platform}:id-{self.platform}",
                source_url=f"https://example.com/{self.platform}",
                position=1,
                query=request.query,
                lang=request.lang,
                query_pack_version=request.query_pack_version,
            )
        ]

    def probe(self, request):
        self.log.append(f"probe:{self.platform}")
        return ProbeResult(
            platform=self.platform,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            canonical_url=request.source_url,
            duration_seconds=30,
            availability="public",
            filesize_approx=1024,
            is_live=False,
        )

    def download(self, request):
        self.log.append(f"download:{self.platform}")
        request.output_dir.mkdir(parents=True, exist_ok=True)
        path = request.output_dir / f"{self.platform}.mp4"
        path.write_bytes(b"fake video")
        return DownloadResult(
            platform=self.platform,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            success=True,
            file_path=path,
            bytes_downloaded=path.stat().st_size,
            returncode=0,
        )


class SmokeHarnessTests(unittest.TestCase):
    def _pack(self, directory: Path, *, status: str = "frozen") -> Path:
        path = directory / "pack.json"
        path.write_text(
            json.dumps(
                {
                    "status": status,
                    "network_config": "default",
                    "query_pack_version": "test.qp.v1",
                    "campaign_id": "test_campaign",
                    "queries": [
                        {"query_id": "q1", "query": "CCTV argument", "lang": "en"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_load_query_requires_frozen_pack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            query = load_frozen_query(self._pack(root), "q1")
            self.assertEqual(query.query, "CCTV argument")
            with self.assertRaises(ValueError):
                load_frozen_query(self._pack(root, status="draft"), "q1")

    def test_network_environment_is_inherited_without_exposing_proxy_values(self):
        self.assertEqual(network_environment_summary({})["proxy_environment_keys"], [])
        summary = network_environment_summary({"HTTPS_PROXY": "http://secret-proxy:1234"})
        self.assertEqual(summary["proxy_environment_keys"], ["HTTPS_PROXY"])
        self.assertFalse(summary["application_proxy_override"])
        self.assertNotIn("secret-proxy", json.dumps(summary))

    def test_candidate_index_must_fit_inside_search_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                SmokeConfig(
                    query_pack_path=self._pack(Path(temporary)),
                    query_id="q1",
                    search_limit=1,
                    candidate_index=2,
                )

    def test_platform_failures_do_not_stop_later_platforms_and_downloads_are_serial(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = self._pack(Path(temporary))
            log: list[str] = []
            config = SmokeConfig(
                query_pack_path=pack,
                query_id="q1",
                platforms=("youtube", "dailymotion", "peertube"),
                enable_download=True,
            )
            adapters = {
                "youtube": FakeAdapter("youtube", log, search_error=True),
                "dailymotion": FakeAdapter("dailymotion", log),
                "peertube": FakeAdapter("peertube", log),
            }
            report = run_smoke(
                config,
                adapters=adapters,
                technical_checker=lambda path: {"checked": path.name},
                environment={},
            )
            self.assertFalse(report["platforms"][0]["search"]["ok"])
            self.assertEqual(
                log,
                [
                    "search:youtube",
                    "search:dailymotion",
                    "probe:dailymotion",
                    "download:dailymotion",
                    "search:peertube",
                    "probe:peertube",
                    "download:peertube",
                ],
            )
            self.assertTrue(report["temp_cleaned"])
            self.assertFalse(Path(report["temp_root"]).exists())

    def test_unknown_probe_size_relies_on_download_hard_limit(self):
        class UnknownSizeAdapter(FakeAdapter):
            def probe(self, request):
                result = super().probe(request)
                return ProbeResult(
                    platform=result.platform,
                    source_id=result.source_id,
                    candidate_key=result.candidate_key,
                    source_url=result.source_url,
                    canonical_url=result.canonical_url,
                    duration_seconds=30,
                    availability=None,
                    filesize_approx=None,
                    is_live=None,
                )

        with tempfile.TemporaryDirectory() as temporary:
            pack = self._pack(Path(temporary))
            log: list[str] = []
            report = run_smoke(
                SmokeConfig(
                    query_pack_path=pack,
                    query_id="q1",
                    platforms=("youtube",),
                    enable_download=True,
                ),
                adapters={"youtube": UnknownSizeAdapter("youtube", log)},
                technical_checker=lambda path: {},
                environment={},
            )
            self.assertTrue(report["platforms"][0]["download"]["attempted"])
            self.assertTrue(report["platforms"][0]["download"]["success"])
            self.assertIn("download:youtube", log)


if __name__ == "__main__":
    unittest.main()
