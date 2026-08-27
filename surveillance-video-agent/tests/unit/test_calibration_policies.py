from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from surveillance_video_agent.calibration import (
    CalibrationExample,
    calibrate_relevance_thresholds,
    store_calibration_result,
)
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.policies import (
    bootstrap_default_campaign_policies,
    bootstrap_safe_dedupe_policy,
    get_campaign_policy,
    get_frontier_policy,
    update_campaign_policy,
    update_frontier_policy,
)


class CalibrationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = CandidateDatabase(Path(self.temporary.name) / "candidates.sqlite3")
        self.database.initialize()
        self.database.create_run("calibration-run", "calibration-test")
        self.schema = EmbeddingSchema(
            version="calibration-schema-v1",
            provider="test",
            model="test-model",
            dimensions=4,
        )
        bootstrap_default_campaign_policies(self.database)
        self.dedupe = bootstrap_safe_dedupe_policy(self.database, self.schema)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def examples(self, campaign_id: str, subtypes) -> tuple[CalibrationExample, ...]:
        result = []
        for subtype_index, subtype in enumerate(subtypes):
            for uploader in range(6):
                for item in range(5):
                    usable = item < 3
                    score = 0.82 + item * 0.02 if usable else 0.18 + item * 0.04
                    result.append(
                        CalibrationExample(
                            candidate_key=f"youtube:{subtype_index:02d}{uploader:02d}{item:02d}xxxxx",
                            campaign_id=campaign_id,
                            subtype=subtype,
                            uploader_identity=f"uploader-{subtype_index}-{uploader}",
                            platform=("youtube", "dailymotion", "peertube")[uploader % 3],
                            lang=("en", "es", "fr")[uploader % 3],
                            similarity=score,
                            usable=usable,
                        )
                    )
        return tuple(result)

    def test_grouped_calibration_passes_and_is_immutable(self) -> None:
        campaign = get_campaign_policy(self.database, "demand_action_v1")
        subtypes = [name for name, _ in campaign.subtype_limits]
        result = calibrate_relevance_thresholds(
            self.examples(campaign.campaign_id, subtypes),
            campaign_id=campaign.campaign_id,
            embedding_schema_version=self.schema.version,
            expected_subtypes=subtypes,
        )
        self.assertEqual(result.status, "passed")
        self.assertEqual(set(result.thresholds), set(subtypes))
        self.assertTrue(
            all(item.evaluation and item.evaluation.recall >= 0.9 for item in result.subtypes)
        )
        store_calibration_result(
            self.database,
            self.schema,
            result,
            run_id="calibration-run",
        )
        store_calibration_result(
            self.database,
            self.schema,
            result,
            run_id="calibration-run",
        )
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM threshold_calibrations"
            ).fetchone()[0],
            1,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.connection.execute("DELETE FROM threshold_calibrations")

    def test_one_insufficient_subtype_blocks_all_thresholds(self) -> None:
        campaign = get_campaign_policy(self.database, "demand_action_v1")
        subtypes = [name for name, _ in campaign.subtype_limits]
        examples = self.examples(campaign.campaign_id, subtypes[:-1])
        result = calibrate_relevance_thresholds(
            examples,
            campaign_id=campaign.campaign_id,
            embedding_schema_version=self.schema.version,
            expected_subtypes=subtypes,
        )
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(dict(result.thresholds), {})
        self.assertEqual(result.subtypes[-1].status, "insufficient")

    def test_capacity_update_is_versioned_and_checks_expected_version(self) -> None:
        current = get_campaign_policy(self.database, "demand_action_v1")
        updated = update_campaign_policy(
            self.database,
            campaign_id=current.campaign_id,
            expected_version=current.version,
            subtype_limits={"举牌/横幅": 10, "下跪": 10, "静坐": 10},
            max_candidates=30,
            reason="smaller pilot",
        )
        self.assertNotEqual(updated.version, current.version)
        self.assertEqual(get_campaign_policy(self.database, current.campaign_id), updated)
        with self.assertRaises(ValueError):
            update_campaign_policy(
                self.database,
                campaign_id=current.campaign_id,
                expected_version=current.version,
                subtype_limits={"举牌/横幅": 10, "下跪": 10, "静坐": 10},
                max_candidates=30,
                reason="stale write",
            )

    def test_frontier_policy_requires_passed_complete_calibration(self) -> None:
        campaign = get_campaign_policy(self.database, "fight_confounder_v1")
        subtypes = [name for name, _ in campaign.subtype_limits]
        with self.assertRaises(ValueError):
            update_frontier_policy(
                self.database,
                campaign_id=campaign.campaign_id,
                expected_version=None,
                calibration_id="missing",
                probe_budget=150,
                batch_size=20,
                vector_oversample_factor=5,
                embedding_schema_version=self.schema.version,
                rrf_k=60,
                dedupe_policy_version=self.dedupe.version,
                low_yield_threshold=0.10,
                low_yield_consecutive_windows=3,
                low_yield_partition_window_size=20,
                reason="must not bypass calibration",
            )
        calibration = calibrate_relevance_thresholds(
            self.examples(campaign.campaign_id, subtypes),
            campaign_id=campaign.campaign_id,
            embedding_schema_version=self.schema.version,
            expected_subtypes=subtypes,
        )
        store_calibration_result(
            self.database,
            self.schema,
            calibration,
            run_id="calibration-run",
        )
        created = update_frontier_policy(
            self.database,
            campaign_id=campaign.campaign_id,
            expected_version=None,
            calibration_id=calibration.calibration_id,
            probe_budget=150,
            batch_size=20,
            vector_oversample_factor=5,
            embedding_schema_version=self.schema.version,
            rrf_k=60,
            dedupe_policy_version=self.dedupe.version,
            low_yield_threshold=0.10,
            low_yield_consecutive_windows=3,
            low_yield_partition_window_size=20,
            reason="calibrated pilot frontier",
        )
        loaded = get_frontier_policy(self.database, campaign.campaign_id)
        self.assertEqual(created, loaded)
        self.assertEqual(
            set(dict(loaded.policy.semantic_score_thresholds)), set(subtypes)
        )
        self.assertEqual(loaded.calibration_id, calibration.calibration_id)


if __name__ == "__main__":
    unittest.main()
