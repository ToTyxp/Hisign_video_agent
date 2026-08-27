"""DashScope SDK qwen3.7 embedding provider with one project-level API key."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import dashscope
from dotenv import load_dotenv

from surveillance_video_agent.embedding import EmbeddingSchema


QWEN_MODEL_ID = "qwen3.7-text-embedding"
QWEN_DIMENSIONS = 1024
QWEN_PROVIDER_ID = "dashscope-sdk"
QWEN_SCHEMA = EmbeddingSchema(
    version="qwen3.7-text-embedding-dense-1024-dashscope-sdk-v1.0.0",
    provider=QWEN_PROVIDER_ID,
    model=QWEN_MODEL_ID,
    dimensions=QWEN_DIMENSIONS,
    distance="cosine",
    text_template_version="metadata-v1",
    normalization_version="unicode-nfc-whitespace-v1",
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_QUERY_INSTRUCTION = (
    "Given a surveillance-video task definition, retrieve public video metadata "
    "relevant to the described event subtype."
)
_ALLOWED_DIMENSIONS = frozenset({2560, 2048, 1536, 1024, 768, 512, 256})
_MAX_BATCH_SIZE = 20


class EmbeddingErrorKind(str, Enum):
    MISSING_API_KEY = "missing_api_key"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    SERVICE = "service"
    INVALID_RESPONSE = "invalid_response"


class EmbeddingProviderError(RuntimeError):
    def __init__(
        self,
        kind: EmbeddingErrorKind,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code


class DashScopeSdkClient(Protocol):
    def call(
        self,
        *,
        model: str,
        input: str | Sequence[str],
        text_type: str,
        dimension: int,
        output_type: str,
        instruct: str | None,
    ) -> Any: ...


class OfficialDashScopeSdkClient:
    """Thin wrapper around the exact SDK call chosen by the user."""

    def call(
        self,
        *,
        model: str,
        input: str | Sequence[str],
        text_type: str,
        dimension: int,
        output_type: str,
        instruct: str | None,
    ) -> Any:
        # Do not inherit a process-wide alternate endpoint that could receive
        # DASHSCOPE_API_KEY. v1 always resets to the official China endpoint.
        dashscope.base_http_api_url = DEFAULT_DASHSCOPE_BASE_URL
        arguments: dict[str, Any] = {
            "model": model,
            "input": input,
            "text_type": text_type,
            "dimension": dimension,
            "output_type": output_type,
        }
        if instruct is not None:
            arguments["instruct"] = instruct
        return dashscope.TextEmbedding.call(**arguments)


@dataclass(frozen=True, slots=True)
class LoadedEnvironment:
    path: Path
    api_key_present: bool


def load_project_environment(
    path: Path = DEFAULT_ENV_PATH,
    *,
    environment: Mapping[str, str] | None = None,
) -> LoadedEnvironment:
    """Load the one project .env without overriding an existing shell value."""

    resolved = Path(path).resolve()
    if environment is None:
        load_dotenv(dotenv_path=resolved, override=False)
        values = os.environ
    else:
        values = environment
    return LoadedEnvironment(
        path=resolved,
        api_key_present=bool(values.get("DASHSCOPE_API_KEY", "").strip()),
    )


class DashScopeQwenEmbeddingProvider:
    provider_id = QWEN_PROVIDER_ID
    model_id = QWEN_MODEL_ID

    def __init__(
        self,
        *,
        dimensions: int = QWEN_DIMENSIONS,
        api_key_env: str = "DASHSCOPE_API_KEY",
        env_path: Path = DEFAULT_ENV_PATH,
        sdk_client: DashScopeSdkClient | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if dimensions not in _ALLOWED_DIMENSIONS:
            raise ValueError("unsupported qwen3.7 embedding dimension")
        if api_key_env != "DASHSCOPE_API_KEY":
            raise ValueError("v1 uses one unified DASHSCOPE_API_KEY")
        self.dimensions = dimensions
        self.api_key_env = api_key_env
        self.env_path = Path(env_path).resolve()
        self._sdk = sdk_client or OfficialDashScopeSdkClient()
        self._environment = environment

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """EmbeddingProvider compatibility: candidates are retrievable documents."""

        return self.embed_documents(texts)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, text_type="document", instruct=None)

    def embed_queries(
        self,
        texts: Sequence[str],
        *,
        instruct: str = DEFAULT_QUERY_INSTRUCTION,
    ) -> list[list[float]]:
        if not instruct.strip():
            raise ValueError("query instruction must not be empty")
        return self._embed(texts, text_type="query", instruct=instruct.strip())

    def embed_symmetric(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, text_type="document", instruct=None)

    def _embed(
        self,
        texts: Sequence[str],
        *,
        text_type: str,
        instruct: str | None,
    ) -> list[list[float]]:
        values = tuple(texts)
        if any(not isinstance(text, str) or not text.strip() for text in values):
            raise ValueError("embedding inputs must be non-empty strings")
        if not values:
            return []
        loaded = load_project_environment(
            self.env_path,
            environment=self._environment,
        )
        if not loaded.api_key_present:
            raise EmbeddingProviderError(
                EmbeddingErrorKind.MISSING_API_KEY,
                f"fill DASHSCOPE_API_KEY in {loaded.path}",
            )
        if isinstance(self._sdk, OfficialDashScopeSdkClient):
            # dashscope caches DASHSCOPE_API_KEY at import time. dotenv is loaded
            # later, so synchronize the same single key before the SDK call.
            key_source = self._environment if self._environment is not None else os.environ
            dashscope.api_key = key_source[self.api_key_env].strip()
        result: list[list[float]] = []
        for start in range(0, len(values), _MAX_BATCH_SIZE):
            batch = values[start : start + _MAX_BATCH_SIZE]
            try:
                response = self._sdk.call(
                    model=self.model_id,
                    input=list(batch),
                    text_type=text_type,
                    dimension=self.dimensions,
                    output_type="dense",
                    instruct=instruct,
                )
            except Exception as error:
                raise EmbeddingProviderError(
                    EmbeddingErrorKind.NETWORK,
                    "DashScope SDK embedding call failed",
                ) from error
            result.extend(self._parse_response(response, len(batch)))
        return result

    def _parse_response(self, response: Any, expected_count: int) -> list[list[float]]:
        status_code = _response_value(response, "status_code")
        if isinstance(status_code, bool) or not isinstance(status_code, int):
            raise _invalid_response("DashScope SDK response has no HTTP status")
        if status_code != 200:
            raise _service_error(response, status_code)
        output = _response_value(response, "output")
        entries = _mapping_value(output, "embeddings")
        if not isinstance(entries, list) or len(entries) != expected_count:
            raise _invalid_response("DashScope embedding count did not match request")
        ordered: list[list[float] | None] = [None] * expected_count
        for entry in entries:
            index = _mapping_value(entry, "text_index")
            vector = _mapping_value(entry, "embedding")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < expected_count
                or ordered[index] is not None
                or not isinstance(vector, list)
                or len(vector) != self.dimensions
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in vector
                )
            ):
                raise _invalid_response("DashScope returned an invalid dense vector")
            ordered[index] = _l2_normalize([float(value) for value in vector])
        if any(vector is None for vector in ordered):
            raise _invalid_response("DashScope response text_index values were incomplete")
        return [vector for vector in ordered if vector is not None]


def _response_value(response: Any, key: str) -> Any:
    if isinstance(response, Mapping):
        return response.get(key)
    return getattr(response, key, None)


def _mapping_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _service_error(response: Any, status_code: int) -> EmbeddingProviderError:
    code = str(_response_value(response, "code") or "")
    if status_code in {401, 403} or code in {"InvalidApiKey", "AccessDenied"}:
        kind = EmbeddingErrorKind.AUTHENTICATION
    elif status_code == 429 or "rate" in code.casefold():
        kind = EmbeddingErrorKind.RATE_LIMITED
    else:
        kind = EmbeddingErrorKind.SERVICE
    return EmbeddingProviderError(
        kind,
        f"DashScope embedding request failed with HTTP {status_code}",
        status_code=status_code,
    )


def _invalid_response(message: str) -> EmbeddingProviderError:
    return EmbeddingProviderError(EmbeddingErrorKind.INVALID_RESPONSE, message)


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise _invalid_response("DashScope returned a zero or non-finite dense vector")
    return [value / norm for value in vector]
