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
POLICY = ROOT / "query-packs/scoring-policy.v1.9.0.json"
QUERY_PACKS = (
    ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.3.0.json",
    ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.3.0.json",
    ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.11.0.json",
    ROOT / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.0.0.json",
)


class FightPositiveScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_scoring_bundle(POLICY, QUERY_PACKS)

    def test_real_surveillance_fight_qualifies(self) -> None:
        metadata = CandidateMetadata(
            candidate_key="youtube:abcdefghijk",
            title="CCTV street brawl people punching and kicking raw footage",
        )
        source = score_source(metadata, self.policy)
        task = score_task(
            metadata,
            source,
            self.policy,
            campaign_id="fight_positive_v1",
            subtype="真实打架/斗殴",
        )
        self.assertTrue(source.qualified)
        self.assertTrue(task.qualified)
        self.assertGreaterEqual(task.score, 4)

    def test_play_fight_training_and_weapon_events_are_forbidden(self) -> None:
        for title in (
            "CCTV friends play fighting raw footage",
            "security camera boxing training fight raw video",
            "surveillance camera stabbing knife attack raw footage",
        ):
            metadata = CandidateMetadata(
                candidate_key="youtube:abcdefghijk",
                title=title,
            )
            source = score_source(metadata, self.policy)
            task = score_task(
                metadata,
                source,
                self.policy,
                campaign_id="fight_positive_v1",
                subtype="真实打架/斗殴",
            )
            self.assertTrue(source.qualified)
            self.assertFalse(task.qualified)


if __name__ == "__main__":
    unittest.main()
