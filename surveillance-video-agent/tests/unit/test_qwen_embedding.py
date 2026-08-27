from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dashscope

from surveillance_video_agent.embedding import (
    CandidateMetadata,
    build_candidate_embedding_input,
    normalize_embedding_text,
)
from surveillance_video_agent.qwen_embedding import (
    DEFAULT_DASHSCOPE_BASE_URL,
    DEFAULT_QUERY_INSTRUCTION,
    QWEN_SCHEMA,
    DashScopeQwenEmbeddingProvider,
    EmbeddingErrorKind,
    EmbeddingProviderError,
    OfficialDashScopeSdkClient,
    load_project_environment,
)


class FakeSdkClient:
    def __init__(self, *, status_code: int = 200, invalid: bool = False) -> None:
        self.status_code = status_code
        self.invalid = invalid
        self.calls = []

    def call(self, **arguments):
        self.calls.append(arguments)
        texts = arguments["input"]
        if self.status_code != 200:
            return SimpleNamespace(
                status_code=self.status_code,
                code="InvalidApiKey",
                message="must not escape",
                output=None,
            )
        entries = []
        for index in reversed(range(len(texts))):
            vector = [0.0] * 1024
            vector[0] = 3.0
            vector[1] = 4.0
            entries.append(
                {
                    "text_index": index,
                    "embedding": vector[:-1] if self.invalid else vector,
                }
            )
        return SimpleNamespace(
            status_code=200,
            code="",
            message="",
            output={"embeddings": entries},
        )


class QwenEmbeddingProviderTests(unittest.TestCase):
    def provider(self, client: FakeSdkClient, **changes):
        values = {
            "sdk_client": client,
            "environment": {"DASHSCOPE_API_KEY": "unit-test-secret"},
        }
        values.update(changes)
        return DashScopeQwenEmbeddingProvider(**values)

    def test_documents_use_sdk_dense_document_role_and_normalize_vectors(self) -> None:
        client = FakeSdkClient()
        provider = self.provider(client)
        vectors = provider.embed_documents(("first", "second"))
        self.assertEqual(len(vectors), 2)
        self.assertAlmostEqual(vectors[0][0], 0.6)
        self.assertAlmostEqual(vectors[0][1], 0.8)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in vectors[0])), 1.0)
        self.assertEqual(
            client.calls[0],
            {
                "model": "qwen3.7-text-embedding",
                "input": ["first", "second"],
                "text_type": "document",
                "dimension": 1024,
                "output_type": "dense",
                "instruct": None,
            },
        )

    def test_queries_use_query_role_and_versioned_instruction(self) -> None:
        client = FakeSdkClient()
        provider = self.provider(client)
        provider.embed_queries(("冲突但未攻击",))
        self.assertEqual(client.calls[0]["text_type"], "query")
        self.assertEqual(client.calls[0]["instruct"], DEFAULT_QUERY_INSTRUCTION)

    def test_official_wrapper_matches_user_selected_dashscope_call(self) -> None:
        response = SimpleNamespace(status_code=200)
        with patch.object(
            dashscope.TextEmbedding,
            "call",
            return_value=response,
        ) as call:
            actual = OfficialDashScopeSdkClient().call(
                model="qwen3.7-text-embedding",
                input=["衣服的质量杠杠的，很漂亮"],
                text_type="document",
                dimension=1024,
                output_type="dense",
                instruct=None,
            )
        self.assertIs(actual, response)
        self.assertEqual(dashscope.base_http_api_url, DEFAULT_DASHSCOPE_BASE_URL)
        call.assert_called_once_with(
            model="qwen3.7-text-embedding",
            input=["衣服的质量杠杠的，很漂亮"],
            text_type="document",
            dimension=1024,
            output_type="dense",
        )
        self.assertNotIn("api_key", call.call_args.kwargs)

    def test_dotenv_key_is_synchronized_to_sdk_without_call_parameter(self) -> None:
        response = SimpleNamespace(
            status_code=200,
            code="",
            output={
                "embeddings": [
                    {
                        "text_index": 0,
                        "embedding": [1.0] + [0.0] * 1023,
                    }
                ]
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("DASHSCOPE_API_KEY=file-key\n", encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True), patch.object(
                dashscope.TextEmbedding,
                "call",
                return_value=response,
            ) as call:
                DashScopeQwenEmbeddingProvider(env_path=path).embed_documents(("text",))
                self.assertEqual(dashscope.api_key, "file-key")
                self.assertNotIn("api_key", call.call_args.kwargs)

    def test_more_than_twenty_inputs_are_split_without_reordering(self) -> None:
        client = FakeSdkClient()
        provider = self.provider(client)
        vectors = provider.embed(tuple(f"text-{index}" for index in range(21)))
        self.assertEqual(len(vectors), 21)
        self.assertEqual([len(call["input"]) for call in client.calls], [20, 1])

    def test_project_dotenv_is_loaded_without_overriding_existing_shell_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("DASHSCOPE_API_KEY=file-key\n", encoding="utf-8")
            with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "shell-key"}, clear=True):
                loaded = load_project_environment(path)
                self.assertTrue(loaded.api_key_present)
                self.assertEqual(__import__("os").environ["DASHSCOPE_API_KEY"], "shell-key")

    def test_missing_key_fails_before_sdk_and_points_to_visible_env_file(self) -> None:
        client = FakeSdkClient()
        provider = DashScopeQwenEmbeddingProvider(
            sdk_client=client,
            environment={},
        )
        with self.assertRaises(EmbeddingProviderError) as context:
            provider.embed(("candidate",))
        self.assertEqual(context.exception.kind, EmbeddingErrorKind.MISSING_API_KEY)
        self.assertIn(".env", str(context.exception))
        self.assertEqual(client.calls, [])

    def test_only_the_unified_key_name_is_allowed(self) -> None:
        with self.assertRaises(ValueError):
            DashScopeQwenEmbeddingProvider(api_key_env="ANOTHER_KEY", environment={})

    def test_authentication_and_invalid_vectors_are_classified_without_api_body(self) -> None:
        auth = self.provider(FakeSdkClient(status_code=401))
        with self.assertRaises(EmbeddingProviderError) as context:
            auth.embed(("candidate",))
        self.assertEqual(context.exception.kind, EmbeddingErrorKind.AUTHENTICATION)
        self.assertNotIn("must not escape", str(context.exception))

        invalid = self.provider(FakeSdkClient(invalid=True))
        with self.assertRaises(EmbeddingProviderError) as context:
            invalid.embed(("candidate",))
        self.assertEqual(context.exception.kind, EmbeddingErrorKind.INVALID_RESPONSE)

    def test_schema_and_text_normalization_are_reproducible(self) -> None:
        self.assertEqual(QWEN_SCHEMA.provider, "dashscope-sdk")
        self.assertEqual(QWEN_SCHEMA.dimensions, 1024)
        decomposed = "Cafe\u0301  camera\r\n  raw   footage"
        composed = "Café camera\nraw footage"
        self.assertEqual(normalize_embedding_text(decomposed), composed)
        left = build_candidate_embedding_input(
            CandidateMetadata(candidate_key="youtube:left", title=decomposed),
            QWEN_SCHEMA,
        )
        right = build_candidate_embedding_input(
            CandidateMetadata(candidate_key="youtube:right", title=composed),
            QWEN_SCHEMA,
        )
        self.assertEqual(left.input_hash, right.input_hash)


if __name__ == "__main__":
    unittest.main()
