from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence
import unittest

from surveillance_video_agent.adapters.base import CommandResult
from surveillance_video_agent.adapters.peertube import (
    HttpClientFailure,
    HttpJsonResponse,
    PeerTubeAdapter,
)
from surveillance_video_agent.contracts import (
    AdapterError,
    AdapterErrorKind,
    DownloadRequest,
    ProbeRequest,
    SearchRequest,
    make_candidate_key,
)


VIDEO_UUID = "123e4567-e89b-42d3-a456-426614174000"
SECOND_UUID = "223e4567-e89b-42d3-a456-426614174001"
INSTANCE = "videos.example.org"
SOURCE_URL = f"https://{INSTANCE}/videos/watch/{VIDEO_UUID}"
RAW_SEARCH_URL = f"https://{INSTANCE}/w/AbCdEfGhIjKl"
WRONG_SOURCE_URL = f"https://{INSTANCE}/videos/watch/{SECOND_UUID}"


class FakeHttpClient:
    def __init__(self, responses: list[HttpJsonResponse | BaseException]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int],
        timeout_seconds: float,
        allowed_hosts: frozenset[str],
    ) -> HttpJsonResponse:
        self.calls.append(
            {
                "url": url,
                "params": dict(params),
                "timeout_seconds": timeout_seconds,
                "allowed_hosts": allowed_hosts,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeRunner:
    def __init__(
        self,
        result: CommandResult,
        *,
        file_bytes: bytes | None = None,
    ) -> None:
        self.result = result
        self.file_bytes = file_bytes
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        command_args = list(args)
        self.calls.append(
            {
                "args": command_args,
                "timeout_seconds": timeout_seconds,
                "cwd": cwd,
                "env": env,
            }
        )
        if self.file_bytes is not None:
            template = Path(command_args[command_args.index("--output") + 1])
            output = Path(str(template).replace("%(ext)s", "mp4"))
            output.write_bytes(self.file_bytes)
            return CommandResult(
                returncode=self.result.returncode,
                stdout=f"{output}\n",
                stderr=self.result.stderr,
                timed_out=self.result.timed_out,
            )
        return self.result


def search_request(*, limit: int = 20) -> SearchRequest:
    return SearchRequest(
        platform="peertube",
        query='"security camera" kneeling',
        lang="en",
        query_pack_version="qp.v1.0.0",
        network_config="default",
        limit=limit,
        request_id="request-1",
        run_id="run-1",
    )


def probe_request(*, source_url: str = SOURCE_URL) -> ProbeRequest:
    return ProbeRequest(
        platform="peertube",
        source_id=VIDEO_UUID,
        candidate_key=make_candidate_key("peertube", VIDEO_UUID),
        source_url=source_url,
        network_config="default",
        request_id="request-2",
        run_id="run-1",
        timeout_seconds=12.0,
    )


def download_request(
    root: Path,
    output_dir: Path,
    *,
    source_url: str = SOURCE_URL,
) -> DownloadRequest:
    return DownloadRequest(
        platform="peertube",
        source_id=VIDEO_UUID,
        candidate_key=make_candidate_key("peertube", VIDEO_UUID),
        source_url=source_url,
        managed_root=root,
        output_dir=output_dir,
        network_config="default",
        request_id="request-3",
        run_id="run-1",
    )


class PeerTubeAdapterIntegrationTests(unittest.TestCase):
    def test_search_caps_at_twenty_and_filters_unapproved_instances(self) -> None:
        allowed = {
            "uuid": VIDEO_UUID,
            "url": RAW_SEARCH_URL,
            "name": "Raw security camera kneeling footage",
            "duration": 48,
            "account": {"displayName": "Town archive"},
            "channel": {"displayName": "Street cameras"},
        }
        unknown = {
            "uuid": SECOND_UUID,
            "url": f"https://unreviewed.example.net/w/{SECOND_UUID}",
            "name": "Unknown federation host",
        }
        fake = FakeHttpClient(
            [
                HttpJsonResponse(
                    status_code=200,
                    payload={"total": 3, "data": [unknown, allowed, dict(allowed)]},
                    final_url="https://sepiasearch.org/api/v1/search/videos?search=x",
                )
            ]
        )
        adapter = PeerTubeAdapter(
            allowed_instance_hosts=[INSTANCE],
            http_client=fake,
            runner=FakeRunner(CommandResult(returncode=0)),
        )

        hits = adapter.search(search_request())

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].candidate_key, f"peertube:{VIDEO_UUID}")
        self.assertEqual(hits[0].source_url, SOURCE_URL)
        # Preserve the platform rank although an earlier unknown host was filtered.
        self.assertEqual(hits[0].position, 2)
        self.assertEqual(hits[0].title, "Raw security camera kneeling footage")
        self.assertEqual(hits[0].uploader, "Town archive")
        self.assertEqual(hits[0].duration_seconds, 48)
        self.assertEqual(
            fake.calls[0]["url"],
            "https://sepiasearch.org/api/v1/search/videos",
        )
        self.assertEqual(fake.calls[0]["params"]["count"], 20)
        self.assertEqual(
            fake.calls[0]["allowed_hosts"], frozenset({"sepiasearch.org"})
        )

    def test_search_does_not_contact_federated_result_hosts(self) -> None:
        fake = FakeHttpClient(
            [
                HttpJsonResponse(
                    status_code=200,
                    payload={
                        "data": [
                            {
                                "uuid": VIDEO_UUID,
                                "url": f"https://unknown.example.net/w/{VIDEO_UUID}",
                            }
                        ]
                    },
                    final_url="https://sepiasearch.org/api/v1/search/videos",
                )
            ]
        )
        adapter = PeerTubeAdapter(http_client=fake)

        self.assertEqual(adapter.search(search_request()), [])
        self.assertEqual(len(fake.calls), 1)

    def test_search_never_releases_more_than_twenty_results(self) -> None:
        data = [
            {
                "uuid": f"123e4567-e89b-42d3-a456-{index:012d}",
                "url": f"https://{INSTANCE}/w/short-id-{index}",
            }
            for index in range(25)
        ]
        fake = FakeHttpClient(
            [
                HttpJsonResponse(
                    status_code=200,
                    payload={"data": data},
                    final_url="https://sepiasearch.org/api/v1/search/videos",
                )
            ]
        )
        adapter = PeerTubeAdapter(allowed_instance_hosts=[INSTANCE], http_client=fake)

        hits = adapter.search(search_request(limit=20))

        self.assertEqual(len(hits), 20)
        self.assertEqual(hits[-1].position, 20)

    def test_search_rejects_redirect_outside_search_host(self) -> None:
        fake = FakeHttpClient(
            [
                HttpJsonResponse(
                    status_code=200,
                    payload={"data": []},
                    final_url="https://redirected.example.net/api/v1/search/videos",
                )
            ]
        )
        adapter = PeerTubeAdapter(allowed_instance_hosts=[INSTANCE], http_client=fake)

        with self.assertRaises(AdapterError) as captured:
            adapter.search(search_request())

        self.assertIs(captured.exception.kind, AdapterErrorKind.UNSUPPORTED)

    def test_probe_normalizes_description_identity_and_media_shape(self) -> None:
        payload = {
            "uuid": VIDEO_UUID,
            # PeerTube may report a short route, but the adapter emits the
            # identity-bound full-UUID canonical watch route.
            "url": RAW_SEARCH_URL,
            "name": "Uncut surveillance recording",
            "description": "Original description\nkept verbatim.",
            "tags": ["security camera", {"name": "kneeling"}, "kneeling"],
            "duration": 91,
            "publishedAt": "2026-08-20T10:30:00.000Z",
            "account": {"id": 17, "name": "archive", "displayName": "Archive"},
            "videoChannel": {"id": 9, "displayName": "Public cameras"},
            "privacy": {"id": 1, "label": "Public"},
            "state": {"id": 1, "label": "Published"},
            "files": [
                {"size": 10_000, "width": 640, "resolution": {"id": 360}},
                {"size": 30_000, "width": 1920, "resolution": {"id": 1080}},
            ],
        }
        fake = FakeHttpClient(
            [
                HttpJsonResponse(
                    status_code=200,
                    payload=payload,
                    final_url=f"https://{INSTANCE}/api/v1/videos/{VIDEO_UUID}",
                )
            ]
        )
        adapter = PeerTubeAdapter(allowed_instance_hosts=[INSTANCE], http_client=fake)

        result = adapter.probe(probe_request())

        self.assertEqual(
            result.video_description, "Original description\nkept verbatim."
        )
        self.assertEqual(result.tags, ("security camera", "kneeling"))
        self.assertEqual(result.uploader, "Archive")
        self.assertEqual(result.uploader_id, "17")
        self.assertEqual(result.channel, "Public cameras")
        self.assertEqual(result.duration_seconds, 91)
        self.assertEqual(result.availability, "public")
        self.assertEqual(result.filesize_approx, 30_000)
        self.assertEqual(result.width, 1920)
        self.assertEqual(result.height, 1080)
        self.assertEqual(result.canonical_url, SOURCE_URL)
        self.assertEqual(fake.calls[0]["allowed_hosts"], frozenset({INSTANCE}))

    def test_probe_rejects_unknown_instance_before_http(self) -> None:
        fake = FakeHttpClient([])
        adapter = PeerTubeAdapter(
            allowed_instance_hosts=["other.example.org"], http_client=fake
        )

        with self.assertRaises(ValueError):
            adapter.probe(probe_request())

        self.assertEqual(fake.calls, [])

    def test_probe_rejects_same_host_wrong_video_before_http(self) -> None:
        fake = FakeHttpClient([])
        adapter = PeerTubeAdapter(allowed_instance_hosts=[INSTANCE], http_client=fake)

        with self.assertRaises(ValueError):
            adapter.probe(probe_request(source_url=WRONG_SOURCE_URL))

        self.assertEqual(fake.calls, [])

    def test_search_rejects_wrong_adapter_platform_before_http(self) -> None:
        fake = FakeHttpClient([])
        adapter = PeerTubeAdapter(allowed_instance_hosts=[INSTANCE], http_client=fake)
        request = SearchRequest(
            platform="youtube",
            query="security camera kneeling",
            lang="en",
            query_pack_version="qp.v1.0.0",
            network_config="default",
            limit=20,
            request_id="request-wrong-platform",
            run_id="run-1",
        )

        with self.assertRaises(ValueError):
            adapter.search(request)

        self.assertEqual(fake.calls, [])

    def test_constructor_rejects_private_or_non_https_endpoints(self) -> None:
        with self.assertRaises(ValueError):
            PeerTubeAdapter(
                search_endpoint="http://sepiasearch.org/api/v1/search/videos"
            )
        with self.assertRaises(ValueError):
            PeerTubeAdapter(search_endpoint="https://127.0.0.1/api/v1/search/videos")
        with self.assertRaises(ValueError):
            PeerTubeAdapter(allowed_instance_hosts=["localhost"])

    def test_http_failure_is_classified_without_network(self) -> None:
        adapter = PeerTubeAdapter(
            allowed_instance_hosts=[INSTANCE],
            http_client=FakeHttpClient(
                [HttpClientFailure("rate limited", status_code=429)]
            ),
        )

        with self.assertRaises(AdapterError) as captured:
            adapter.search(search_request())

        self.assertIs(captured.exception.kind, AdapterErrorKind.RATE_LIMITED)

    def test_download_is_single_item_managed_and_resource_limited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "managed"
            root.mkdir()
            output_dir = root / "attempt-1"
            runner = FakeRunner(CommandResult(returncode=0), file_bytes=b"video-bytes")
            adapter = PeerTubeAdapter(allowed_instance_hosts=[INSTANCE], runner=runner)

            result = adapter.download(download_request(root, output_dir))

            self.assertTrue(result.success)
            self.assertIsNotNone(result.file_path)
            assert result.file_path is not None
            self.assertEqual(result.file_path.read_bytes(), b"video-bytes")
            self.assertEqual(result.bytes_downloaded, len(b"video-bytes"))
            args = runner.calls[0]["args"]
            self.assertEqual(args.count(SOURCE_URL), 1)
            self.assertIn("--no-playlist", args)
            self.assertIn("--no-plugin-dirs", args)
            self.assertIn("--no-write-subs", args)
            self.assertIn("--no-write-auto-subs", args)
            self.assertEqual(
                args[args.index("--max-filesize") + 1], str(2 * 1024**3)
            )
            self.assertIn("height<=1080", args[args.index("--format") + 1])
            self.assertNotIn("android", " ".join(args).lower())
            self.assertEqual(runner.calls[0]["cwd"], output_dir.resolve())

    def test_download_rejects_output_escape_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "managed"
            root.mkdir()
            runner = FakeRunner(CommandResult(returncode=0), file_bytes=b"video")
            adapter = PeerTubeAdapter(allowed_instance_hosts=[INSTANCE], runner=runner)

            with self.assertRaises(ValueError):
                adapter.download(download_request(root, base / "outside"))

            self.assertEqual(runner.calls, [])

    def test_download_rejects_same_host_wrong_video_before_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "managed"
            root.mkdir()
            runner = FakeRunner(CommandResult(returncode=0), file_bytes=b"video")
            adapter = PeerTubeAdapter(allowed_instance_hosts=[INSTANCE], runner=runner)

            with self.assertRaises(ValueError):
                adapter.download(
                    download_request(
                        root,
                        root / "attempt",
                        source_url=WRONG_SOURCE_URL,
                    )
                )

            self.assertEqual(runner.calls, [])

    def test_download_maps_timeout_and_does_not_claim_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "managed"
            root.mkdir()
            runner = FakeRunner(CommandResult(returncode=-1, timed_out=True))
            adapter = PeerTubeAdapter(allowed_instance_hosts=[INSTANCE], runner=runner)

            result = adapter.download(download_request(root, root / "attempt"))

            self.assertFalse(result.success)
            self.assertIs(result.error_kind, AdapterErrorKind.TIMEOUT)
            self.assertIsNone(result.file_path)


if __name__ == "__main__":
    unittest.main()
