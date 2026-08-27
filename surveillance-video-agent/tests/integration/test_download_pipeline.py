from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from surveillance_video_agent.contracts import (
    AdapterErrorKind,
    DownloadResult,
    ProbeResult,
)
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.download_pipeline import (
    DownloadWorkerConfig,
    SerialDownloadWorker,
    enqueue_downloads,
)
from surveillance_video_agent.manifest import (
    REQUIRED_MANIFEST_KEYS,
    export_campaign_manifest,
)
from surveillance_video_agent.pilot_review import export_pilot_review
from surveillance_video_agent.pilot_feedback import (
    import_pilot_feedback,
    refine_semantic_gate,
)
from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.scoring import (
    CandidateMetadata,
    load_scoring_bundle,
    score_all_tasks,
    score_source,
)
from surveillance_video_agent.technical import sha256_file
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


class FakeDownloadAdapter:
    platform = "youtube"

    def __init__(self, content_by_id: dict[str, bytes], *, fail_ids=()) -> None:
        self.content_by_id = content_by_id
        self.fail_ids = set(fail_ids)
        self.calls: list[str] = []

    def download(self, request):
        self.calls.append(request.candidate_key)
        if request.source_id in self.fail_ids:
            return DownloadResult(
                platform=request.platform,
                source_id=request.source_id,
                candidate_key=request.candidate_key,
                source_url=request.source_url,
                success=False,
                returncode=1,
                error_kind=AdapterErrorKind.NETWORK,
                error_message="fake network failure",
            )
        request.output_dir.mkdir(parents=True, exist_ok=True)
        path = request.output_dir / f"{request.source_id}.mp4"
        path.write_bytes(self.content_by_id[request.source_id])
        return DownloadResult(
            platform=request.platform,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            success=True,
            file_path=path,
            bytes_downloaded=path.stat().st_size,
            returncode=0,
        )


class FlakyDownloadAdapter:
    platform = "youtube"

    def __init__(self, source_id: str, content: bytes) -> None:
        self.source_id = source_id
        self.content = content
        self.calls = 0

    def download(self, request):
        self.calls += 1
        if self.calls == 1:
            return DownloadResult(
                platform=request.platform,
                source_id=request.source_id,
                candidate_key=request.candidate_key,
                source_url=request.source_url,
                success=False,
                returncode=1,
                error_kind=AdapterErrorKind.NETWORK,
                error_message="HTTP Error 503: Service Unavailable",
            )
        request.output_dir.mkdir(parents=True, exist_ok=True)
        path = request.output_dir / f"{self.source_id}.mp4"
        path.write_bytes(self.content)
        return DownloadResult(
            platform=request.platform,
            source_id=request.source_id,
            candidate_key=request.candidate_key,
            source_url=request.source_url,
            success=True,
            file_path=path,
            bytes_downloaded=path.stat().st_size,
            returncode=0,
        )


def fake_technical(path: Path) -> dict:
    return {
        "ffprobe_returncode": 0,
        "video_stream_present": True,
        "video_streams": [{"codec_name": "h264", "width": 640, "height": 360}],
        "duration_seconds": 20.0,
        "decode": [
            {"point": "first", "returncode": 0},
            {"point": "middle", "returncode": 0},
            {"point": "last", "returncode": 0},
        ],
        "technical_passed": True,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


class DownloadPipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = CandidateDatabase(self.root / "state/candidates.sqlite3")
        self.database.initialize()
        self.database.create_run("run-download", "download-integration")
        self.database.register_frozen_query_pack(FIGHT_PACK)
        self.scoring = load_scoring_bundle(SCORING_POLICY, QUERY_PACKS)
        self._register_campaign_policy()

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def test_successful_download_reaches_terminal_state_and_manifest(self) -> None:
        candidate_key = self._create_candidate(
            "iiiiiiiiiii", "CCTV heated argument", "冲突但未攻击", 1
        )
        batch_id = self._create_batch([(candidate_key, "冲突但未攻击")])
        self.assertEqual(enqueue_downloads(self.database, batch_id), (candidate_key,))
        adapter = FakeDownloadAdapter({"iiiiiiiiiii": b"unique-media-one"})
        worker = self._worker(adapter)
        outcome = worker.process_next()
        self.assertEqual(outcome.status, "downloaded")
        self.assertTrue(Path(outcome.media_path).is_file())
        self.assertEqual(self.database.get_candidate(candidate_key)["status"], "downloaded")
        self.assertEqual(
            self.database.connection.execute(
                "SELECT status FROM secondary_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()[0],
            "completed",
        )
        manifest_path = self.root / "outputs/fight_confounder_v1/manifest.jsonl"
        self.assertEqual(
            export_campaign_manifest(
                self.database, "fight_confounder_v1", manifest_path
            ),
            1,
        )
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(REQUIRED_MANIFEST_KEYS - document.keys(), set())
        self.assertEqual(document["technical_status"], "downloaded")
        self.assertTrue(document["technical_checks"]["decode_middle_passed"])
        self.assertEqual(document["sha256"], sha256_file(Path(outcome.media_path)))
        review = export_pilot_review(
            self.database,
            self.root / "outputs/pilot_review.html",
            campaign_ids=("fight_confounder_v1",),
        )
        self.assertEqual(review.video_count, 1)
        page = review.output_path.read_text(encoding="utf-8")
        self.assertIn("<video controls", page)
        self.assertIn(candidate_key, page)
        self.assertEqual(
            review.feedback_template_path.name,
            "pilot_review_feedback_template.json",
        )
        template = json.loads(review.feedback_template_path.read_text(encoding="utf-8"))
        self.assertEqual(template["schema_version"], "pilot_feedback_v1")
        self.assertIsNone(template["labels"][0]["source_correct"])
        filtered = export_pilot_review(
            self.database,
            self.root / "outputs/pilot_review_filtered.html",
            campaign_ids=("fight_confounder_v1",),
            run_ids=("missing-run",),
        )
        self.assertEqual(filtered.video_count, 0)
        feedback_path = self.root / "pilot_feedback.json"
        feedback_path.write_text(
            json.dumps(
                {
                    "schema_version": "pilot_feedback_v1",
                    "exported_at": "2026-08-26T00:00:00Z",
                    "labels": [
                        {
                            "candidate_key": candidate_key,
                            "campaign_id": "fight_confounder_v1",
                            "shown_subtype": "冲突但未攻击",
                            "source_correct": True,
                            "task_usable": False,
                            "corrected_subtype": "",
                            "notes": "visual review",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        imported = import_pilot_feedback(
            self.database,
            feedback_path,
            run_id="run-download",
        )
        self.assertEqual(imported.label_count, 1)
        self.assertEqual(imported.source_correct_count, 1)
        self.assertEqual(imported.task_usable_count, 0)

    def test_refined_semantic_gate_is_append_only_and_rejects_attack_metadata(self) -> None:
        safe = self._create_candidate(
            "vvvvvvvvvvv", "CCTV heated argument", "冲突但未攻击", 1
        )
        attack = self._create_candidate(
            "xxxxxxxxxxx", "CCTV violent mass brawl", "冲突但未攻击", 2
        )
        schema = EmbeddingSchema(
            version="feedback-schema-v1",
            provider="test",
            model="test",
            dimensions=4,
        )
        self.database.register_embedding_schema(schema)
        for number, candidate_key in enumerate((safe, attack), 1):
            self.database.connection.execute(
                """
                INSERT INTO semantic_task_eligibility(
                    eligibility_id, candidate_key, campaign_id, subtype,
                    query_pack_version, embedding_schema_version,
                    policy_version, similarity, threshold, run_id, created_at
                ) VALUES (?, ?, 'fight_confounder_v1', '冲突但未攻击',
                          'fight_confounder_v1.qp.v1.0.0', ?, 'base-v1',
                          0.5, 0.4, 'run-download', '2026-08-26T00:00:00Z')
                """,
                (f"eligibility-{number}", candidate_key, schema.version),
            )
        result = refine_semantic_gate(
            self.database,
            run_id="run-download",
            base_policy_version="base-v1",
            policy_version="refined-v2",
            campaign_thresholds={"fight_confounder_v1": 0.4},
            minimum_source_score=6,
            negative_terms={
                ("fight_confounder_v1", "冲突但未攻击"): ("violent", "brawl")
            },
        )
        self.assertEqual(result.accepted_pair_count, 1)
        self.assertEqual(result.rejected_pair_count, 1)
        accepted = self.database.connection.execute(
            """
            SELECT candidate_key FROM semantic_task_eligibility
            WHERE policy_version = 'refined-v2'
            """
        ).fetchall()
        self.assertEqual([row["candidate_key"] for row in accepted], [safe])
        decision = self.database.connection.execute(
            """
            SELECT accepted, reasons_json FROM semantic_gate_decisions
            WHERE candidate_key = ? AND policy_version = 'refined-v2'
            """,
            (attack,),
        ).fetchone()
        self.assertEqual(decision["accepted"], 0)
        self.assertIn("metadata_contains_task_negative", decision["reasons_json"])

    def test_adapter_failure_becomes_technical_failed_without_output(self) -> None:
        candidate_key = self._create_candidate(
            "jjjjjjjjjjj", "CCTV heated argument", "冲突但未攻击", 1
        )
        batch_id = self._create_batch([(candidate_key, "冲突但未攻击")])
        enqueue_downloads(self.database, batch_id)
        worker = self._worker(
            FakeDownloadAdapter({"jjjjjjjjjjj": b"unused"}, fail_ids={"jjjjjjjjjjj"})
        )
        outcome = worker.process_next()
        self.assertEqual(outcome.status, "technical_failed")
        self.assertEqual(self.database.get_candidate(candidate_key)["status"], "technical_failed")
        self.assertEqual(list((self.root / "outputs").rglob("*.mp4")), [])

    def test_transient_failure_uses_audited_backoff_and_then_succeeds(self) -> None:
        candidate_key = self._create_candidate(
            "ttttttttttt", "CCTV heated argument", "冲突但未攻击", 1
        )
        batch_id = self._create_batch([(candidate_key, "冲突但未攻击")])
        enqueue_downloads(self.database, batch_id)
        delays = []
        adapter = FlakyDownloadAdapter("ttttttttttt", b"retry-media")
        worker = SerialDownloadWorker(
            self.database,
            {"youtube": adapter},
            DownloadWorkerConfig(
                internal_root=self.root / "internal",
                output_root=self.root / "outputs",
                cooldown_min_seconds=0,
                cooldown_max_seconds=0,
                transient_retry_attempts=2,
                retry_backoff_base_seconds=1,
                retry_backoff_max_seconds=2,
                retry_jitter_max_seconds=0,
            ),
            checker=fake_technical,
            sleeper=delays.append,
        )
        outcome = worker.process_next()
        self.assertEqual(outcome.status, "downloaded")
        self.assertEqual(adapter.calls, 2)
        self.assertEqual(delays, [1.0])
        retry = self.database.connection.execute(
            "SELECT retry_ordinal, error_kind, delay_seconds FROM download_retry_events"
        ).fetchone()
        self.assertEqual(tuple(retry), (1, "network", 1.0))

    def test_post_download_resource_violation_never_reaches_output(self) -> None:
        candidate_key = self._create_candidate(
            "rrrrrrrrrrr", "CCTV heated argument", "冲突但未攻击", 1
        )
        batch_id = self._create_batch([(candidate_key, "冲突但未攻击")])
        enqueue_downloads(self.database, batch_id)

        def out_of_range(path):
            result = fake_technical(path)
            result["duration_seconds"] = 901.0
            return result

        worker = self._worker(
            FakeDownloadAdapter({"rrrrrrrrrrr": b"resource-violating-media"}),
            checker=out_of_range,
        )
        outcome = worker.process_next()
        self.assertEqual(outcome.status, "technical_failed")
        attempt = self.database.connection.execute(
            "SELECT status, error_kind, temp_path FROM download_attempts"
        ).fetchone()
        self.assertEqual(attempt["status"], "resource_limit")
        self.assertEqual(attempt["error_kind"], "resource_limit")
        self.assertTrue(Path(attempt["temp_path"]).exists())
        self.assertEqual(list((self.root / "outputs").rglob("*.mp4")), [])

    def test_identical_second_file_is_quarantined_and_not_published_twice(self) -> None:
        first = self._create_candidate(
            "kkkkkkkkkkk", "CCTV heated argument one", "冲突但未攻击", 1
        )
        first_batch = self._create_batch([(first, "冲突但未攻击")])
        enqueue_downloads(self.database, first_batch)
        content = b"identical-media"
        worker = self._worker(FakeDownloadAdapter({"kkkkkkkkkkk": content}))
        self.assertEqual(worker.process_next().status, "downloaded")

        second = self._create_candidate(
            "lllllllllll", "CCTV heated argument two", "冲突但未攻击", 2
        )
        second_batch = self._create_batch([(second, "冲突但未攻击")])
        enqueue_downloads(self.database, second_batch)
        worker = self._worker(FakeDownloadAdapter({"lllllllllll": content}))
        outcome = worker.process_next()
        self.assertEqual(outcome.status, "duplicate_suppressed")
        self.assertIn("quarantine/duplicate_suppressed", outcome.media_path)
        self.assertEqual(self.database.get_candidate(second)["status"], "duplicate_suppressed")
        published = self.database.connection.execute(
            "SELECT COUNT(*) FROM media_objects WHERE publish_status = 'published'"
        ).fetchone()[0]
        self.assertEqual(published, 1)
        edge = self.database.connection.execute(
            "SELECT kind FROM duplicate_edges WHERE kind = 'sha256'"
        ).fetchone()
        self.assertIsNotNone(edge)

    def test_process_until_idle_remains_serial_and_applies_task_cooldown(self) -> None:
        first = self._create_candidate(
            "mmmmmmmmmmm", "CCTV heated argument", "冲突但未攻击", 1
        )
        second = self._create_candidate(
            "nnnnnnnnnnn", "CCTV people hugging", "非攻击性身体接触", 1,
            query_id="fcv1-contact-en-01",
        )
        self._create_batch(
            [(first, "冲突但未攻击"), (second, "非攻击性身体接触")]
        )
        adapter = FakeDownloadAdapter(
            {"mmmmmmmmmmm": b"media-one", "nnnnnnnnnnn": b"media-two"}
        )
        sleeps: list[float] = []
        worker = self._worker(adapter, sleeper=sleeps.append)
        outcomes = worker.process_until_idle()
        self.assertEqual([item.status for item in outcomes], ["downloaded", "downloaded"])
        self.assertEqual(adapter.calls, [first, second])
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(10 <= sleeps[0] <= 20)

    def test_pending_publish_intent_is_recovered_before_new_download(self) -> None:
        candidate_key = self._create_candidate(
            "ooooooooooo", "CCTV heated argument", "冲突但未攻击", 1
        )
        batch_id = self._create_batch([(candidate_key, "冲突但未攻击")])
        enqueue_downloads(self.database, batch_id)
        worker = self._worker(FakeDownloadAdapter({"ooooooooooo": b"unused"}))
        source = self.root / "internal/tmp/recovery/source.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"recoverable-media")
        sha256 = sha256_file(source)
        target = self.root / "outputs/fight_confounder_v1/recovered.mp4"
        attempt_id = "recovery-attempt"
        self.database.connection.execute(
            """
            INSERT INTO download_attempts(
                attempt_id, candidate_key, platform, run_id, campaign_id,
                subtype, status, adapter_version, network_config,
                temp_path, started_at
            ) VALUES (?, ?, 'youtube', 'run-download', 'fight_confounder_v1',
                      '冲突但未攻击', 'running', 'test', 'default', ?,
                      '2026-08-26T00:00:00Z')
            """,
            (attempt_id, candidate_key, str(source)),
        )
        self.database.connection.execute(
            """
            INSERT INTO media_objects(
                sha256, candidate_key, publish_status, final_path,
                bytes, created_at
            ) VALUES (?, ?, 'pending', ?, ?, '2026-08-26T00:00:00Z')
            """,
            (sha256, candidate_key, str(target), source.stat().st_size),
        )
        self.database.connection.execute(
            """
            INSERT INTO media_publish_intents(
                attempt_id, candidate_key, sha256, kind, status,
                temp_path, target_path, created_at
            ) VALUES (?, ?, ?, 'publish', 'pending', ?, ?,
                      '2026-08-26T00:00:00Z')
            """,
            (attempt_id, candidate_key, sha256, str(source), str(target)),
        )
        self.assertEqual(worker.recover_publish_intents(), 1)
        self.assertTrue(target.is_file())
        self.assertFalse(source.exists())
        self.assertEqual(self.database.get_candidate(candidate_key)["status"], "downloaded")
        self.assertEqual(
            self.database.connection.execute(
                "SELECT status FROM secondary_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()[0],
            "completed",
        )

    def test_campaign_capacity_completion_releases_unqueued_batch_items(self) -> None:
        self.database.connection.execute(
            """
            UPDATE campaign_policy_versions SET subtype_limits_json = ?
            WHERE campaign_id = 'fight_confounder_v1' AND policy_version = 'campaign-v1'
            """,
            (json.dumps({"冲突但未攻击": 1}, ensure_ascii=False),),
        )
        first = self._create_candidate(
            "ppppppppppp", "CCTV heated argument one", "冲突但未攻击", 1
        )
        second = self._create_candidate(
            "qqqqqqqqqqq", "CCTV heated argument two", "冲突但未攻击", 2
        )
        batch_id = self._create_batch(
            [(first, "冲突但未攻击"), (second, "冲突但未攻击")]
        )
        self.assertEqual(enqueue_downloads(self.database, batch_id), (first,))
        worker = self._worker(FakeDownloadAdapter({"ppppppppppp": b"capacity-media"}))
        self.assertEqual(worker.process_next().status, "downloaded")
        self.assertEqual(
            self.database.connection.execute(
                "SELECT status FROM secondary_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()[0],
            "completed",
        )
        self.assertEqual(self.database.get_candidate(second)["status"], "source_qualified")
        self.assertEqual(
            self.database.connection.execute(
                """
                SELECT status FROM frontier_entries
                WHERE candidate_key = ? AND subtype = '冲突但未攻击'
                """,
                (second,),
            ).fetchone()[0],
            "suspended",
        )

    def _worker(
        self,
        adapter,
        *,
        sleeper=lambda _seconds: None,
        checker=fake_technical,
    ):
        return SerialDownloadWorker(
            self.database,
            {"youtube": adapter},
            DownloadWorkerConfig(
                internal_root=self.root / "internal",
                output_root=self.root / "outputs",
            ),
            checker=checker,
            sleeper=sleeper,
        )

    def _register_campaign_policy(self) -> None:
        self.database.connection.execute(
            "INSERT INTO campaigns(campaign_id, active_policy_version, created_at) VALUES ('fight_confounder_v1', 'campaign-v1', '2026-08-26T00:00:00Z')"
        )
        self.database.connection.execute(
            """
            INSERT INTO campaign_policy_versions(
                campaign_id, policy_version, subtype_limits_json,
                max_candidates, created_at, created_by, reason
            ) VALUES ('fight_confounder_v1', 'campaign-v1', ?, 50,
                      '2026-08-26T00:00:00Z', 'test', 'test')
            """,
            (
                json.dumps(
                    {
                        "冲突但未攻击": 13,
                        "非攻击性身体接触": 12,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        )

    def _create_candidate(
        self,
        source_id: str,
        title: str,
        subtype: str,
        position: int,
        *,
        query_id: str = "fcv1-conflict-no-attack-en-01",
    ) -> str:
        probe = ProbeResult(
            platform="youtube",
            source_id=source_id,
            candidate_key=f"youtube:{source_id}",
            source_url=f"https://www.youtube.com/watch?v={source_id}",
            canonical_url=f"https://www.youtube.com/watch?v={source_id}",
            title=title,
            video_description="security footage from a fixed camera",
            tags=("cctv",),
            uploader=f"Uploader-{source_id}",
            uploader_id=f"uploader-{source_id}",
            channel="Camera Archive",
            duration_seconds=20,
            availability="public",
            filesize_approx=1024,
            width=640,
            height=360,
            is_live=False,
            live_status="not_live",
        )
        self.database.insert_candidate(probe, run_id="run-download")
        self.database.record_resource_evaluation(
            evaluate_probe_resources(probe), run_id="run-download"
        )
        candidate = CandidateMetadata.from_probe(probe)
        source = score_source(candidate, self.scoring)
        self.database.record_source_score(source, run_id="run-download")
        for task in score_all_tasks(candidate, source, self.scoring):
            self.database.record_task_score(task, run_id="run-download")
        self.database.connection.execute(
            """
            INSERT INTO candidate_discoveries(
                discovery_id, candidate_key, query_id, platform_position,
                discovered_at, run_id
            ) VALUES (?, ?, ?, ?, '2026-08-26T00:00:00Z', 'run-download')
            """,
            (str(uuid.uuid4()), probe.candidate_key, query_id, position),
        )
        self.database.connection.execute(
            """
            INSERT INTO frontier_entries(
                candidate_key, campaign_id, subtype, run_id, status,
                task_score, source_score, platform, lang, attributed_query_id,
                frontier_policy_version, embedding_schema_version,
                dedupe_policy_version, created_at, updated_at
            ) VALUES (?, 'fight_confounder_v1', ?, 'run-download', 'ready',
                      4, ?, 'youtube', 'en', ?, 'frontier-v1',
                      'embedding-v1', 'dedupe-v1',
                      '2026-08-26T00:00:00Z', '2026-08-26T00:00:00Z')
            """,
            (probe.candidate_key, subtype, source.score, query_id),
        )
        return probe.candidate_key

    def _create_batch(self, items: list[tuple[str, str]]) -> str:
        batch_id = str(uuid.uuid4())
        self.database.connection.execute(
            """
            INSERT INTO secondary_batches(
                batch_id, run_id, campaign_id, campaign_policy_version,
                frontier_policy_version, status, requested_size,
                actual_size, created_at, completed_at
            ) VALUES (?, 'run-download', 'fight_confounder_v1', 'campaign-v1',
                      'frontier-v1', 'reviewed', 20, ?,
                      '2026-08-26T00:00:00Z', '2026-08-26T00:00:00Z')
            """,
            (batch_id, len(items)),
        )
        for rank, (candidate_key, subtype) in enumerate(items, 1):
            lease_id = str(uuid.uuid4())
            self.database.connection.execute(
                """
                INSERT INTO secondary_batch_items(
                    batch_id, candidate_key, campaign_id, subtype, rank,
                    vector_similarity, rrf_score, lease_id
                ) VALUES (?, ?, 'fight_confounder_v1', ?, ?, 0.99, 0.03, ?)
                """,
                (batch_id, candidate_key, subtype, rank, lease_id),
            )
            self.database.connection.execute(
                """
                UPDATE frontier_entries
                SET status = 'leased', lease_id = ?
                WHERE candidate_key = ? AND campaign_id = 'fight_confounder_v1'
                  AND subtype = ? AND run_id = 'run-download'
                """,
                (lease_id, candidate_key, subtype),
            )
            self.database.connection.execute(
                """
                INSERT INTO secondary_filter_decisions(
                    batch_id, candidate_key, decision, decided_campaign_id,
                    decided_subtype, vector_similarity, threshold,
                    reasons_json, decided_at
                ) VALUES (?, ?, 'download_eligible', 'fight_confounder_v1',
                          ?, 0.99, 0.8, '{}', '2026-08-26T00:00:00Z')
                """,
                (batch_id, candidate_key, subtype),
            )
        return batch_id


if __name__ == "__main__":
    unittest.main()
