from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.semantic_queries import (
    SEMANTIC_QUERY_TEMPLATE_VERSION,
    SemanticQueryVectorService,
    build_semantic_query_specs,
)
from surveillance_video_agent.vector_index import QdrantVectorIndex


ROOT = Path(__file__).resolve().parents[2]
DEMAND_PACK = (
    ROOT
    / "query-packs/demand_action_v1/demand_action_v1.qp.v1.0.0.draft.json"
)
FIGHT_PACK = (
    ROOT
    / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json"
)


class FakeQueryProvider:
    provider_id = "test-query"
    model_id = "test-query-v1"
    dimensions = 4

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def embed_queries(self, texts, *, instruct):
        self.calls.append((tuple(texts), instruct))
        if self.fail:
            raise RuntimeError("simulated provider failure")
        vectors = []
        for index, _ in enumerate(texts):
            vector = [0.0] * self.dimensions
            vector[index % self.dimensions] = 1.0
            vectors.append(vector)
        return vectors


class SemanticQueryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.database = CandidateDatabase(root / "candidates.sqlite3")
        self.database.initialize()
        self.database.create_run("semantic-run", "semantic-query-test")
        self.schema = EmbeddingSchema(
            version="test-semantic-query-v1",
            provider="test-query",
            model="test-query-v1",
            dimensions=4,
        )
        self.index = QdrantVectorIndex(root / "qdrant")

    def tearDown(self) -> None:
        self.index.close()
        self.database.close()
        self.temporary.cleanup()

    def test_query_specs_are_deterministic_multilingual_and_source_neutral(self) -> None:
        fight = build_semantic_query_specs(FIGHT_PACK, self.schema)
        demand = build_semantic_query_specs(DEMAND_PACK, self.schema)
        self.assertEqual(len(fight), 4)
        self.assertEqual(len(demand), 3)
        self.assertEqual(
            [spec.subtype for spec in fight],
            ["冲突但未攻击", "舞蹈/玩闹/训练", "非攻击性身体接触", "场景先验"],
        )
        first = fight[0]
        self.assertIn("target_definition_zh:", first.query_text)
        self.assertIn("action_terms_en: heated argument", first.query_text)
        self.assertIn("action_terms_es:", first.query_text)
        self.assertIn("action_terms_fr:", first.query_text)
        self.assertNotIn("source_anchor", first.query_text)
        self.assertEqual(fight, build_semantic_query_specs(FIGHT_PACK, self.schema))
        self.assertEqual(len({spec.input_hash for spec in fight + demand}), 7)

    def test_prepare_calls_provider_once_then_reuses_qdrant_vectors(self) -> None:
        provider = FakeQueryProvider()
        service = SemanticQueryVectorService(
            self.database,
            self.index,
            provider,
            self.schema,
        )
        first = service.prepare(run_id="semantic-run", query_pack_path=FIGHT_PACK)
        self.assertEqual(first.generated_count, 4)
        self.assertEqual(first.cached_count, 0)
        self.assertEqual(set(first.vectors), set(spec.subtype for spec in build_semantic_query_specs(FIGHT_PACK, self.schema)))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(provider.calls[0][0]), 4)
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM subtype_semantic_queries WHERE index_status = 'ready'"
            ).fetchone()[0],
            4,
        )
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM embedding_calls WHERE status = 'succeeded'"
            ).fetchone()[0],
            1,
        )

        second = service.prepare(run_id="semantic-run", query_pack_path=FIGHT_PACK)
        self.assertEqual(second.generated_count, 0)
        self.assertEqual(second.cached_count, 4)
        self.assertEqual(first.vectors, second.vectors)
        self.assertEqual(len(provider.calls), 1)

    def test_provider_failure_is_audited_and_can_be_recovered(self) -> None:
        failing = FakeQueryProvider(fail=True)
        service = SemanticQueryVectorService(
            self.database,
            self.index,
            failing,
            self.schema,
        )
        with self.assertRaises(RuntimeError):
            service.prepare(run_id="semantic-run", query_pack_path=DEMAND_PACK)
        failed_rows = self.database.connection.execute(
            "SELECT DISTINCT index_status, error_kind FROM subtype_semantic_queries"
        ).fetchall()
        self.assertEqual([tuple(row) for row in failed_rows], [("failed", "provider_error")])
        audit = self.database.connection.execute(
            "SELECT status, error_kind, subject_count FROM embedding_calls"
        ).fetchone()
        self.assertEqual(tuple(audit), ("failed", "provider_error", 3))

        healthy = FakeQueryProvider()
        recovered = SemanticQueryVectorService(
            self.database,
            self.index,
            healthy,
            self.schema,
        ).prepare(run_id="semantic-run", query_pack_path=DEMAND_PACK)
        self.assertEqual(recovered.generated_count, 3)
        self.assertEqual(
            self.database.connection.execute(
                "SELECT COUNT(*) FROM subtype_semantic_queries WHERE index_status = 'ready'"
            ).fetchone()[0],
            3,
        )

    def test_template_and_embedding_call_audits_are_immutable(self) -> None:
        provider = FakeQueryProvider()
        SemanticQueryVectorService(
            self.database,
            self.index,
            provider,
            self.schema,
        ).prepare(run_id="semantic-run", query_pack_path=DEMAND_PACK)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.connection.execute(
                """
                UPDATE semantic_query_templates SET status = 'frozen'
                WHERE template_version = ?
                """,
                (SEMANTIC_QUERY_TEMPLATE_VERSION,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.connection.execute("DELETE FROM embedding_calls")


if __name__ == "__main__":
    unittest.main()
