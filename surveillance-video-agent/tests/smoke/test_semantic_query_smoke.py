from __future__ import annotations

import unittest
from pathlib import Path

from surveillance_video_agent.semantic_query_smoke import run_semantic_query_smoke


class FakeQwenQueryProvider:
    provider_id = "dashscope-sdk"
    model_id = "qwen3.7-text-embedding"
    dimensions = 1024

    def __init__(self) -> None:
        self.calls = []

    def embed_queries(self, texts, *, instruct):
        self.calls.append((tuple(texts), instruct))
        vectors = []
        for index, _ in enumerate(texts):
            vector = [0.0] * self.dimensions
            vector[index] = 1.0
            vectors.append(vector)
        return vectors


class SemanticQuerySmokeTests(unittest.TestCase):
    def test_all_seven_queries_are_cached_without_candidate_metadata(self) -> None:
        provider = FakeQwenQueryProvider()
        report = run_semantic_query_smoke(provider=provider, environment={})
        self.assertTrue(report["ok"])
        self.assertFalse(report["candidate_metadata_sent"])
        self.assertEqual(report["ready_query_vectors"], 7)
        self.assertEqual(report["successful_embedding_calls"], 2)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual([len(call[0]) for call in provider.calls], [3, 4])
        self.assertTrue(report["cache_reuse_passed"])
        self.assertTrue(report["temp_cleaned"])
        self.assertFalse(Path(report["temp_root"]).exists())


if __name__ == "__main__":
    unittest.main()
