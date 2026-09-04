"""Ollama local HTTP adapter using the existing httpx dependency."""

import time
from typing import Any
from urllib.parse import urlparse

import httpx

from gateway.domain.provider import (
    Capability,
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorCategory,
    ProviderMessage,
    ProviderMetadata,
    ProviderUsage,
)


class OllamaConfigurationError(ValueError):
    """Raised when the adapter cannot be safely configured."""


class OllamaProvider:
    """Translates normalized requests to Ollama's local `/api/chat` endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        default_model: str,
        client: Any | None = None,
    ) -> None:
        if not base_url or urlparse(base_url).scheme not in {"http", "https"}:
            raise OllamaConfigurationError(
                "OLLAMA_BASE_URL must be an http or https URL"
            )
        if not default_model or not default_model.strip():
            raise OllamaConfigurationError("OLLAMA_DEFAULT_MODEL must not be empty")
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._client = client or httpx.Client()
        self._metadata = ProviderMetadata(
            id="ollama",
            name="Ollama",
            capabilities=frozenset({Capability.TEXT_GENERATION}),
            model_ids=(default_model,),
            supports_streaming=False,
        )

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    def chat(self, request: ProviderChatRequest) -> ProviderChatResponse:
        remaining_seconds = request.deadline_monotonic - time.perf_counter()
        if remaining_seconds <= 0:
            raise ProviderError(
                category=ProviderErrorCategory.TIMEOUT,
                message="The provider deadline expired before the Ollama request started.",
            )

        options: dict[str, Any] = {}
        if request.generation:
            if request.generation.max_output_tokens is not None:
                options["num_predict"] = request.generation.max_output_tokens
            if request.generation.temperature is not None:
                options["temperature"] = request.generation.temperature
            if request.generation.top_p is not None:
                options["top_p"] = request.generation.top_p
        payload: dict[str, Any] = {
            "model": request.model or self._default_model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "stream": False,
        }
        if options:
            payload["options"] = options

        try:
            response = self._client.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=max(0.001, remaining_seconds),
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as error:
            raise ProviderError(
                category=ProviderErrorCategory.TIMEOUT,
                message="Ollama did not respond before the provider deadline.",
            ) from error
        except httpx.HTTPStatusError as error:
            raise self._status_error(error.response.status_code) from error
        except httpx.RequestError as error:
            raise ProviderError(
                category=ProviderErrorCategory.UNAVAILABLE,
                message="Ollama is unavailable.",
            ) from error
        except (TypeError, ValueError) as error:
            raise ProviderError(
                category=ProviderErrorCategory.PROVIDER_ERROR,
                message="Ollama returned an invalid response.",
            ) from error

        try:
            content = data["message"]["content"]
            if not content:
                raise ValueError("empty response content")
            usage = self._usage(data)
            return ProviderChatResponse(
                message=ProviderMessage(role="assistant", content=content),
                provider_id=self.metadata.id,
                model=data.get("model") or payload["model"],
                finish_reason=data.get("done_reason") or "unknown",
                usage=usage,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderError(
                category=ProviderErrorCategory.PROVIDER_ERROR,
                message="Ollama returned an invalid response.",
            ) from error

    @staticmethod
    def _status_error(status_code: int) -> ProviderError:
        if status_code in {400, 422}:
            category = ProviderErrorCategory.INVALID_REQUEST
        elif status_code in {404, 408, 429} or status_code >= 500:
            category = ProviderErrorCategory.UNAVAILABLE
        else:
            category = ProviderErrorCategory.PROVIDER_ERROR
        return ProviderError(
            category=category,
            message="Ollama could not complete the request.",
        )

    @staticmethod
    def _usage(data: dict[str, Any]) -> ProviderUsage | None:
        input_tokens = data.get("prompt_eval_count")
        output_tokens = data.get("eval_count")
        if input_tokens is None and output_tokens is None:
            return None
        total_tokens = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        )
        return ProviderUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
