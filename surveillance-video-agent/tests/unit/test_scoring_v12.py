from __future__ import annotations

import unittest
from pathlib import Path

from surveillance_video_agent.scoring import (
    CandidateMetadata,
    load_scoring_bundle,
    score_source,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "query-packs/scoring-policy.v1.2.0.json"
QUERY_PACKS = (
    ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.3.0.json",
    ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.2.0.json",
)


class ScoringV12Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_scoring_bundle(POLICY, QUERY_PACKS)

    def test_french_game_walkthrough_overrides_camera_anchor(self) -> None:
        candidate = CandidateMetadata(
            candidate_key="dailymotion:x26f9nf",
            title="Red Johnson caméra de sécurité",
            video_description=(
                "Cette vidéo vous montre comment résoudre l'énigme. "
                "Le but du jeu est expliqué dans cette soluce Steam."
            ),
        )
        result = score_source(candidate, self.policy)
        self.assertTrue(result.hard_excluded)
        self.assertFalse(result.qualified)
        categories = {item.category for item in result.hard_exclusions}
        self.assertEqual(categories, {"game", "tutorial"})


if __name__ == "__main__":
    unittest.main()
