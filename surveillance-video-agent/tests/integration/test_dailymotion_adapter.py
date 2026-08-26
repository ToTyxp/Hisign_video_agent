from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Callable, Mapping, Sequence

from surveillance_video_agent.adapters.base import CommandResult
from surveillance_video_agent.adapters.dailymotion import DailymotionAdapter
from surveillance_video_agent.contracts import (
    AdapterError,
    AdapterErrorKind,
    DownloadRequest,
    ProbeRequest,
    SearchRequest,
    make_candidate_key,
)


class FakeRunner:
    def __init__(
        self,
        results: Sequence[CommandResult],
        callbacks: Sequence[Callable[[Sequence[str]], None] | None] | None = None,
    ) -> None:
        self.results = list(results)
        self.callbacks = list(callbacks or [None] * len(results))
        self.calls: list[tuple[list[str], float, Path | None, Mapping[str, str] | None]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        self.calls.append((list(args), timeout_seconds, cwd, env))
        callback = self.callbacks.pop(0)
        if callback is not None:
            callback(args)
        return self.results.pop(0)


def search_request(query: str = "security camera person kneeling") -> SearchRequest:
    return SearchRequest(
        platform="dailymotion",
        query=query,
        lang="en",
        query_pack_version="qp-v1",
        network_config="default",
        limit=20,
        request_id="request-search",
        run_id="run-1",
    )


def probe_request(source_url: str = "https://www.dailymotion.com/video/x8abc12") -> ProbeRequest:
    source_id = "x8abc12"
    return ProbeRequest(
        platform="dailymotion",
        source_id=source_id,
        candidate_key=make_candidate_key("dailymotion", source_id),
        source_url=source_url,
        network_config="default",
        request_id="request-probe",
        run_id="run-1",
    )


class DailymotionAdapterTests(unittest.TestCase):
    def test_search_uses_native_search_extractor_and_caps_results(self) -> None:
        payload = {
            "entries": [
                {
                    "id": "x8abc12",
                    "title": "Security camera footage",
                    "uploader": "Camera Archive",
                    "duration": 42,
                    "url": "http://untrusted.invalid/media.m3u8?token=secret",
                    "extractor_key": "Dailymotion",
                },
                {"id": "x8abc12", "title": "duplicate identity"},
                {"id": "x9def34", "title": "Doorbell camera", "duration": 13.5},
                {"id": "bad:id", "title": "invalid identity"},
                {"id": "x" * 129, "title": "overlong identity"},
            ]
        }
        runner = FakeRunner([CommandResult(0, stdout=json.dumps(payload))])
        adapter = DailymotionAdapter(runner=runner)

        hits = adapter.search(search_request("camera $(touch /tmp/never-run)"))

        self.assertEqual([hit.source_id for hit in hits], ["x8abc12", "x9def34"])
        self.assertEqual(hits[0].candidate_key, "dailymotion:x8abc12")
        self.assertEqual(
            hits[0].source_url, "https://www.dailymotion.com/video/x8abc12"
        )
        self.assertEqual(hits[0].position, 1)
        args, timeout, cwd, env = runner.calls[0]
        self.assertEqual(args[0], "yt-dlp")
        self.assertIn("--ignore-config", args)
        self.assertIn("--no-plugin-dirs", args)
        self.assertIn("--flat-playlist", args)
        self.assertEqual(args[args.index("--playlist-end") + 1], "20")
        self.assertEqual(
            args[-1],
            "https://www.dailymotion.com/search/"
            "camera+%24%28touch+%2Ftmp%2Fnever-run%29/videos",
        )
        self.assertEqual(timeout, 60.0)
        self.assertIsNone(cwd)
        self.assertEqual(env, {"YTDLP_NO_PLUGINS": "1"})

    def test_probe_preserves_full_description_and_redacts_transport_data(self) -> None:
        description = "Original platform description. " * 500
        payload = {
            "id": "x8abc12",
            "title": "CCTV raw footage",
            "description": description,
            "tags": ["cctv", "raw", 99, ""],
            "uploader": "Archive",
            "uploader_id": "xowner",
            "duration": 91,
            "timestamp": 1_700_000_000,
            "upload_date": "20231114",
            "availability": "public",
            "is_live": False,
            "live_status": "not_live",
            "filesize": 654321,
            "filesize_approx": 123456,
            "width": 1920,
            "height": 1080,
            "http_headers": {"Authorization": "Bearer secret"},
            "url": "https://cdn.example/video.m3u8?token=secret",
            "formats": [{"url": "https://cdn.example/format?token=secret"}],
        }
        runner = FakeRunner([CommandResult(0, stdout=json.dumps(payload))])
        adapter = DailymotionAdapter(runner=runner)

        result = adapter.probe(probe_request())

        self.assertEqual(result.video_description, description)
        self.assertEqual(result.tags, ("cctv", "raw"))
        self.assertEqual(result.upload_date, "2023-11-14")
        self.assertFalse(result.is_live)
        self.assertEqual(result.live_status, "not_live")
        self.assertEqual(result.filesize_approx, 654321)
        self.assertEqual(result.canonical_url, "https://www.dailymotion.com/video/x8abc12")
        self.assertEqual(result.raw_metadata["http_headers"], "[REDACTED]")
        self.assertEqual(result.raw_metadata["url"], "[REDACTED]")
        self.assertNotIn("formats", result.raw_metadata)
        args = runner.calls[0][0]
        self.assertIn("--skip-download", args)
        self.assertIn("--no-playlist", args)
        self.assertIn("--no-write-subs", args)
        self.assertIn("--no-write-auto-subs", args)
        self.assertEqual(args[-2:], ["--", result.canonical_url])

    def test_probe_rejects_noncanonical_or_unsafe_urls_before_runner(self) -> None:
        unsafe_urls = (
            "http://www.dailymotion.com/video/x8abc12",
            "https://evil.example/video/x8abc12",
            "https://attacker.dailymotion.com/video/x8abc12",
            "https://www.dailymotion.com/embed/video/x8abc12",
            "https://www.dailymotion.com/video/x8abc12?playlist=x123",
            "https://www.dailymotion.com/video/different",
        )
        for source_url in unsafe_urls:
            with self.subTest(source_url=source_url):
                runner = FakeRunner([])
                with self.assertRaises(ValueError):
                    DailymotionAdapter(runner=runner).probe(probe_request(source_url))
                self.assertEqual(runner.calls, [])

    def test_probe_rejects_overlong_source_id_before_runner(self) -> None:
        source_id = "x" * 129
        request = ProbeRequest(
            platform="dailymotion",
            source_id=source_id,
            candidate_key=make_candidate_key("dailymotion", source_id),
            source_url=f"https://www.dailymotion.com/video/{source_id}",
            network_config="default",
            request_id="request-probe-overlong",
            run_id="run-1",
        )
        runner = FakeRunner([])

        with self.assertRaisesRegex(ValueError, "invalid Dailymotion source_id"):
            DailymotionAdapter(runner=runner).probe(request)

        self.assertEqual(runner.calls, [])

    def test_probe_classifies_rate_limit_and_sanitizes_error(self) -> None:
        runner = FakeRunner(
            [CommandResult(1, stderr="HTTP Error 429 Authorization: bearer-secret")]
        )
        with self.assertRaises(AdapterError) as context:
            DailymotionAdapter(runner=runner).probe(probe_request())
        self.assertEqual(context.exception.kind, AdapterErrorKind.RATE_LIMITED)
        self.assertNotIn("bearer-secret", context.exception.message)

    def test_download_is_single_video_managed_and_resource_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            managed_root = Path(temporary) / "managed"
            output_dir = managed_root / "run-1" / "candidate"
            final_path = output_dir / "dailymotion_x8abc12.mp4"

            def create_download(_: Sequence[str]) -> None:
                final_path.parent.mkdir(parents=True, exist_ok=True)
                final_path.write_bytes(b"video-bytes")

            runner = FakeRunner(
                [CommandResult(0, stdout=str(final_path))], callbacks=[create_download]
            )
            request = DownloadRequest(
                platform="dailymotion",
                source_id="x8abc12",
                candidate_key="dailymotion:x8abc12",
                source_url="https://dai.ly/x8abc12",
                managed_root=managed_root,
                output_dir=output_dir,
                network_config="default",
                request_id="request-download",
                run_id="run-1",
            )

            result = DailymotionAdapter(runner=runner).download(request)

            self.assertTrue(result.success)
            self.assertEqual(result.file_path, final_path.resolve())
            self.assertEqual(result.bytes_downloaded, len(b"video-bytes"))
            args, timeout, cwd, _ = runner.calls[0]
            self.assertIn("--no-playlist", args)
            self.assertIn("--max-filesize", args)
            self.assertEqual(
                args[args.index("--max-filesize") + 1], str(2 * 1024 * 1024 * 1024)
            )
            self.assertEqual(
                args[args.index("--format") + 1],
                "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            )
            self.assertIn("--no-write-thumbnail", args)
            self.assertIn("--no-write-info-json", args)
            self.assertEqual(
                args[-2:], ["--", "https://www.dailymotion.com/video/x8abc12"]
            )
            self.assertEqual(cwd, output_dir.resolve())
            self.assertEqual(timeout, 1800.0)
            self.assertNotIn("player_client", " ".join(args))
            self.assertNotIn("android", " ".join(args).lower())

    def test_download_rejects_output_directory_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "managed"
            request = DownloadRequest(
                platform="dailymotion",
                source_id="x8abc12",
                candidate_key="dailymotion:x8abc12",
                source_url="https://www.dailymotion.com/video/x8abc12",
                managed_root=root,
                output_dir=Path(temporary) / "outside",
                network_config="default",
                request_id="request-download",
                run_id="run-1",
            )
            runner = FakeRunner([])
            with self.assertRaises(ValueError):
                DailymotionAdapter(runner=runner).download(request)
            self.assertEqual(runner.calls, [])

    def test_download_rejects_symlink_reported_as_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            managed_root = Path(temporary) / "managed"
            output_dir = managed_root / "run-1" / "candidate"
            target = output_dir / "real.mp4"
            reported_link = output_dir / "dailymotion_x8abc12.mp4"

            def create_symlink_output(_: Sequence[str]) -> None:
                output_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"video-bytes")
                reported_link.symlink_to(target)

            runner = FakeRunner(
                [CommandResult(0, stdout=str(reported_link))],
                callbacks=[create_symlink_output],
            )
            request = DownloadRequest(
                platform="dailymotion",
                source_id="x8abc12",
                candidate_key="dailymotion:x8abc12",
                source_url="https://www.dailymotion.com/video/x8abc12",
                managed_root=managed_root,
                output_dir=output_dir,
                network_config="default",
                request_id="request-download-symlink",
                run_id="run-1",
            )

            result = DailymotionAdapter(runner=runner).download(request)

            self.assertFalse(result.success)
            self.assertEqual(result.error_kind, AdapterErrorKind.TOOL_ERROR)
            self.assertIn("symlink", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
