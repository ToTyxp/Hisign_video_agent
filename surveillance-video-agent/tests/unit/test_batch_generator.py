from __future__ import annotations

import unittest

from surveillance_video_agent.batch_generator import (
    CampaignPolicy,
    FrontierPolicy,
    reciprocal_rank_fusion,
)


class BatchGeneratorUnitTests(unittest.TestCase):
    def test_rrf_combines_ranks_without_adding_raw_score_scales(self) -> None:
        scores = reciprocal_rank_fusion(
            ["a", "b", "c"],
            ["b", "a", "c"],
            rrf_k=60,
        )
        self.assertAlmostEqual(scores["a"], scores["b"])
        self.assertGreater(scores["a"], scores["c"])

    def test_campaign_policy_rejects_subtype_sum_over_campaign_capacity(self) -> None:
        with self.assertRaises(ValueError):
            CampaignPolicy(
                campaign_id="campaign",
                version="v1",
                subtype_limits=(("a", 3), ("b", 3)),
                max_candidates=5,
            )

    def test_frontier_policy_keeps_batch_and_threshold_bounded(self) -> None:
        self.assertEqual(FrontierPolicy(version="v1").batch_size, 20)
        with self.assertRaises(ValueError):
            FrontierPolicy(version="v1", batch_size=21)
        with self.assertRaises(ValueError):
            FrontierPolicy(version="v1", semantic_score_threshold=1.1)


if __name__ == "__main__":
    unittest.main()
