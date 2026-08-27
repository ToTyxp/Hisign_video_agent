from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from surveillance_video_agent.contracts import ProbeResult
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.resources import evaluate_probe_resources
from surveillance_video_agent.scoring import (
    CandidateMetadata,
    load_scoring_bundle,
    score_all_tasks,
    score_source,
)
from surveillance_video_agent.semantic_recall import CalibrationSemanticRecallService
from surveillance_video_agent.vector_index import QdrantVectorIndex


ROOT = Path(__file__).resolve().parents[2]
FIGHT_PACK = ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.1.0.json"
QUERY_PACKS = (
    ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.1.0.json",
    FIGHT_PACK,
)
POLICY = ROOT / "query-packs/scoring-policy.v1.1.0.json"


class FakeRecallProvider:
    provider_id = "recall-test"
    model_id = "recall-test-v1"
    dimensions = 4

    def __init__(self) -> None:
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class SemanticRecallIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = CandidateDatabase(self.root / "candidates.sqlite3")
        self.database.initialize()
        self.database.create_run("recall-run", "recall-test")
        self.database.register_frozen_query_pack(FIGHT_PACK)
        self.scoring = load_scoring_bundle(POLICY, QUERY_PACKS)
        self.schema = EmbeddingSchema(
            version="recall-schema-v1",
            provider="recall-test",
            model="recall-test-v1",
            dimensions=4,
        )
        self.index = QdrantVectorIndex(self.root / "qdrant")
        self.provider = FakeRecallProvider()
        self.service = CalibrationSemanticRecallService(
            self.database,
            self.index,
            self.provider,
            self.schema,
        )
        self.argument = self._candidate("aaaaaaaaaaa", "CCTV neighbors arguing")
        self.punching = self._candidate("bbbbbbbbbbb", "CCTV neighbors punching")

    def tearDown(self) -> None:
        self.index.close()
        self.database.close()
        self.temporary.cleanup()

    def test_isolated_index_is_cached_and_forbidden_pairs_are_not_exported(self) -> None:
        first = self.service.prepare_index(run_id="recall-run")
        self.assertEqual(first.eligible_count, 2)
        self.assertEqual(first.generated_count, 2)
        self.assertEqual(first.api_call_count, 1)
        second = self.service.prepare_index(run_id="recall-run")
        self.assertEqual(second.cached_count, 2)
        self.assertEqual(second.generated_count, 0)
        self.assertEqual(self.provider.calls, 1)
        output = self.root / "recall.jsonl"
        result = self.service.export_recall(
            run_id="recall-run",
            campaign_id="fight_confounder_v1",
            query_pack_version="fight_confounder_v1.qp.v1.1.0",
            query_vectors={
                "冲突但未攻击": [1.0, 0.0, 0.0, 0.0],
                "舞蹈/玩闹/训练": [0.0, 1.0, 0.0, 0.0],
            },
            scoring_policy_version="surveillance_scoring_v1.1.0",
            output_path=output,
            top_n_per_subtype=2,
        )
        self.assertEqual(result.subtype_counts["冲突但未攻击"], 1)
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        conflict = [row for row in rows if row["subtype"] == "冲突但未攻击"]
        self.assertEqual([row["candidate_key"] for row in conflict], [self.argument])
        self.assertTrue(all(row["calibration_only"] for row in rows))
        self.assertTrue(all(row["usable"] is None for row in rows))
        self.assertEqual(
            self.database.get_candidate(self.punching)["status"], "source_qualified"
        )
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM candidate_embeddings"
            ).fetchone()[0],
            0,
        )

    def test_user_threshold_override_promotes_only_allowed_pairs_without_state_change(self) -> None:
        self.service.prepare_index(run_id="recall-run")
        result = self.service.apply_threshold_override(
            run_id="recall-run",
            campaign_query_vectors={
                "fight_confounder_v1": (
                    "fight_confounder_v1.qp.v1.1.0",
                    {"冲突但未攻击": [1.0, 0.0, 0.0, 0.0]},
                )
            },
            scoring_policy_version="surveillance_scoring_v1.1.0",
            threshold=0.40,
            policy_version="user-threshold-test-v1",
        )
        self.assertEqual(result.pair_count, 1)
        self.assertEqual(result.unique_candidate_count, 1)
        self.assertEqual(result.promoted_relevance_count, 1)
        eligibility = self.database.connection.execute(
            "SELECT * FROM semantic_task_eligibility"
        ).fetchone()
        self.assertEqual(eligibility["candidate_key"], self.argument)
        self.assertGreater(eligibility["similarity"], 0.40)
        self.assertEqual(
            self.database.get_candidate(self.argument)["status"],
            "source_qualified",
        )
        self.assertEqual(
            self.database.get_candidate(self.punching)["status"],
            "source_qualified",
        )
        vectors = self.database.connection.execute(
            "SELECT candidate_key, vector_name FROM candidate_embeddings"
        ).fetchall()
        self.assertEqual(
            [(row["candidate_key"], row["vector_name"]) for row in vectors],
            [(self.argument, "relevance")],
        )
        repeated = self.service.apply_threshold_override(
            run_id="recall-run",
            campaign_query_vectors={
                "fight_confounder_v1": (
                    "fight_confounder_v1.qp.v1.1.0",
                    {"冲突但未攻击": [1.0, 0.0, 0.0, 0.0]},
                )
            },
            scoring_policy_version="surveillance_scoring_v1.1.0",
            threshold=0.40,
            policy_version="user-threshold-test-v1",
        )
        self.assertEqual(repeated.promoted_relevance_count, 0)
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM semantic_task_eligibility"
            ).fetchone()[0],
            1,
        )
        filtered = self.service.apply_threshold_override(
            run_id="recall-run",
            campaign_query_vectors={
                "fight_confounder_v1": (
                    "fight_confounder_v1.qp.v1.1.0",
                    {"冲突但未攻击": [1.0, 0.0, 0.0, 0.0]},
                )
            },
            scoring_policy_version="surveillance_scoring_v1.1.0",
            threshold=0.40,
            policy_version="source-policy-filter-test-v1",
            required_source_policy_version="different-source-policy-v1",
        )
        self.assertEqual(filtered.pair_count, 0)
        self.assertEqual(filtered.unique_candidate_count, 0)

    def _candidate(self, source_id: str, title: str) -> str:
        probe = ProbeResult(
            platform="youtube",
            source_id=source_id,
            candidate_key=f"youtube:{source_id}",
            source_url=f"https://www.youtube.com/watch?v={source_id}",
            canonical_url=f"https://www.youtube.com/watch?v={source_id}",
            title=title,
            video_description="security camera raw footage",
            duration_seconds=30,
            availability="public",
            filesize_approx=1024,
            is_live=False,
        )
        self.database.insert_candidate(probe, run_id="recall-run")
        self.database.record_resource_evaluation(
            evaluate_probe_resources(probe), run_id="recall-run"
        )
        metadata = CandidateMetadata.from_probe(probe)
        source = score_source(metadata, self.scoring)
        self.database.record_qualification(
            source,
            score_all_tasks(metadata, source, self.scoring),
            run_id="recall-run",
        )
        self.database.connection.execute(
            """
            INSERT INTO candidate_discoveries(
                discovery_id, candidate_key, query_id, platform_position,
                discovered_at, run_id
            ) VALUES (?, ?, 'fcv11-conflict-no-attack-en-01', 1,
                      '2026-08-26T00:00:00Z', 'recall-run')
            """,
            (str(uuid.uuid4()), probe.candidate_key),
        )
        return probe.candidate_key


if __name__ == "__main__":
    unittest.main()
