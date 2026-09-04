"""Google Gemini adapter; Gemini SDK types remain inside this module."""

import time
from typing import Any

from google import genai
from google.genai import types

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


class GeminiConfigurationError(ValueError):
    """Raised when the adapter cannot be safely configured."""


class GeminiProvider:
    """Translates normalized chat requests to the official Google GenAI SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        client: Any | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is required to enable Gemini"
            )
        if not default_model or not default_model.strip():
            raise GeminiConfigurationError("GEMINI_DEFAULT_MODEL must not be empty")
        self._default_model = default_model
        self._client = client or genai.Client(api_key=api_key.strip())
        self._metadata = ProviderMetadata(
            id="gemini",
            name="Google Gemini",
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
                message="The provider deadline expired before the Gemini request started.",
            )

        contents = [
            types.Content(
                role="model" if message.role == "assistant" else "user",
                parts=[types.Part.from_text(text=message.content)],
            )
            for message in request.messages
            if message.role != "system"
        ]
        system_parts = [
            message.content for message in request.messages if message.role == "system"
        ]
        if not contents:
            raise ProviderError(
                category=ProviderErrorCategory.INVALID_REQUEST,
                message="Gemini requires a non-system chat message.",
            )

        config_kwargs: dict[str, Any] = {
            "http_options": types.HttpOptions(
                timeout=max(1, int(remaining_seconds * 1000))
            )
        }
        if system_parts:
            config_kwargs["system_instruction"] = "\n\n".join(system_parts)
        if request.generation:
            if request.generation.max_output_tokens is not None:
                config_kwargs["max_output_tokens"] = request.generation.max_output_tokens
            if request.generation.temperature is not None:
                config_kwargs["temperature"] = request.generation.temperature
            if request.generation.top_p is not None:
                config_kwargs["top_p"] = request.generation.top_p

        try:
            response = self._client.models.generate_content(
                model=request.model or self._default_model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as error:
            raise self._translate_error(error) from error

        try:
            content = response.text
            if not content:
                raise ValueError("empty response content")
            candidate = response.candidates[0] if response.candidates else None
            finish_reason = self._finish_reason(
                getattr(candidate, "finish_reason", None)
            )
            usage = self._usage(getattr(response, "usage_metadata", None))
            return ProviderChatResponse(
                message=ProviderMessage(role="assistant", content=content),
                provider_id=self.metadata.id,
                model=getattr(response, "model_version", None)
                or request.model
                or self._default_model,
                finish_reason=finish_reason,
                usage=usage,
            )
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise ProviderError(
                category=ProviderErrorCategory.PROVIDER_ERROR,
                message="Gemini returned an invalid response.",
            ) from error

    @staticmethod
    def _finish_reason(value: Any) -> str:
        normalized = str(value).lower().split(".")[-1]
        return {
            "stop": "stop",
            "max_tokens": "length",
            "safety": "content_filter",
            "recitation": "content_filter",
        }.get(normalized, "unknown")

    @staticmethod
    def _usage(usage: Any) -> ProviderUsage | None:
        if usage is None:
            return None
        input_tokens = getattr(usage, "prompt_token_count", None)
        output_tokens = getattr(usage, "candidates_token_count", None)
        total_tokens = getattr(usage, "total_token_count", None)
        if input_tokens is None and output_tokens is None and total_tokens is None:
            return None
        return ProviderUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    @staticmethod
    def _translate_error(error: Exception) -> ProviderError:
        status_code = getattr(error, "status_code", None)
        class_name = type(error).__name__.lower()
        if status_code in {401, 403} or "auth" in class_name:
            category = ProviderErrorCategory.AUTHENTICATION_FAILURE
        elif status_code == 429 or "rate" in class_name:
            category = ProviderErrorCategory.RATE_LIMIT
        elif status_code in {400, 422}:
            category = ProviderErrorCategory.INVALID_REQUEST
        elif isinstance(error, TimeoutError) or "timeout" in class_name:
            category = ProviderErrorCategory.TIMEOUT
        elif isinstance(status_code, int) and status_code >= 500:
            category = ProviderErrorCategory.UNAVAILABLE
        elif "connection" in class_name:
            category = ProviderErrorCategory.UNAVAILABLE
        else:
            category = ProviderErrorCategory.UNKNOWN
        return ProviderError(
            category=category,
            message="Gemini could not complete the request.",
        )
