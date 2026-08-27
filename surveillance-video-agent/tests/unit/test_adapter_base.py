from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from surveillance_video_agent.adapters.base import (
    CommandResult,
    SubprocessCommandRunner,
    classify_command_error,
    ensure_child_path,
    ensure_managed_output_dir,
    sanitize_error_text,
    sanitize_metadata,
)
from surveillance_video_agent.contracts import AdapterErrorKind


class AdapterBaseTests(unittest.TestCase):
    @patch("surveillance_video_agent.adapters.base.subprocess.run")
    def test_subprocess_runner_disables_shell_and_plugins(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(["yt-dlp"], 0, "{}", "")
        result = SubprocessCommandRunner().run(
            ["yt-dlp", "--version"], timeout_seconds=1, env={"EXAMPLE": "yes"}
        )
        self.assertEqual(result.returncode, 0)
        kwargs = run_mock.call_args.kwargs
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["env"]["YTDLP_NO_PLUGINS"], "1")
        self.assertEqual(kwargs["env"]["EXAMPLE"], "yes")

    def test_sanitizers_remove_credentials_and_direct_media_urls(self) -> None:
        value = sanitize_metadata(
            {
                "http_headers": {"Authorization": "Bearer secret"},
                "formats": [{"url": "https://media.example/secret"}],
                "title": "ordinary title",
            }
        )
        self.assertEqual(value["http_headers"], "[REDACTED]")
        self.assertEqual(value["formats"][0]["url"], "[REDACTED]")
        self.assertEqual(value["title"], "ordinary title")
        cleaned = sanitize_error_text(
            "Authorization: Bearer-secret https://foo.googlevideo.com/path?sig=secret"
        )
        self.assertNotIn("Bearer-secret", cleaned)
        self.assertNotIn("googlevideo.com", cleaned)

    def test_http_403_and_503_are_transient_network_failures(self) -> None:
        for message in (
            "HTTP Error 403: Forbidden",
            "HTTP Error 503: Service Unavailable",
            "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol",
        ):
            self.assertEqual(
                classify_command_error(CommandResult(1, "", message)),
                AdapterErrorKind.NETWORK,
            )

    def test_output_directory_must_remain_under_managed_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "managed"
            output = root / "run-1"
            self.assertEqual(ensure_managed_output_dir(root, output), output.resolve())
            with self.assertRaisesRegex(ValueError, "escapes"):
                ensure_managed_output_dir(root, root.parent / "outside")

    def test_child_path_rejects_symlink_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            target = parent / "media.mp4"
            target.write_bytes(b"media")
            link = parent / "linked.mp4"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                ensure_child_path(parent, link)


if __name__ == "__main__":
    unittest.main()
