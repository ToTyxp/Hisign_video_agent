from __future__ import annotations

import math
import unittest
from unittest.mock import MagicMock

from surveillance_video_agent.embedding import EmbeddingSchema
from surveillance_video_agent.feedback_rerank import build_feedback_ranking_vectors


class _FakeIndex:
    def __init__(self, vectors):
        self.vectors = vectors

    def get_calibration_candidate_vector(self, schema, *, candidate_key):
        return self.vectors.get(candidate_key)


class FeedbackRerankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = EmbeddingSchema(
            version="feedback-test-v1",
            provider="test",
            model="test",
            dimensions=2,
        )

    def test_feedback_changes_ranking_vector_and_keeps_it_normalized(self) -> None:
        rows = [
            {"candidate_key": "task-positive", "shown_subtype": "举牌/横幅", "source_correct": 1, "task_usable": 1},
            {"candidate_key": "task-positive-2", "shown_subtype": "举牌/横幅", "source_correct": 1, "task_usable": 1},
            {"candidate_key": "task-negative", "shown_subtype": "举牌/横幅", "source_correct": 0, "task_usable": 0},
            {"candidate_key": "task-negative-2", "shown_subtype": "举牌/横幅", "source_correct": 0, "task_usable": 0},
        ]
        database = MagicMock()
        database.connection.execute.return_value.fetchall.return_value = rows
        index = _FakeIndex(
            {
                "task-positive": [0.8, 0.6],
                "task-positive-2": [0.6, 0.8],
                "task-negative": [0.8, -0.6],
                "task-negative-2": [0.6, -0.8],
            }
        )
        result = build_feedback_ranking_vectors(
            database,
            index,
            self.schema,
            campaign_id="sign_action_v1",
            base_vectors={"举牌/横幅": [1.0, 0.0]},
            task_weight=0.5,
            source_weight=0.25,
        )
        vector = result["举牌/横幅"]
        self.assertGreater(vector[1], 0)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in vector)), 1.0)

    def test_zero_weights_return_base_without_reading_labels(self) -> None:
        database = MagicMock()
        result = build_feedback_ranking_vectors(
            database,
            _FakeIndex({}),
            self.schema,
            campaign_id="sign_action_v1",
            base_vectors={"举牌/横幅": [1.0, 0.0]},
            task_weight=0,
            source_weight=0,
        )
        self.assertEqual(result, {"举牌/横幅": [1.0, 0.0]})
        database.connection.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
