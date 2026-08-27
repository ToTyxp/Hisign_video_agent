from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from surveillance_video_agent.calibration import (
    CalibrationExample,
    calibrate_relevance_thresholds,
    store_calibration_result,
)
from surveillance_video_agent.calibration_dataset import (
    load_labeled_calibration_dataset,
)
from surveillance_video_agent.contracts import ProbeResult
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.policies import (
    bootstrap_default_campaign_policies,
    bootstrap_safe_dedupe_policy,
    get_campaign_policy,
    update_frontier_policy,
)
from surveillance_video_agent.projection import VectorProjectionService
from surveillance_video_agent.resources import evaluate_probe_resources
from surveillance_video_agent.scoring import (
    CandidateMetadata,
    load_scoring_bundle,
    score_all_tasks,
    score_source,
)
from surveillance_video_agent.semantic_queries import SemanticQueryVectorService
from surveillance_video_agent.vector_index import QdrantVectorIndex
from surveillance_video_agent.workflow import (
    prepare_calibration_phase,
    run_calibrated_iteration,
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
SCORING_POLICY = ROOT / "query-packs/scoring-policy.v1.0.0.draft.json"


class FakeWorkflowEmbeddingProvider:
    provider_id = "workflow-test"
    model_id = "workflow-test-v1"
    dimensions = 4

    def embed(self, texts):
        return [self._vector(text) for text in texts]

    def embed_queries(self, texts, *, instruct):
        return [self._vector(text) for text in texts]

    def _vector(self, text):
        lowered = text.casefold()
        if "argument" in lowered or "冲突但未攻击" in text:
            return [1.0, 0.0, 0.0, 0.0]
        if "拥抱" in text:
            return [0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 1.0, 0.0]


class WorkflowIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = CandidateDatabase(self.root / "candidates.sqlite3")
        self.database.initialize()
        self.database.create_run("workflow-run", "workflow-test")
        self.database.register_frozen_query_pack(FIGHT_PACK)
        bootstrap_default_campaign_policies(self.database)
        self.schema = EmbeddingSchema(
            version="workflow-schema-v1",
            provider="workflow-test",
            model="workflow-test-v1",
            dimensions=4,
        )
        self.index = QdrantVectorIndex(self.root / "qdrant")
        self.provider = FakeWorkflowEmbeddingProvider()
        self.projection = VectorProjectionService(
            self.database,
            self.index,
            self.provider,
            self.schema,
        )
        self.semantic = SemanticQueryVectorService(
            self.database,
            self.index,
            self.provider,
            self.schema,
        )
        self.scoring = load_scoring_bundle(SCORING_POLICY, QUERY_PACKS)
        self.candidate_key = self._candidate()

    def tearDown(self) -> None:
        self.index.close()
        self.database.close()
        self.temporary.cleanup()

    def test_calibration_export_then_calibrated_batch_stops_before_download(self) -> None:
        calibration_path = self.root / "calibration/fight.jsonl"
        prepared = prepare_calibration_phase(
            self.database,
            self.index,
            self.projection,
            self.semantic,
            self.schema,
            run_id="workflow-run",
            campaign_id="fight_confounder_v1",
            query_pack_path=FIGHT_PACK,
            output_path=calibration_path,
        )
        self.assertEqual(prepared.projection_enqueued_count, 1)
        self.assertEqual(prepared.projection_processed_count, 1)
        self.assertEqual(prepared.calibration_export.record_count, 1)
        row = json.loads(calibration_path.read_text(encoding="utf-8"))
        self.assertIsNone(row["usable"])
        row["usable"] = True
        calibration_path.write_text(
            json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        labeled = load_labeled_calibration_dataset(calibration_path)
        self.assertEqual(len(labeled), 1)
        self.assertTrue(labeled[0].usable)

        campaign = get_campaign_policy(self.database, "fight_confounder_v1")
        subtypes = [name for name, _ in campaign.subtype_limits]
        calibration = calibrate_relevance_thresholds(
            self._synthetic_labels(subtypes),
            campaign_id=campaign.campaign_id,
            embedding_schema_version=self.schema.version,
            expected_subtypes=subtypes,
        )
        store_calibration_result(
            self.database,
            self.schema,
            calibration,
            run_id="workflow-run",
        )
        dedupe = bootstrap_safe_dedupe_policy(self.database, self.schema)
        update_frontier_policy(
            self.database,
            campaign_id=campaign.campaign_id,
            expected_version=None,
            calibration_id=calibration.calibration_id,
            probe_budget=150,
            batch_size=20,
            vector_oversample_factor=5,
            embedding_schema_version=self.schema.version,
            rrf_k=60,
            dedupe_policy_version=dedupe.version,
            low_yield_threshold=0.10,
            low_yield_consecutive_windows=3,
            low_yield_partition_window_size=20,
            reason="workflow integration calibrated policy",
        )
        batch_path = self.root / "runs/workflow-run/batch.jsonl"
        iteration = run_calibrated_iteration(
            self.database,
            self.index,
            self.semantic,
            self.schema,
            dedupe,
            run_id="workflow-run",
            campaign_id=campaign.campaign_id,
            query_pack_path=FIGHT_PACK,
            batch_output_path=batch_path,
            enable_downloads=False,
        )
        self.assertEqual(iteration.status, "awaiting_download_approval")
        self.assertEqual(iteration.queued_count, 0)
        self.assertTrue(batch_path.is_file())
        self.assertEqual(
            self.database.get_candidate(self.candidate_key)["status"],
            "source_qualified",
        )
        repeated = run_calibrated_iteration(
            self.database,
            self.index,
            self.semantic,
            self.schema,
            dedupe,
            run_id="workflow-run",
            campaign_id=campaign.campaign_id,
            query_pack_path=FIGHT_PACK,
            batch_output_path=batch_path,
            enable_downloads=False,
        )
        self.assertEqual(repeated.status, "awaiting_download_approval")
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM secondary_batches"
            ).fetchone()[0],
            1,
        )

    def test_campaign_hold_stops_before_frontier_or_download_work(self) -> None:
        self.database.connection.execute(
            """
            INSERT INTO campaign_hold_events(
                event_id, campaign_id, action, reason, run_id, created_at
            ) VALUES ('hold-1', 'fight_confounder_v1', 'hold',
                      'collect better data', 'workflow-run',
                      '2026-08-27T00:00:00Z')
            """
        )
        dedupe = bootstrap_safe_dedupe_policy(self.database, self.schema)
        result = run_calibrated_iteration(
            self.database,
            self.index,
            self.semantic,
            self.schema,
            dedupe,
            run_id="workflow-run",
            campaign_id="fight_confounder_v1",
            query_pack_path=FIGHT_PACK,
            batch_output_path=self.root / "held.jsonl",
            enable_downloads=False,
        )
        self.assertEqual(result.status, "stopped")
        self.assertIn("collect better data", result.reason)
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM secondary_batches"
            ).fetchone()[0],
            0,
        )

    def _candidate(self) -> str:
        probe = ProbeResult(
            platform="youtube",
            source_id="wwwwwwwwwww",
            candidate_key="youtube:wwwwwwwwwww",
            source_url="https://www.youtube.com/watch?v=wwwwwwwwwww",
            canonical_url="https://www.youtube.com/watch?v=wwwwwwwwwww",
            title="CCTV heated argument",
            video_description="security camera raw uncut footage",
            tags=("heated argument",),
            uploader="Archive",
            uploader_id="archive-uploader",
            channel="Camera Archive",
            duration_seconds=30,
            availability="public",
            filesize_approx=1024,
            width=640,
            height=360,
            is_live=False,
            live_status="not_live",
        )
        self.database.insert_candidate(probe, run_id="workflow-run")
        self.database.record_resource_evaluation(
            evaluate_probe_resources(probe), run_id="workflow-run"
        )
        metadata = CandidateMetadata.from_probe(probe)
        source = score_source(metadata, self.scoring)
        self.database.record_qualification(
            source,
            score_all_tasks(metadata, source, self.scoring),
            run_id="workflow-run",
        )
        self.database.connection.execute(
            """
            INSERT INTO candidate_discoveries(
                discovery_id, candidate_key, query_id, platform_position,
                discovered_at, run_id
            ) VALUES (?, ?, 'fcv1-conflict-no-attack-en-01', 1,
                      '2026-08-26T00:00:00Z', 'workflow-run')
            """,
            (str(uuid.uuid4()), probe.candidate_key),
        )
        return probe.candidate_key

    def _synthetic_labels(self, subtypes):
        examples = []
        for subtype_index, subtype in enumerate(subtypes):
            for uploader in range(6):
                for item in range(5):
                    usable = item < 3
                    examples.append(
                        CalibrationExample(
                            candidate_key=f"youtube:{subtype_index}{uploader}{item}labelsxxx",
                            campaign_id="fight_confounder_v1",
                            subtype=subtype,
                            uploader_identity=f"label-uploader-{subtype_index}-{uploader}",
                            platform=("youtube", "dailymotion", "peertube")[uploader % 3],
                            lang=("en", "es", "fr")[uploader % 3],
                            similarity=0.85 if usable else 0.20,
                            usable=usable,
                        )
                    )
        return tuple(examples)


if __name__ == "__main__":
    unittest.main()
