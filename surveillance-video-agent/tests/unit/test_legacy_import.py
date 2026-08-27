from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.legacy_import import import_legacy_state


class LegacyImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = CandidateDatabase(self.root / "candidates.sqlite3")
        self.database.initialize()
        self.history = self.root / "download-history.json"
        self.archive = self.root / "accepted.archive"
        self.cache = self.root / "cache"
        self.cache.mkdir()
        records = {
            "aaaaaaaaaaa": {"id": "aaaaaaaaaaa", "status": "accepted"},
            "bbbbbbbbbbb": {"id": "bbbbbbbbbbb", "status": "accepted"},
            "ccccccccccc": {"id": "ccccccccccc", "status": "downloaded"},
        }
        self.history.write_text(
            json.dumps({"version": 1, "records": records}), encoding="utf-8"
        )
        self.archive.write_text("aaaaaaaaaaa\nbbbbbbbbbbb\n", encoding="utf-8")
        for source_id in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
            (self.cache / f"{source_id}.info.json").write_text(
                json.dumps(
                    {
                        "id": source_id,
                        "uploader_id": "@trusted-archive",
                        "channel_id": "channel-id",
                    }
                ),
                encoding="utf-8",
            )

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def test_import_is_read_only_scoped_and_idempotent(self) -> None:
        before = self.history.read_bytes()
        result = import_legacy_state(
            self.database,
            history_path=self.history,
            info_cache_dir=self.cache,
            accepted_archive_path=self.archive,
        )
        self.assertEqual(result.imported_download_count, 3)
        self.assertEqual(result.accepted_count, 2)
        self.assertEqual(result.downloaded_only_count, 1)
        self.assertEqual(result.imported_uploader_prior_count, 1)
        self.assertEqual(self.history.read_bytes(), before)
        statuses = self.database.connection.execute(
            "SELECT youtube_id, legacy_status FROM legacy_downloads ORDER BY youtube_id"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in statuses],
            [
                ("aaaaaaaaaaa", "accepted"),
                ("bbbbbbbbbbb", "accepted"),
                ("ccccccccccc", "downloaded"),
            ],
        )
        prior = self.database.connection.execute(
            "SELECT completed_count, prior_points, provenance_json FROM uploader_priors"
        ).fetchone()
        self.assertEqual((prior["completed_count"], prior["prior_points"]), (2, 2))
        provenance = json.loads(prior["provenance_json"])
        self.assertFalse(provenance["rejection_reasons_migrated"])
        self.assertFalse(provenance["channel_ban_created"])
        self.assertEqual(
            self.database.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],
            0,
        )

        repeated = import_legacy_state(
            self.database,
            history_path=self.history,
            info_cache_dir=self.cache,
            accepted_archive_path=self.archive,
        )
        self.assertEqual(repeated, result)
        self.assertEqual(
            self.database.connection.execute("SELECT COUNT(*) FROM legacy_imports").fetchone()[0],
            1,
        )

    def test_archive_mismatch_stops_without_partial_import(self) -> None:
        self.archive.write_text("aaaaaaaaaaa\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_legacy_state(
                self.database,
                history_path=self.history,
                info_cache_dir=self.cache,
                accepted_archive_path=self.archive,
            )
        self.assertEqual(
            self.database.connection.execute("SELECT COUNT(*) FROM legacy_downloads").fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
