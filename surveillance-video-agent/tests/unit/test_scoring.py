from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from surveillance_video_agent.scoring import (
    CandidateMetadata,
    load_scoring_bundle,
    score_all_tasks,
    score_source,
    score_task,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "query-packs/scoring-policy.v1.0.0.draft.json"
QUERY_PACKS = (
    ROOT
    / "query-packs/demand_action_v1/demand_action_v1.qp.v1.0.0.draft.json",
    ROOT
    / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json",
)


class ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_scoring_bundle(
            POLICY,
            QUERY_PACKS,
        )

    def candidate(self, **changes) -> CandidateMetadata:
        values = {
            "candidate_key": "youtube:abcdefghijk",
            "title": "",
            "video_description": "",
            "tags": (),
            "uploader": "",
            "channel": "",
            "playlist": "",
        }
        values.update(changes)
        return CandidateMetadata(**values)

    def test_runtime_loader_accepts_confirmed_frozen_policy(self) -> None:
        loaded = load_scoring_bundle(POLICY, QUERY_PACKS)
        self.assertEqual(loaded.policy_version, "surveillance_scoring_v1.0.0")

    def test_runtime_loader_rejects_tampered_frozen_policy(self) -> None:
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        document["source"]["title_strong_anchors"].append("tampered anchor")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tampered-policy.json"
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_scoring_bundle(path, QUERY_PACKS)

    def test_policy_loads_all_seven_subtypes_and_frozen_query_versions(self) -> None:
        self.assertEqual(len(self.policy.tasks), 7)
        self.assertEqual(
            set(self.policy.query_pack_versions),
            {"demand_action_v1.qp.v1.0.0", "fight_confounder_v1.qp.v1.0.0"},
        )

    def test_strong_title_anchor_and_rawness_qualify_once(self) -> None:
        result = score_source(
            self.candidate(title="Original uncut CCTV parking lot footage"),
            self.policy,
        )
        self.assertEqual(result.score, 5)
        self.assertTrue(result.qualified)
        self.assertEqual(result.camera_pool, "surveillance")
        self.assertEqual(
            [item.rule_code for item in result.evidence],
            ["source.title_strong_anchor", "source.rawness"],
        )

    def test_metadata_evidence_alone_does_not_pass_source_threshold(self) -> None:
        result = score_source(
            self.candidate(video_description="Recorded by a fixed camera security system"),
            self.policy,
        )
        self.assertEqual(result.score, 2)
        self.assertFalse(result.qualified)

    def test_legacy_prior_is_capped_and_can_only_add_two(self) -> None:
        candidate = self.candidate(video_description="fixed camera security system")
        result = score_source(candidate, self.policy, legacy_uploader_prior=2)
        self.assertEqual(result.score, 4)
        self.assertTrue(result.qualified)
        with self.assertRaises(ValueError):
            score_source(candidate, self.policy, legacy_uploader_prior=3)

    def test_packaging_penalty_can_keep_candidate_below_threshold(self) -> None:
        result = score_source(
            self.candidate(title="News report uncut CCTV footage compilation"),
            self.policy,
        )
        self.assertEqual(result.score, 2)
        self.assertFalse(result.qualified)
        self.assertIn("source.packaging_penalty", [item.rule_code for item in result.evidence])

    def test_hard_exclusion_overrides_positive_source_terms(self) -> None:
        result = score_source(
            self.candidate(title="CCTV security camera review and product demo"),
            self.policy,
        )
        self.assertTrue(result.hard_excluded)
        self.assertFalse(result.qualified)
        self.assertEqual(result.score, 0)
        self.assertIn("equipment_ad", [item.category for item in result.hard_exclusions])

    def test_multilingual_source_anchor_is_derived_from_frozen_query_pack(self) -> None:
        result = score_source(
            self.candidate(title="cámara de vigilancia discusión acalorada"),
            self.policy,
        )
        self.assertTrue(result.qualified)
        self.assertEqual(result.score, 4)

    def test_title_action_scores_four_after_source_gate(self) -> None:
        candidate = self.candidate(title="CCTV kneeling protest at door")
        source = score_source(candidate, self.policy)
        result = score_task(
            candidate,
            source,
            self.policy,
            campaign_id="demand_action_v1",
            subtype="下跪",
        )
        self.assertEqual(result.score, 4)
        self.assertTrue(result.qualified)

    def test_description_action_scores_two_and_does_not_qualify(self) -> None:
        candidate = self.candidate(
            title="CCTV footage",
            video_description="A kneeling protest outside the entrance",
        )
        source = score_source(candidate, self.policy)
        result = score_task(
            candidate,
            source,
            self.policy,
            campaign_id="demand_action_v1",
            subtype="下跪",
        )
        self.assertEqual(result.score, 2)
        self.assertFalse(result.qualified)

    def test_task_scoring_is_blocked_when_source_gate_fails(self) -> None:
        candidate = self.candidate(title="Phone video of a kneeling protest")
        source = score_source(candidate, self.policy)
        result = score_task(
            candidate,
            source,
            self.policy,
            campaign_id="demand_action_v1",
            subtype="下跪",
        )
        self.assertTrue(source.hard_excluded)
        self.assertTrue(result.blocked_by_source_gate)
        self.assertEqual(result.score, 0)

    def test_task_scoring_rejects_source_result_from_another_policy(self) -> None:
        candidate = self.candidate(title="CCTV kneeling protest")
        source = score_source(candidate, self.policy)
        altered = type(source)(
            candidate_key=source.candidate_key,
            policy_version="different-policy",
            score=source.score,
            qualified=source.qualified,
            hard_excluded=source.hard_excluded,
            camera_pool=source.camera_pool,
            evidence=source.evidence,
            hard_exclusions=source.hard_exclusions,
        )
        with self.assertRaises(ValueError):
            score_task(
                candidate,
                altered,
                self.policy,
                campaign_id="demand_action_v1",
                subtype="下跪",
            )

    def test_chinese_scene_prior_requires_scene_and_ordinary_behavior(self) -> None:
        matching = self.candidate(title="CCTV 停车场 多人等待")
        matching_source = score_source(matching, self.policy)
        matching_task = score_task(
            matching,
            matching_source,
            self.policy,
            campaign_id="fight_confounder_v1",
            subtype="场景先验",
        )
        self.assertEqual(matching_task.score, 4)
        self.assertTrue(matching_task.qualified)

        missing_behavior = self.candidate(title="CCTV 停车场")
        missing_source = score_source(missing_behavior, self.policy)
        missing_task = score_task(
            missing_behavior,
            missing_source,
            self.policy,
            campaign_id="fight_confounder_v1",
            subtype="场景先验",
        )
        self.assertEqual(missing_task.score, 0)

    def test_all_subtypes_are_scored_independently(self) -> None:
        candidate = self.candidate(title="CCTV people hugging")
        source = score_source(candidate, self.policy)
        results = score_all_tasks(candidate, source, self.policy)
        self.assertEqual(len(results), 7)
        qualified = [(item.campaign_id, item.subtype) for item in results if item.qualified]
        self.assertEqual(qualified, [("fight_confounder_v1", "非攻击性身体接触")])


if __name__ == "__main__":
    unittest.main()
