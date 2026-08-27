from __future__ import annotations

import unittest

from surveillance_video_agent.contracts import ProbeResult
from surveillance_video_agent.resources import evaluate_probe_resources


class ResourceGateTests(unittest.TestCase):
    def probe(self, **changes) -> ProbeResult:
        values = {
            "platform": "youtube",
            "source_id": "abcdefghijk",
            "candidate_key": "youtube:abcdefghijk",
            "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
            "canonical_url": "https://www.youtube.com/watch?v=abcdefghijk",
            "duration_seconds": 30,
            "availability": "public",
            "filesize_approx": 1024,
            "is_live": False,
            "live_status": "not_live",
        }
        values.update(changes)
        return ProbeResult(**values)

    def test_inclusive_duration_boundaries_are_eligible(self) -> None:
        self.assertTrue(evaluate_probe_resources(self.probe(duration_seconds=10)).eligible)
        self.assertTrue(evaluate_probe_resources(self.probe(duration_seconds=900)).eligible)

    def test_unknown_or_out_of_range_duration_is_blocked(self) -> None:
        for value in (None, 9.99, 900.01):
            result = evaluate_probe_resources(self.probe(duration_seconds=value))
            self.assertFalse(result.eligible)
            self.assertTrue(any(reason.code.startswith("duration_") for reason in result.reasons))

    def test_private_live_and_oversize_are_independent_blockers(self) -> None:
        result = evaluate_probe_resources(
            self.probe(
                availability="private",
                is_live=True,
                live_status="is_live",
                filesize_approx=2 * 1024 * 1024 * 1024 + 1,
            )
        )
        self.assertFalse(result.eligible)
        self.assertEqual(
            {reason.code for reason in result.reasons},
            {
                "availability_not_public",
                "live_video",
                "estimated_file_size_out_of_range",
            },
        )


if __name__ == "__main__":
    unittest.main()
