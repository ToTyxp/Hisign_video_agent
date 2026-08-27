from __future__ import annotations

import unittest
from pathlib import Path

from surveillance_video_agent.scoring import (
    CandidateMetadata,
    load_scoring_bundle,
    score_source,
    score_task,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "query-packs/scoring-policy.v1.1.0.json"
QUERY_PACKS = (
    ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.1.0.json",
    ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.1.0.json",
)


class ScoringV11Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_scoring_bundle(POLICY, QUERY_PACKS)

    def score(self, title, campaign, subtype, *, description=""):
        candidate = CandidateMetadata(
            candidate_key="youtube:abcdefghijk",
            title=title,
            video_description=description,
        )
        source = score_source(candidate, self.policy)
        return score_task(
            candidate,
            source,
            self.policy,
            campaign_id=campaign,
            subtype=subtype,
        )

    def test_alias_title_keeps_original_four_point_threshold(self) -> None:
        result = self.score(
            "CCTV neighbors arguing outside",
            "fight_confounder_v1",
            "冲突但未攻击",
        )
        self.assertEqual(result.score, 4)
        self.assertTrue(result.qualified)
        self.assertEqual(result.evidence[0].rule_code, "task.title_action")

    def test_forbidden_physical_attack_blocks_conflict_alias(self) -> None:
        result = self.score(
            "CCTV neighbors arguing and punching",
            "fight_confounder_v1",
            "冲突但未攻击",
        )
        self.assertEqual(result.score, 0)
        self.assertFalse(result.qualified)
        self.assertEqual(result.evidence[0].rule_code, "task.forbidden_semantics")

    def test_kneeling_requires_demand_conjunction_and_blocks_prayer(self) -> None:
        matching = self.score(
            "CCTV woman on her knees pleading",
            "demand_action_v1",
            "下跪",
        )
        self.assertTrue(matching.qualified)
        prayer = self.score(
            "CCTV woman kneeling prayer",
            "demand_action_v1",
            "下跪",
        )
        self.assertFalse(prayer.qualified)

    def test_sign_and_sit_in_use_same_field_conjunctions(self) -> None:
        sign = self.score(
            "CCTV protesters holding a banner",
            "demand_action_v1",
            "举牌/横幅",
        )
        self.assertTrue(sign.qualified)
        sitting = self.score(
            "Security camera protesters sitting and blocking entrance",
            "demand_action_v1",
            "静坐",
        )
        self.assertTrue(sitting.qualified)

    def test_scene_prior_requires_location_and_ordinary_behavior(self) -> None:
        matching = self.score(
            "CCTV people waiting at apartment entrance",
            "fight_confounder_v1",
            "场景先验",
        )
        self.assertTrue(matching.qualified)
        location_only = self.score(
            "CCTV apartment entrance",
            "fight_confounder_v1",
            "场景先验",
        )
        self.assertFalse(location_only.qualified)

    def test_metadata_alias_remains_two_points_and_does_not_qualify(self) -> None:
        result = self.score(
            "CCTV footage",
            "fight_confounder_v1",
            "非攻击性身体接触",
            description="Two people hugging near the door",
        )
        self.assertEqual(result.score, 2)
        self.assertFalse(result.qualified)


if __name__ == "__main__":
    unittest.main()
