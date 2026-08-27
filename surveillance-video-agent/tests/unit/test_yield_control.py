from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from surveillance_video_agent.contracts import ProbeResult
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.yield_control import record_batch_yield


ROOT = Path(__file__).resolve().parents[2]
FIGHT_PACK = (
    ROOT
    / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json"
)


class YieldControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = CandidateDatabase(Path(self.temporary.name) / "candidates.sqlite3")
        self.database.initialize()
        self.database.create_run("yield-run", "yield-test")
        self.database.register_frozen_query_pack(FIGHT_PACK)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def test_three_low_batches_stop_campaign_and_partition(self) -> None:
        evaluations = []
        for batch_number in range(3):
            batch_id = self._batch(batch_number, decisions=(False, False))
            evaluations.append(
                record_batch_yield(
                    self.database,
                    batch_id=batch_id,
                    low_yield_threshold=0.10,
                    low_yield_consecutive_windows=3,
                    partition_window_size=2,
                )
            )
        self.assertFalse(evaluations[1].campaign_stopped)
        self.assertTrue(evaluations[2].campaign_stopped)
        self.assertEqual(
            evaluations[2].suspended_partitions,
            (("fcv1-conflict-no-attack-en-01", "冲突但未攻击"),),
        )
        control = self.database.connection.execute(
            "SELECT status, stop_reason FROM campaign_run_control"
        ).fetchone()
        self.assertEqual(control["status"], "stopped")
        self.assertIsNotNone(control["stop_reason"])
        stats = self.database.connection.execute(
            "SELECT * FROM frontier_partition_stats"
        ).fetchone()
        self.assertEqual(stats["released_count"], 6)
        self.assertEqual(stats["consecutive_low_yield_windows"], 3)
        self.assertEqual(stats["status"], "suspended")

    def test_non_low_batch_resets_campaign_consecutive_count(self) -> None:
        for batch_number, decisions in enumerate(
            ((False, False), (True, False), (False, False))
        ):
            evaluation = record_batch_yield(
                self.database,
                batch_id=self._batch(batch_number, decisions=decisions),
                low_yield_threshold=0.10,
                low_yield_consecutive_windows=3,
                partition_window_size=20,
            )
        self.assertEqual(evaluation.campaign_consecutive_low_yield_batches, 1)
        self.assertFalse(evaluation.campaign_stopped)

    def _batch(self, batch_number: int, *, decisions) -> str:
        batch_id = f"batch-{batch_number}"
        self.database.connection.execute(
            """
            INSERT INTO secondary_batches(
                batch_id, run_id, campaign_id, campaign_policy_version,
                frontier_policy_version, status, requested_size,
                actual_size, created_at, completed_at
            ) VALUES (?, 'yield-run', 'fight_confounder_v1', 'campaign-v1',
                      'frontier-v1', 'completed', 20, ?, ?, ?)
            """,
            (
                batch_id,
                len(decisions),
                f"2026-08-26T00:00:0{batch_number}Z",
                f"2026-08-26T00:00:0{batch_number}Z",
            ),
        )
        for rank, eligible in enumerate(decisions, 1):
            source_id = f"{batch_number}{rank}aaaaaaaaa"[:11]
            probe = ProbeResult(
                platform="youtube",
                source_id=source_id,
                candidate_key=f"youtube:{source_id}",
                source_url=f"https://www.youtube.com/watch?v={source_id}",
                canonical_url=f"https://www.youtube.com/watch?v={source_id}",
            )
            self.database.insert_candidate(probe, run_id="yield-run")
            self.database.connection.execute(
                """
                INSERT INTO secondary_batch_items(
                    batch_id, candidate_key, campaign_id, subtype,
                    rank, vector_similarity, rrf_score, lease_id
                ) VALUES (?, ?, 'fight_confounder_v1', '冲突但未攻击',
                          ?, 0.5, 0.02, ?)
                """,
                (batch_id, probe.candidate_key, rank, str(uuid.uuid4())),
            )
            self.database.connection.execute(
                """
                INSERT INTO secondary_filter_decisions(
                    batch_id, candidate_key, decision, decided_campaign_id,
                    decided_subtype, vector_similarity, threshold,
                    reasons_json, decided_at
                ) VALUES (?, ?, ?, 'fight_confounder_v1', '冲突但未攻击',
                          0.5, 0.8, '{}', '2026-08-26T00:00:00Z')
                """,
                (
                    batch_id,
                    probe.candidate_key,
                    "download_eligible" if eligible else "below_semantic_threshold",
                ),
            )
            self.database.connection.execute(
                """
                INSERT INTO frontier_entries(
                    candidate_key, campaign_id, subtype, run_id, status,
                    task_score, source_score, platform, lang,
                    attributed_query_id, frontier_policy_version,
                    embedding_schema_version, dedupe_policy_version,
                    created_at, updated_at
                ) VALUES (?, 'fight_confounder_v1', '冲突但未攻击',
                          'yield-run', 'consumed', 4, 4, 'youtube', 'en',
                          'fcv1-conflict-no-attack-en-01', 'frontier-v1',
                          'embedding-v1', 'dedupe-v1',
                          '2026-08-26T00:00:00Z', '2026-08-26T00:00:00Z')
                """,
                (probe.candidate_key,),
            )
        return batch_id


if __name__ == "__main__":
    unittest.main()
