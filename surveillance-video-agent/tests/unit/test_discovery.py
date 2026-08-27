from __future__ import annotations

import unittest

from surveillance_video_agent.discovery import DiscoveryConfig


class DiscoveryConfigTests(unittest.TestCase):
    def base(self, **changes) -> DiscoveryConfig:
        values = {
            "campaign_id": "fight_confounder_v1",
            "query_pack_version": "fight_confounder_v1.qp.v1.0.0",
        }
        values.update(changes)
        return DiscoveryConfig(**values)

    def test_v1_network_and_request_limits_are_hard_ceilings(self) -> None:
        with self.assertRaises(ValueError):
            self.base(network_config="vpn-a")
        with self.assertRaises(ValueError):
            self.base(per_query_limit=21)
        with self.assertRaises(ValueError):
            self.base(probe_limit=151)
        with self.assertRaises(ValueError):
            self.base(max_requests_per_platform=3)

    def test_query_subset_must_be_unique(self) -> None:
        with self.assertRaises(ValueError):
            self.base(query_ids=("query-1", "query-1"))


if __name__ == "__main__":
    unittest.main()
