from __future__ import annotations

import unittest
from pathlib import Path

from surveillance_video_agent.scoring import (
    CandidateMetadata,
    load_scoring_bundle,
    score_sign_mobile_source,
    score_source,
    score_task,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "query-packs/scoring-policy.v1.8.0.json"
QUERY_PACKS = (
    ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.3.0.json",
    ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.2.0.json",
    ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.9.0.json",
)


class MobileSignSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_scoring_bundle(POLICY, QUERY_PACKS)

    def test_vertical_real_world_video_qualifies_only_for_mobile_sign_gate(self) -> None:
        metadata = CandidateMetadata(
            candidate_key="youtube:abcdefghijk",
            title="Person holding protest sign",
        )
        generic = score_source(metadata, self.policy)
        self.assertFalse(generic.qualified)
        mobile = score_sign_mobile_source(
            metadata,
            self.policy,
            width=720,
            height=1280,
        )
        self.assertTrue(mobile.qualified)
        self.assertEqual(mobile.camera_pool, "mobile_adjacent")
        self.assertEqual(mobile.score, 4)

    def test_mobile_exception_does_not_override_game_exclusion(self) -> None:
        metadata = CandidateMetadata(
            candidate_key="youtube:abcdefghijk",
            title="Vertical video game walkthrough",
        )
        result = score_sign_mobile_source(
            metadata,
            self.policy,
            width=720,
            height=1280,
        )
        self.assertTrue(result.hard_excluded)
        self.assertFalse(result.qualified)

    def test_mobile_query_plus_short_duration_qualifies_horizontal_video(self) -> None:
        metadata = CandidateMetadata(
            candidate_key="youtube:abcdefghijk",
            title="One person holding a sign",
        )
        result = score_sign_mobile_source(
            metadata,
            self.policy,
            width=1280,
            height=720,
            duration_seconds=45,
            discovered_by_mobile_query=True,
        )
        self.assertTrue(result.qualified)
        self.assertEqual(result.camera_pool, "mobile_adjacent")
        self.assertEqual(result.evidence[0].rule_code, "source.mobile_query_short_duration")

    def test_news_packaging_in_uploader_or_description_cancels_mobile_anchor(self) -> None:
        for metadata in (
            CandidateMetadata(
                candidate_key="youtube:abcdefghijk",
                title="Person holding protest sign",
                uploader="Euronews",
            ),
            CandidateMetadata(
                candidate_key="youtube:abcdefghijk",
                title="Woman holding a placard",
                video_description="Interview recorded after a press conference",
            ),
        ):
            result = score_sign_mobile_source(
                metadata,
                self.policy,
                width=720,
                height=1280,
            )
            self.assertFalse(result.qualified)
            self.assertEqual(result.score, 1)
            self.assertIn(
                "source.packaging_penalty",
                {item.rule_code for item in result.evidence},
            )

    def test_ugc_short_without_packaging_still_qualifies(self) -> None:
        result = score_sign_mobile_source(
            CandidateMetadata(
                candidate_key="youtube:abcdefghijk",
                title="One person holding protest sign #shorts",
                uploader="Everyday Moments",
            ),
            self.policy,
            width=720,
            height=1280,
        )
        self.assertTrue(result.qualified)
        self.assertEqual(result.score, 4)

    def test_sign_campaign_reuses_frozen_sign_action_rules(self) -> None:
        metadata = CandidateMetadata(
            candidate_key="youtube:abcdefghijk",
            title="Lone protester holding protest sign shorts",
        )
        source = score_sign_mobile_source(
            metadata,
            self.policy,
            width=720,
            height=1280,
        )
        task = score_task(
            metadata,
            source,
            self.policy,
            campaign_id="sign_action_v1",
            subtype="举牌/横幅",
        )
        self.assertTrue(source.qualified)
        self.assertTrue(task.qualified)

    def test_ai_generated_mobile_video_remains_hard_excluded(self) -> None:
        metadata = CandidateMetadata(
            candidate_key="youtube:abcdefghijk",
            title="AI-generated video protest sign shorts",
        )
        result = score_sign_mobile_source(
            metadata,
            self.policy,
            width=720,
            height=1280,
        )
        self.assertTrue(result.hard_excluded)

    def test_large_scale_protest_is_task_forbidden_for_sign_campaign(self) -> None:
        metadata = CandidateMetadata(
            candidate_key="youtube:abcdefghijk",
            title="Thousands of protesters holding signs shorts",
        )
        source = score_sign_mobile_source(
            metadata,
            self.policy,
            width=720,
            height=1280,
        )
        task = score_task(
            metadata,
            source,
            self.policy,
            campaign_id="sign_action_v1",
            subtype="举牌/横幅",
        )
        self.assertTrue(source.qualified)
        self.assertFalse(task.qualified)
        self.assertEqual(task.evidence[0].rule_code, "task.forbidden_semantics")

    def test_fall_video_is_task_forbidden_for_sign_campaign(self) -> None:
        metadata = CandidateMetadata(
            candidate_key="youtube:abcdefghijk",
            title="Woman falls on ice shorts",
        )
        source = score_sign_mobile_source(
            metadata,
            self.policy,
            width=720,
            height=1280,
        )
        task = score_task(
            metadata,
            source,
            self.policy,
            campaign_id="sign_action_v1",
            subtype="举牌/横幅",
        )
        self.assertTrue(source.qualified)
        self.assertFalse(task.qualified)

    def test_numeric_participant_count_over_five_is_forbidden(self) -> None:
        for title in (
            "450 manifestants avec pancartes shorts",
            "25.000 personas se manifiestan video vertical",
        ):
            metadata = CandidateMetadata(
                candidate_key="youtube:abcdefghijk",
                title=title,
            )
            source = score_sign_mobile_source(
                metadata,
                self.policy,
                width=720,
                height=1280,
            )
            task = score_task(
                metadata,
                source,
                self.policy,
                campaign_id="sign_action_v1",
                subtype="举牌/横幅",
            )
            self.assertFalse(task.qualified)
            self.assertIn("participant count", task.evidence[0].reason)

    def test_stock_tutorial_and_game_mobile_videos_are_hard_excluded(self) -> None:
        for title in (
            "Woman holding sign stock footage Videohive",
            "How to make a protest sign shorts",
            "In-game protest sign Roblox",
        ):
            result = score_sign_mobile_source(
                CandidateMetadata(
                    candidate_key="youtube:abcdefghijk",
                    title=title,
                ),
                self.policy,
                width=720,
                height=1280,
            )
            self.assertTrue(result.hard_excluded)


if __name__ == "__main__":
    unittest.main()
