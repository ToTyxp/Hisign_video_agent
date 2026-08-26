from pathlib import Path
import unittest

from surveillance_video_agent.contracts import (
    DEFAULT_MAX_FILESIZE_BYTES,
    DownloadRequest,
    SearchRequest,
    make_candidate_key,
)


class ContractTests(unittest.TestCase):
    def test_candidate_key_is_platform_and_source_id(self) -> None:
        self.assertEqual(make_candidate_key("youtube", "abc123def45"), "youtube:abc123def45")
        with self.assertRaises(ValueError):
            make_candidate_key("youtube", "contains:colon")

    def test_search_request_rejects_non_v1_cache_dimensions(self) -> None:
        with self.assertRaisesRegex(ValueError, "network_config"):
            SearchRequest(
                platform="youtube",
                query="security camera person kneeling",
                lang="en",
                query_pack_version="qp-v1",
                network_config="vpn",
                limit=20,
                request_id="request-1",
                run_id="run-1",
            )

    def test_search_limit_cannot_exceed_twenty(self) -> None:
        with self.assertRaisesRegex(ValueError, "limit"):
            SearchRequest(
                platform="youtube",
                query="security camera sit-in",
                lang="en",
                query_pack_version="qp-v1",
                network_config="default",
                limit=21,
                request_id="request-1",
                run_id="run-1",
            )

    def test_download_request_enforces_v1_resource_ceilings(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_height"):
            DownloadRequest(
                platform="youtube",
                source_id="abc123def45",
                candidate_key="youtube:abc123def45",
                source_url="https://www.youtube.com/watch?v=abc123def45",
                managed_root=Path("/tmp/managed"),
                output_dir=Path("/tmp/managed/run"),
                network_config="default",
                request_id="request-1",
                run_id="run-1",
                max_height=2160,
            )
        with self.assertRaisesRegex(ValueError, "max_filesize_bytes"):
            DownloadRequest(
                platform="youtube",
                source_id="abc123def45",
                candidate_key="youtube:abc123def45",
                source_url="https://www.youtube.com/watch?v=abc123def45",
                managed_root=Path("/tmp/managed"),
                output_dir=Path("/tmp/managed/run"),
                network_config="default",
                request_id="request-1",
                run_id="run-1",
                max_filesize_bytes=DEFAULT_MAX_FILESIZE_BYTES + 1,
            )

    def test_download_request_requires_absolute_managed_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute paths"):
            DownloadRequest(
                platform="youtube",
                source_id="abc123def45",
                candidate_key="youtube:abc123def45",
                source_url="https://www.youtube.com/watch?v=abc123def45",
                managed_root=Path("relative-managed"),
                output_dir=Path("relative-managed/run-1"),
                network_config="default",
                request_id="request-1",
                run_id="run-1",
            )


if __name__ == "__main__":
    unittest.main()
