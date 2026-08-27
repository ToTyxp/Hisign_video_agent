from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from surveillance_video_agent.contracts import ProbeResult
from surveillance_video_agent.batch_generator import (
    CampaignPolicy,
    FrontierPolicy,
    generate_secondary_batch,
)
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.dedupe import DedupePolicy, refresh_vector_duplicate_clusters
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.frontier import refresh_frontier
from surveillance_video_agent.projection import VectorProjectionService
from surveillance_video_agent.scoring import (
    CandidateMetadata,
    load_scoring_bundle,
    score_all_tasks,
    score_source,
)
from surveillance_video_agent.vector_index import QdrantVectorIndex, point_id_for
from surveillance_video_agent.resources import evaluate_probe_resources


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


class FakeEmbeddingProvider:
    provider_id = "test"
    model_id = "keyword-v1"
    dimensions = 4

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.casefold()
            if "hugging" in lowered:
                vectors.append([0.0, 1.0, 0.0, 0.0])
            elif "argument" in lowered:
                vectors.append([1.0, 0.0, 0.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0, 0.0])
        return vectors


class VectorFrontierIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.database = CandidateDatabase(root / "candidates.sqlite3")
        self.database.initialize()
        self.database.create_run("run-vector", "vector-integration")
        self.database.register_frozen_query_pack(FIGHT_PACK)
        self.scoring = load_scoring_bundle(SCORING_POLICY, QUERY_PACKS)
        self.schema = EmbeddingSchema(
            version="test-embedding-v1",
            provider="test",
            model="keyword-v1",
            dimensions=4,
        )
        self.qdrant_path = root / "qdrant"
        self.index = QdrantVectorIndex(self.qdrant_path)
        self.projection = VectorProjectionService(
            self.database,
            self.index,
            FakeEmbeddingProvider(),
            self.schema,
        )

    def tearDown(self) -> None:
        self.index.close()
        self.database.close()
        self.temporary.cleanup()

    def test_projection_dedupe_and_frontier_end_to_end(self) -> None:
        first = self._insert_scored_candidate(
            "aaaaaaaaaaa",
            "CCTV heated argument at apartment entrance",
            30,
            "fcv1-conflict-no-attack-en-01",
            1,
        )
        second = self._insert_scored_candidate(
            "bbbbbbbbbbb",
            "CCTV heated argument at apartment entrance duplicate",
            31,
            "fcv1-conflict-no-attack-en-01",
            2,
        )
        third = self._insert_scored_candidate(
            "ccccccccccc",
            "CCTV people hugging",
            50,
            "fcv1-contact-en-01",
            1,
        )
        keys = (first, second, third)

        event_ids = [self.projection.enqueue_candidate(key) for key in keys]
        self.assertTrue(all(event_ids))
        results = self.projection.process_all()
        self.assertEqual(len(results), 3)
        self.assertTrue(all(item.status == "completed" for item in results))
        self.assertEqual(
            self.database.connection.execute(
                """
                SELECT COUNT(*) FROM embedding_calls
                WHERE operation = 'candidate_documents' AND status = 'succeeded'
                """
            ).fetchone()[0],
            3,
        )
        self.assertIsNone(self.projection.enqueue_candidate(first))

        ready = self.database.connection.execute(
            "SELECT candidate_key, vector_name, index_status FROM candidate_embeddings"
        ).fetchall()
        self.assertEqual(len(ready), 6)
        self.assertTrue(all(row["index_status"] == "ready" for row in ready))
        self.assertEqual(
            self.database.connection.execute(
                "SELECT qdrant_point_id FROM candidate_embeddings WHERE candidate_key = ? LIMIT 1",
                (first,),
            ).fetchone()[0],
            point_id_for(self.schema.version, first),
        )

        dedupe = refresh_vector_duplicate_clusters(
            self.database,
            self.index,
            self.schema,
            DedupePolicy(
                version="dedupe-v1",
                similarity_threshold=0.99,
                title_similarity_threshold=0.75,
                duration_tolerance_seconds=2,
                neighbor_limit=5,
            ),
            run_id="run-vector",
            campaign_id="fight_confounder_v1",
        )
        self.assertEqual(dedupe.edge_count, 1)
        self.assertEqual(dedupe.cluster_count, 1)
        members = self.database.connection.execute(
            "SELECT candidate_key, member_status FROM duplicate_cluster_members ORDER BY candidate_key"
        ).fetchall()
        self.assertEqual(
            [(row["candidate_key"], row["member_status"]) for row in members],
            [(first, "ready"), (second, "ready")],
        )

        frontier = refresh_frontier(
            self.database,
            run_id="run-vector",
            campaign_id="fight_confounder_v1",
            query_pack_version="fight_confounder_v1.qp.v1.0.0",
            frontier_policy_version="frontier-v1",
            embedding_schema_version=self.schema.version,
            dedupe_policy_version="dedupe-v1",
        )
        self.assertEqual(frontier.ready_count, 3)
        rows = self.database.connection.execute(
            """
            SELECT candidate_key, subtype, attributed_query_id, status,
                   frontier_policy_version, embedding_schema_version,
                   dedupe_policy_version
            FROM frontier_entries ORDER BY candidate_key
            """
        ).fetchall()
        self.assertEqual(
            [(row["candidate_key"], row["subtype"]) for row in rows],
            [
                (first, "冲突但未攻击"),
                (second, "冲突但未攻击"),
                (third, "非攻击性身体接触"),
            ],
        )
        self.assertTrue(all(row["status"] == "ready" for row in rows))
        self.assertTrue(all(row["frontier_policy_version"] == "frontier-v1" for row in rows))
        self.assertTrue(all(row["embedding_schema_version"] == self.schema.version for row in rows))
        self.assertTrue(all(row["dedupe_policy_version"] == "dedupe-v1" for row in rows))

        batch = generate_secondary_batch(
            self.database,
            self.index,
            self.schema,
            CampaignPolicy(
                campaign_id="fight_confounder_v1",
                version="campaign-v1",
                subtype_limits=(("冲突但未攻击", 2), ("非攻击性身体接触", 1)),
                max_candidates=3,
            ),
            FrontierPolicy(
                version="frontier-v1",
                batch_size=3,
                vector_oversample_factor=3,
                semantic_score_threshold=0.99,
                uploader_cap=5,
            ),
            run_id="run-vector",
            dedupe_policy_version="dedupe-v1",
            query_vectors={
                "冲突但未攻击": [1.0, 0.0, 0.0, 0.0],
                "非攻击性身体接触": [0.0, 1.0, 0.0, 0.0],
            },
        )
        self.assertEqual(len(batch.items), 2)
        self.assertEqual(
            [item.subtype for item in batch.items],
            ["冲突但未攻击", "非攻击性身体接触"],
        )
        self.assertEqual(len({item.candidate_key for item in batch.items}), 2)
        decisions = self.database.connection.execute(
            "SELECT decision FROM secondary_filter_decisions WHERE batch_id = ?",
            (batch.batch_id,),
        ).fetchall()
        self.assertEqual([row["decision"] for row in decisions], ["download_eligible"] * 2)
        with self.assertRaises(ValueError):
            generate_secondary_batch(
                self.database,
                self.index,
                self.schema,
                CampaignPolicy(
                    campaign_id="fight_confounder_v1",
                    version="campaign-v1",
                    subtype_limits=(("冲突但未攻击", 2), ("非攻击性身体接触", 1)),
                    max_candidates=3,
                ),
                FrontierPolicy(
                    version="frontier-v1",
                    batch_size=3,
                    vector_oversample_factor=3,
                    semantic_score_threshold=0.99,
                    uploader_cap=5,
                ),
                run_id="run-vector",
                dedupe_policy_version="dedupe-v1",
                query_vectors={
                    "冲突但未攻击": [1.0, 0.0, 0.0, 0.0],
                    "非攻击性身体接触": [0.0, 1.0, 0.0, 0.0],
                },
            )

    def test_frontier_requires_completed_dedupe_refresh(self) -> None:
        candidate_key = self._insert_scored_candidate(
            "ddddddddddd",
            "CCTV heated argument",
            20,
            "fcv1-conflict-no-attack-en-01",
            1,
        )
        self.projection.enqueue_candidate(candidate_key)
        self.projection.process_all()
        with self.assertRaises(ValueError):
            refresh_frontier(
                self.database,
                run_id="run-vector",
                campaign_id="fight_confounder_v1",
                query_pack_version="fight_confounder_v1.qp.v1.0.0",
                frontier_policy_version="frontier-v1",
                embedding_schema_version=self.schema.version,
                dedupe_policy_version="not-refreshed",
            )

    def test_below_threshold_candidate_is_audited_and_not_leased(self) -> None:
        candidate_key = self._insert_scored_candidate(
            "sssssssssss",
            "CCTV verbal confrontation",
            20,
            "fcv1-conflict-no-attack-en-03",
            1,
        )
        self.projection.enqueue_candidate(candidate_key)
        self.projection.process_all()
        policy = DedupePolicy(
            version="dedupe-disabled-test",
            similarity_threshold=1.0,
            title_similarity_threshold=1.0,
            duration_tolerance_seconds=0,
            neighbor_limit=1,
            vector_enabled=False,
        )
        refresh_vector_duplicate_clusters(
            self.database,
            self.index,
            self.schema,
            policy,
            run_id="run-vector",
            campaign_id="fight_confounder_v1",
        )
        refresh_frontier(
            self.database,
            run_id="run-vector",
            campaign_id="fight_confounder_v1",
            query_pack_version="fight_confounder_v1.qp.v1.0.0",
            frontier_policy_version="frontier-below-v1",
            embedding_schema_version=self.schema.version,
            dedupe_policy_version=policy.version,
        )
        batch = generate_secondary_batch(
            self.database,
            self.index,
            self.schema,
            CampaignPolicy(
                campaign_id="fight_confounder_v1",
                version="campaign-below-v1",
                subtype_limits=(("冲突但未攻击", 1),),
                max_candidates=1,
            ),
            FrontierPolicy(
                version="frontier-below-v1",
                batch_size=1,
                vector_oversample_factor=1,
                semantic_score_threshold=0.99,
            ),
            run_id="run-vector",
            dedupe_policy_version=policy.version,
            query_vectors={"冲突但未攻击": [0.0, 0.0, 1.0, 0.0]},
            eligibility_query_vectors={
                "冲突但未攻击": [1.0, 0.0, 0.0, 0.0]
            },
        )
        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.yield_rate, 0.0)
        decision = self.database.connection.execute(
            """
            SELECT decision FROM secondary_filter_decisions
            WHERE batch_id = ? AND candidate_key = ?
            """,
            (batch.batch_id, candidate_key),
        ).fetchone()[0]
        self.assertEqual(decision, "below_semantic_threshold")
        frontier_status = self.database.connection.execute(
            """
            SELECT status FROM frontier_entries
            WHERE candidate_key = ? AND frontier_policy_version = 'frontier-below-v1'
            """,
            (candidate_key,),
        ).fetchone()[0]
        self.assertEqual(frontier_status, "consumed")

    def test_metadata_change_creates_new_projection_revision(self) -> None:
        candidate_key = self._insert_scored_candidate(
            "eeeeeeeeeee",
            "CCTV heated argument",
            20,
            "fcv1-conflict-no-attack-en-01",
            1,
        )
        self.projection.enqueue_candidate(candidate_key)
        self.projection.process_all()
        probe = self._probe(
            "eeeeeeeeeee",
            "CCTV heated argument updated",
            20,
        )
        self.database.insert_candidate(probe, run_id="run-vector")
        event_id = self.projection.enqueue_candidate(candidate_key)
        self.assertIsNotNone(event_id)
        result = self.projection.process_next()
        self.assertEqual(result.projection_revision, 2)
        revisions = self.database.connection.execute(
            "SELECT DISTINCT projection_revision FROM candidate_embeddings WHERE candidate_key = ?",
            (candidate_key,),
        ).fetchall()
        self.assertEqual([row[0] for row in revisions], [2])

    def test_projection_requires_task_qualification_and_recovers_interrupted_event(self) -> None:
        probe = self._probe("fffffffffff", "CCTV ordinary footage", 20)
        self.database.insert_candidate(probe, run_id="run-vector")
        self.database.record_resource_evaluation(
            evaluate_probe_resources(probe), run_id="run-vector"
        )
        candidate = CandidateMetadata.from_probe(probe)
        source = score_source(candidate, self.scoring)
        self.database.record_source_score(source, run_id="run-vector")
        with self.assertRaises(ValueError):
            self.projection.enqueue_candidate(probe.candidate_key)

        qualifying = self._insert_scored_candidate(
            "ggggggggggg",
            "CCTV heated argument",
            20,
            "fcv1-conflict-no-attack-en-01",
            1,
        )
        event_id = self.projection.enqueue_candidate(qualifying)
        self.database.connection.execute(
            "UPDATE vector_index_outbox SET status = 'processing' WHERE event_id = ?",
            (event_id,),
        )
        self.assertEqual(self.projection.recover_processing_events(), 1)
        result = self.projection.process_next()
        self.assertEqual(result.status, "completed")

    def test_local_qdrant_reopens_persisted_named_vectors(self) -> None:
        candidate_key = self._insert_scored_candidate(
            "hhhhhhhhhhh",
            "CCTV heated argument",
            20,
            "fcv1-conflict-no-attack-en-01",
            1,
        )
        self.projection.enqueue_candidate(candidate_key)
        self.projection.process_all()
        self.index.close()
        self.index = QdrantVectorIndex(self.qdrant_path)
        matches = self.index.query_relevance(
            self.schema,
            [1.0, 0.0, 0.0, 0.0],
            candidate_keys=[candidate_key],
            limit=1,
            score_threshold=0.99,
        )
        self.assertEqual([item.candidate_key for item in matches], [candidate_key])

    def _insert_scored_candidate(
        self,
        source_id: str,
        title: str,
        duration: float,
        query_id: str,
        position: int,
    ) -> str:
        probe = self._probe(source_id, title, duration)
        self.database.insert_candidate(probe, run_id="run-vector")
        self.database.record_resource_evaluation(
            evaluate_probe_resources(probe), run_id="run-vector"
        )
        candidate = CandidateMetadata.from_probe(probe)
        source = score_source(candidate, self.scoring)
        self.database.record_source_score(source, run_id="run-vector")
        for task in score_all_tasks(candidate, source, self.scoring):
            self.database.record_task_score(task, run_id="run-vector")
        self.database.connection.execute(
            """
            INSERT INTO candidate_discoveries(
                discovery_id, candidate_key, query_id, platform_position,
                discovered_at, run_id
            ) VALUES (?, ?, ?, ?, '2026-08-26T00:00:00Z', 'run-vector')
            """,
            (str(uuid.uuid4()), probe.candidate_key, query_id, position),
        )
        return probe.candidate_key

    def _probe(self, source_id: str, title: str, duration: float) -> ProbeResult:
        return ProbeResult(
            platform="youtube",
            source_id=source_id,
            candidate_key=f"youtube:{source_id}",
            source_url=f"https://www.youtube.com/watch?v={source_id}",
            canonical_url=f"https://www.youtube.com/watch?v={source_id}",
            title=title,
            video_description="security footage from a fixed camera",
            tags=("cctv",),
            uploader="Archive",
            channel="Camera Archive",
            duration_seconds=duration,
            availability="public",
            filesize_approx=1024,
            width=640,
            height=360,
            is_live=False,
            live_status="not_live",
        )


if __name__ == "__main__":
    unittest.main()
