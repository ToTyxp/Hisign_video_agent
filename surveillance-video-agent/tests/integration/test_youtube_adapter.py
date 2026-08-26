import json
from pathlib import Path
import tempfile
import unittest

from surveillance_video_agent.adapters.base import CommandResult
from surveillance_video_agent.adapters.youtube import YouTubeAdapter
from surveillance_video_agent.contracts import (
    AdapterErrorKind,
    DownloadRequest,
    ProbeRequest,
    SearchRequest,
)


SOURCE_ID = "abc123DEF_4"
SOURCE_URL = f"https://www.youtube.com/watch?v={SOURCE_ID}"
CANDIDATE_KEY = f"youtube:{SOURCE_ID}"


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, args, *, timeout_seconds, cwd=None, env=None):
        self.calls.append(
            {
                "args": list(args),
                "timeout_seconds": timeout_seconds,
                "cwd": cwd,
                "env": dict(env or {}),
            }
        )
        response = self.responses.pop(0)
        return response(self.calls[-1]) if callable(response) else response


def search_request(limit=20):
    return SearchRequest(
        platform="youtube",
        query="security camera person holding banner",
        lang="en",
        query_pack_version="demand-action-v1.0.0",
        network_config="default",
        limit=limit,
        request_id="search-request-1",
        run_id="run-1",
    )


def probe_request(source_url=SOURCE_URL):
    return ProbeRequest(
        platform="youtube",
        source_id=SOURCE_ID,
        candidate_key=CANDIDATE_KEY,
        source_url=source_url,
        network_config="default",
        request_id="probe-request-1",
        run_id="run-1",
    )


class YouTubeAdapterIntegrationTests(unittest.TestCase):
    def test_search_normalizes_results_and_uses_safe_yt_dlp_flags(self) -> None:
        payload = {
            "entries": [
                {
                    "id": SOURCE_ID,
                    "title": "CCTV person holding a banner",
                    "uploader": "Camera Archive",
                    "duration": 42,
                },
                {"id": "secondID_22", "title": "Security camera protest", "duration": 80},
            ]
        }
        runner = FakeRunner([CommandResult(0, json.dumps(payload), "")])
        hits = YouTubeAdapter(runner).search(search_request(limit=2))

        self.assertEqual([hit.candidate_key for hit in hits], [CANDIDATE_KEY, "youtube:secondID_22"])
        self.assertEqual(hits[0].position, 1)
        args = runner.calls[0]["args"]
        self.assertIn("--ignore-config", args)
        self.assertIn("--no-plugin-dirs", args)
        self.assertIn("--no-playlist", args)
        self.assertIn("ytsearch2:security camera person holding banner", args)
        self.assertNotIn("youtube:player_client=android", " ".join(args))
        self.assertEqual(runner.calls[0]["env"]["YTDLP_NO_PLUGINS"], "1")

    def test_probe_preserves_description_and_redacts_raw_metadata(self) -> None:
        description = "Original platform description\nwith every line preserved."
        payload = {
            "id": SOURCE_ID,
            "title": "CCTV clip",
            "description": description,
            "tags": ["cctv", "banner"],
            "uploader": "Archive",
            "uploader_id": "archive-id",
            "channel": "Camera Archive",
            "playlist": "Raw CCTV",
            "duration": 42.5,
            "upload_date": "20240131",
            "availability": "public",
            "filesize_approx": 123456,
            "width": 1920,
            "height": 1080,
            "is_live": False,
            "live_status": "not_live",
            "http_headers": {"Cookie": "secret-cookie"},
            "formats": [{"url": "https://foo.googlevideo.com/private-stream"}],
        }
        runner = FakeRunner([CommandResult(0, json.dumps(payload), "")])
        result = YouTubeAdapter(runner).probe(probe_request())

        self.assertEqual(result.video_description, description)
        self.assertEqual(result.tags, ("cctv", "banner"))
        self.assertEqual(result.height, 1080)
        self.assertIs(result.is_live, False)
        self.assertNotIn("formats", result.raw_metadata)
        self.assertNotIn("http_headers", result.raw_metadata)
        self.assertNotIn("url", result.raw_metadata)
        self.assertNotIn("googlevideo.com", json.dumps(result.raw_metadata))
        self.assertNotIn("secret-cookie", json.dumps(result.raw_metadata))
        args = runner.calls[0]["args"]
        self.assertIn("--skip-download", args)
        self.assertIn("--no-write-subs", args)
        self.assertIn("--no-write-auto-subs", args)

    def test_probe_rejects_non_youtube_and_mismatched_urls_before_runner(self) -> None:
        runner = FakeRunner([])
        adapter = YouTubeAdapter(runner)
        with self.assertRaises(ValueError):
            adapter.probe(probe_request("https://example.com/watch?v=abc123DEF_4"))
        with self.assertRaises(ValueError):
            adapter.probe(probe_request("https://www.youtube.com/watch?v=different11"))
        self.assertEqual(runner.calls, [])

    def test_download_is_single_item_capped_and_reports_created_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            managed_root = Path(temporary) / "pool" / "tmp"
            output_dir = managed_root / "run-1"

            def download_response(call):
                output_template = Path(call["args"][call["args"].index("--output") + 1])
                media_path = Path(str(output_template).replace("%(ext)s", "mp4"))
                media_path.write_bytes(b"fake-media")
                return CommandResult(0, f"{media_path}\n", "")

            runner = FakeRunner([download_response])
            request = DownloadRequest(
                platform="youtube",
                source_id=SOURCE_ID,
                candidate_key=CANDIDATE_KEY,
                source_url=SOURCE_URL,
                managed_root=managed_root,
                output_dir=output_dir,
                network_config="default",
                request_id="download-request-1",
                run_id="run-1",
            )
            result = YouTubeAdapter(runner).download(request)

            self.assertTrue(result.success)
            self.assertEqual(result.bytes_downloaded, len(b"fake-media"))
            self.assertEqual(result.file_path, (output_dir / f"youtube-{SOURCE_ID}.mp4").resolve())
            args = runner.calls[0]["args"]
            self.assertEqual(args[-1], SOURCE_URL)
            self.assertIn("--max-filesize", args)
            self.assertIn("bestvideo[height<=1080]+bestaudio/best[height<=1080]", args)
            self.assertIn("--no-playlist", args)
            self.assertNotIn("android", " ".join(args).lower())

    def test_download_classifies_timeout_without_creating_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            managed_root = Path(temporary) / "pool" / "tmp"
            request = DownloadRequest(
                platform="youtube",
                source_id=SOURCE_ID,
                candidate_key=CANDIDATE_KEY,
                source_url=SOURCE_URL,
                managed_root=managed_root,
                output_dir=managed_root / "run-1",
                network_config="default",
                request_id="download-request-1",
                run_id="run-1",
            )
            runner = FakeRunner([CommandResult(124, "", "timed out", timed_out=True)])
            result = YouTubeAdapter(runner).download(request)
            self.assertFalse(result.success)
            self.assertEqual(result.error_kind, AdapterErrorKind.TIMEOUT)


if __name__ == "__main__":
    unittest.main()
