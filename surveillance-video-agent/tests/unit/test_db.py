from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from surveillance_video_agent.contracts import ProbeResult
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.scoring import (
    CandidateMetadata,
    load_scoring_bundle,
    score_source,
    score_task,
)


ROOT = Path(__file__).resolve().parents[2]
QUERY_PACKS = (
    ROOT
    / "query-packs/demand_action_v1/demand_action_v1.qp.v1.0.0.draft.json",
    ROOT
    / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json",
)
POLICY = ROOT / "query-packs/scoring-policy.v1.0.0.draft.json"


class CandidateDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db = CandidateDatabase(Path(self.temporary.name) / "candidates.sqlite3")
        self.db.initialize()
        self.db.create_run("run-1", "unit-test")
        self.policy = load_scoring_bundle(POLICY, QUERY_PACKS)

    def tearDown(self) -> None:
        self.db.close()
        self.temporary.cleanup()

    def probe(self, *, title: str = "CCTV kneeling protest") -> ProbeResult:
        return ProbeResult(
            platform="youtube",
            source_id="abcdefghijk",
            candidate_key="youtube:abcdefghijk",
            source_url="https://www.youtube.com/watch?v=abcdefghijk",
            canonical_url="https://www.youtube.com/watch?v=abcdefghijk",
            title=title,
            video_description="fixed camera raw footage",
            tags=("cctv",),
            uploader="Archive",
            channel="Camera Archive",
            duration_seconds=30,
            availability="public",
            filesize_approx=1024,
            width=640,
            height=360,
            is_live=False,
            live_status="not_live",
        )

    def insert_scored_candidate(self):
        probe = self.probe()
        self.db.insert_candidate(probe, run_id="run-1")
        candidate = CandidateMetadata.from_probe(probe)
        source = score_source(candidate, self.policy)
        self.db.record_source_score(source, run_id="run-1")
        task = score_task(
            candidate,
            source,
            self.policy,
            campaign_id="demand_action_v1",
            subtype="下跪",
        )
        self.db.record_task_score(task, run_id="run-1")
        return probe, source, task

    def test_schema_initializes_expected_control_plane_tables_and_pragmas(self) -> None:
        tables = {
            row["name"]
            for row in self.db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {
                "candidates",
                "search_cache",
                "probe_selections",
                "adapter_calls",
                "embedding_calls",
                "semantic_query_templates",
                "subtype_semantic_queries",
                "threshold_calibrations",
                "calibration_exports",
                "legacy_imports",
                "calibration_candidate_embeddings",
                "calibration_embedding_calls",
                "semantic_recall_exports",
                "score_evidence",
                "frontier_entries",
                "secondary_batches",
                "download_attempts",
                "media_objects",
                "state_transitions",
            }.issubset(tables)
        )
        self.assertEqual(self.db.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.db.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_frozen_query_packs_register_and_cannot_be_mutated(self) -> None:
        for path in QUERY_PACKS:
            self.db.register_frozen_query_pack(path)
        count = self.db.connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
        self.assertEqual(count, 63)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                "UPDATE queries SET query_text = 'changed' WHERE query_id = ?",
                ("dav1-sign-banner-en-01",),
            )

    def test_tampered_frozen_query_pack_is_rejected_by_content_hash(self) -> None:
        document = json.loads(QUERY_PACKS[0].read_text(encoding="utf-8"))
        document["queries"][0]["query"] = "tampered query"
        tampered = Path(self.temporary.name) / "tampered.json"
        tampered.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.db.register_frozen_query_pack(tampered)

    def test_candidate_insert_is_idempotent_without_resetting_state(self) -> None:
        probe, _, _ = self.insert_scored_candidate()
        self.assertEqual(self.db.get_candidate(probe.candidate_key)["status"], "source_qualified")
        updated = replace(probe, title="Updated CCTV title")
        self.db.insert_candidate(updated, run_id="run-1")
        row = self.db.get_candidate(probe.candidate_key)
        self.assertEqual(row["status"], "source_qualified")
        self.assertEqual(row["title"], "Updated CCTV title")

    def test_source_and_task_scores_write_structured_audit_evidence(self) -> None:
        probe, source, task = self.insert_scored_candidate()
        row = self.db.get_candidate(probe.candidate_key)
        self.assertEqual(row["status"], "source_qualified")
        self.assertEqual(row["source_score"], source.score)
        stored_task = self.db.connection.execute(
            "SELECT * FROM candidate_task_scores WHERE candidate_key = ? AND subtype = ?",
            (probe.candidate_key, "下跪"),
        ).fetchone()
        self.assertEqual(stored_task["score"], task.score)
        self.assertEqual(stored_task["qualified"], 1)
        evidence = self.db.connection.execute(
            "SELECT score_kind, rule_code FROM score_evidence WHERE candidate_key = ?",
            (probe.candidate_key,),
        ).fetchall()
        self.assertIn(("source", "source.title_strong_anchor"), [tuple(row) for row in evidence])
        self.assertIn(("task", "task.title_action"), [tuple(row) for row in evidence])
        transitions = self.db.connection.execute(
            "SELECT old_status, new_status FROM state_transitions WHERE candidate_key = ?",
            (probe.candidate_key,),
        ).fetchall()
        self.assertEqual([tuple(row) for row in transitions], [("discovered", "source_qualified")])

    def test_task_score_cannot_be_recorded_before_source_state(self) -> None:
        probe = self.probe()
        self.db.insert_candidate(probe, run_id="run-1")
        candidate = CandidateMetadata.from_probe(probe)
        source = score_source(candidate, self.policy)
        task = score_task(
            candidate,
            source,
            self.policy,
            campaign_id="demand_action_v1",
            subtype="下跪",
        )
        with self.assertRaises(ValueError):
            self.db.record_task_score(task, run_id="run-1")

    def test_atomic_qualification_rolls_back_source_when_a_task_write_fails(self) -> None:
        probe = self.probe()
        self.db.insert_candidate(probe, run_id="run-1")
        candidate = CandidateMetadata.from_probe(probe)
        source = score_source(candidate, self.policy)
        task = score_task(
            candidate,
            source,
            self.policy,
            campaign_id="demand_action_v1",
            subtype="下跪",
        )
        invalid_task = replace(task, score=7)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.record_qualification(source, (invalid_task,), run_id="run-1")
        row = self.db.get_candidate(probe.candidate_key)
        self.assertEqual(row["status"], "discovered")
        self.assertIsNone(row["source_policy_version"])
        self.assertEqual(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM state_transitions WHERE candidate_key = ?",
                (probe.candidate_key,),
            ).fetchone()[0],
            0,
        )

    def test_task_queue_transition_requires_batch_decision_and_assignment(self) -> None:
        probe, _, _ = self.insert_scored_candidate()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.transition_candidate(
                probe.candidate_key,
                "task_queued",
                reason="missing queue prerequisites",
                run_id="run-1",
            )
        self.assertEqual(self.db.get_candidate(probe.candidate_key)["status"], "source_qualified")

        self._insert_queue_prerequisites(probe.candidate_key)
        self.db.transition_candidate(
            probe.candidate_key,
            "task_queued",
            reason="secondary filter passed",
            run_id="run-1",
        )
        self.assertEqual(self.db.get_candidate(probe.candidate_key)["status"], "task_queued")

    def test_task_queue_accepts_versioned_semantic_eligibility_for_exact_assignment(self) -> None:
        probe = self.probe(title="CCTV ordinary fixed-camera footage")
        self.db.insert_candidate(probe, run_id="run-1")
        candidate = CandidateMetadata.from_probe(probe)
        source = score_source(candidate, self.policy)
        task = score_task(
            candidate,
            source,
            self.policy,
            campaign_id="demand_action_v1",
            subtype="下跪",
        )
        self.assertFalse(task.qualified)
        self.db.record_source_score(source, run_id="run-1")
        self.db.record_task_score(task, run_id="run-1")
        self.db.register_frozen_query_pack(QUERY_PACKS[0])
        schema = EmbeddingSchema(
            version="semantic-queue-test-v1",
            provider="test",
            model="test",
            dimensions=4,
        )
        self.db.register_embedding_schema(schema)
        self.db.connection.execute(
            """
            INSERT INTO semantic_task_eligibility(
                eligibility_id, candidate_key, campaign_id, subtype,
                query_pack_version, embedding_schema_version, policy_version,
                similarity, threshold, run_id, created_at
            ) VALUES ('eligibility-1', ?, 'demand_action_v1', '下跪',
                      'demand_action_v1.qp.v1.0.0', ?, 'threshold-v1',
                      0.6, 0.4, 'run-1', '2026-08-26T00:00:00Z')
            """,
            (probe.candidate_key, schema.version),
        )
        self._insert_queue_prerequisites(probe.candidate_key)
        self.db.transition_candidate(
            probe.candidate_key,
            "task_queued",
            reason="versioned semantic threshold passed",
            run_id="run-1",
        )
        self.assertEqual(
            self.db.get_candidate(probe.candidate_key)["status"],
            "task_queued",
        )

    def test_technical_failure_requires_failed_attempt_and_is_audited(self) -> None:
        probe, _, _ = self.insert_scored_candidate()
        self._insert_queue_prerequisites(probe.candidate_key)
        self.db.transition_candidate(
            probe.candidate_key,
            "task_queued",
            reason="queued",
            run_id="run-1",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.transition_candidate(
                probe.candidate_key,
                "technical_failed",
                reason="no attempt evidence",
                run_id="run-1",
            )
        self.db.connection.execute(
            """
            INSERT INTO download_attempts(
                attempt_id, candidate_key, platform, run_id, campaign_id,
                subtype, status, adapter_version, network_config, started_at
            ) VALUES ('attempt-1', ?, 'youtube', 'run-1', 'demand_action_v1',
                      '下跪', 'failed', 'test', 'default', '2026-08-26T00:00:00Z')
            """,
            (probe.candidate_key,),
        )
        self.db.transition_candidate(
            probe.candidate_key,
            "technical_failed",
            reason="download failed",
            run_id="run-1",
        )
        self.assertEqual(self.db.get_candidate(probe.candidate_key)["status"], "technical_failed")

    def test_search_cache_key_includes_all_five_dimensions(self) -> None:
        self.db.register_frozen_query_pack(QUERY_PACKS[1])
        values = {
            "platform": "youtube",
            "query": "CCTV heated argument",
            "query_pack_version": "fight_confounder_v1.qp.v1.0.0",
            "network_config": "default",
            "payload": {"entries": []},
            "fetched_at": "2026-08-26T00:00:00Z",
            "expires_at": "2026-08-26T01:00:00Z",
        }
        self.db.upsert_search_cache(lang="en", **values)
        self.db.upsert_search_cache(lang="es", **values)
        rows = self.db.connection.execute(
            "SELECT lang, payload_json FROM search_cache ORDER BY lang"
        ).fetchall()
        self.assertEqual([row["lang"] for row in rows], ["en", "es"])
        self.assertEqual(json.loads(rows[0]["payload_json"]), {"entries": []})
        self.assertEqual(
            self.db.get_search_cache(
                platform="youtube",
                query="CCTV heated argument",
                lang="en",
                query_pack_version="fight_confounder_v1.qp.v1.0.0",
                network_config="default",
                now="2026-08-26T00:30:00Z",
            ),
            {"entries": []},
        )
        self.assertIsNone(
            self.db.get_search_cache(
                platform="youtube",
                query="CCTV heated argument",
                lang="en",
                query_pack_version="fight_confounder_v1.qp.v1.0.0",
                network_config="default",
                now="2026-08-26T01:00:00Z",
            )
        )

    def test_append_only_audit_tables_reject_mutation(self) -> None:
        probe, _, _ = self.insert_scored_candidate()
        transition_id = self.db.connection.execute(
            "SELECT transition_id FROM state_transitions WHERE candidate_key = ?",
            (probe.candidate_key,),
        ).fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                "UPDATE state_transitions SET reason = 'changed' WHERE transition_id = ?",
                (transition_id,),
            )
        evidence_id = self.db.connection.execute(
            "SELECT evidence_id FROM score_evidence WHERE candidate_key = ? LIMIT 1",
            (probe.candidate_key,),
        ).fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                "DELETE FROM score_evidence WHERE evidence_id = ?", (evidence_id,)
            )
        self.db.register_frozen_query_pack(QUERY_PACKS[0])
        self.db.record_adapter_call(
            request_id="request-1",
            run_id="run-1",
            platform="youtube",
            operation="search",
            query_id="dav1-sign-banner-en-01",
            cache_hit=False,
            status="succeeded",
            started_at="2026-08-26T00:00:00Z",
            finished_at="2026-08-26T00:00:01Z",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                "UPDATE adapter_calls SET cache_hit = 1 WHERE request_id = 'request-1'"
            )

    def _insert_queue_prerequisites(self, candidate_key: str) -> None:
        self.db.register_frozen_query_pack(QUERY_PACKS[0])
        self.db.connection.execute(
            """
            INSERT INTO secondary_batches(
                batch_id, run_id, campaign_id, campaign_policy_version,
                frontier_policy_version,
                status, requested_size, actual_size, created_at
            ) VALUES ('batch-1', 'run-1', 'demand_action_v1', 'campaign-v1', 'frontier-v1',
                      'reviewed', 20, 1, '2026-08-26T00:00:00Z')
            """
        )
        self.db.connection.execute(
            """
            INSERT INTO secondary_batch_items(
                batch_id, candidate_key, campaign_id, subtype, rank, lease_id
            ) VALUES ('batch-1', ?, 'demand_action_v1', '下跪', 1, 'lease-1')
            """,
            (candidate_key,),
        )
        self.db.connection.execute(
            """
            INSERT INTO secondary_filter_decisions(
                batch_id, candidate_key, decision, decided_campaign_id,
                decided_subtype, reasons_json, decided_at
            ) VALUES ('batch-1', ?, 'download_eligible', 'demand_action_v1',
                      '下跪', '[]', '2026-08-26T00:00:00Z')
            """,
            (candidate_key,),
        )
        self.db.connection.execute(
            """
            INSERT INTO queue_assignments(
                candidate_key, batch_id, campaign_id, subtype, rank,
                queued_at, run_id
            ) VALUES (?, 'batch-1', 'demand_action_v1', '下跪', 1,
                      '2026-08-26T00:00:00Z', 'run-1')
            """,
            (candidate_key,),
        )


if __name__ == "__main__":
    unittest.main()
