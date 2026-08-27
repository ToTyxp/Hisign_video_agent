from __future__ import annotations

import math
import unittest
from pathlib import Path

from surveillance_video_agent.embedding_smoke import run_embedding_smoke


class FakeQwenProvider:
    provider_id = "dashscope-native"
    model_id = "qwen3.7-text-embedding"
    dimensions = 1024

    def __init__(self) -> None:
        self.documents = ()
        self.queries = ()

    def embed_documents(self, texts):
        self.documents = tuple(texts)
        return [self._unit(index) for index, _ in enumerate(texts)]

    def embed_queries(self, texts):
        self.queries = tuple(texts)
        return [self._unit(0) for _ in texts]

    def _unit(self, index):
        vector = [0.0] * self.dimensions
        vector[index] = 1.0
        self.assert_normalized(vector)
        return vector

    @staticmethod
    def assert_normalized(vector):
        assert math.isclose(sum(value * value for value in vector), 1.0)


class EmbeddingSmokeTests(unittest.TestCase):
    def test_synthetic_vectors_round_trip_through_temp_qdrant(self) -> None:
        provider = FakeQwenProvider()
        report = run_embedding_smoke(provider=provider, environment={})
        self.assertTrue(report["ok"])
        self.assertTrue(report["synthetic_only"])
        self.assertFalse(report["real_candidate_metadata_sent"])
        self.assertEqual(report["document_count"], 4)
        self.assertEqual(report["query_count"], 1)
        self.assertEqual(report["qdrant_match_count"], 4)
        self.assertEqual(report["ranked_matches"][0]["candidate_key"], "youtube:synthetic01")
        self.assertTrue(report["temp_cleaned"])
        self.assertFalse(Path(report["temp_root"]).exists())
        self.assertTrue(all("http" not in text.casefold() for text in provider.documents))


if __name__ == "__main__":
    unittest.main()
