"""OpenAI adapter; OpenAI SDK types remain inside this module."""

import time
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

from gateway.domain.provider import (
    Capability,
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorCategory,
    ProviderMetadata,
    ProviderMessage,
    ProviderUsage,
)


class OpenAIConfigurationError(ValueError):
    """Raised when the adapter cannot be safely configured."""


class OpenAIProvider:
    """Translates normalized provider calls to OpenAI Chat Completions."""

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        client: Any | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise OpenAIConfigurationError("OPENAI_API_KEY is required to enable OpenAI")
        if not default_model or not default_model.strip():
            raise OpenAIConfigurationError("OPENAI_DEFAULT_MODEL must not be empty")

        self._default_model = default_model
        self._client = client or OpenAI(api_key=api_key.strip())
        self._metadata = ProviderMetadata(
            id="openai",
            name="OpenAI",
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
                message="The provider deadline expired before the OpenAI request started.",
            )

        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ]
        parameters: dict[str, Any] = {
            "model": request.model or self._default_model,
            "messages": messages,
        }
        if request.generation:
            if request.generation.max_output_tokens is not None:
                parameters["max_completion_tokens"] = request.generation.max_output_tokens
            if request.generation.temperature is not None:
                parameters["temperature"] = request.generation.temperature
            if request.generation.top_p is not None:
                parameters["top_p"] = request.generation.top_p

        try:
            response = self._client.with_options(
                timeout=max(0.001, remaining_seconds)
            ).chat.completions.create(**parameters)
        except Exception as error:
            raise self._translate_error(error) from error

        try:
            choice = response.choices[0]
            content = choice.message.content
            if not content:
                raise ValueError("empty response content")
            usage = self._usage(response.usage)
            return ProviderChatResponse(
                message=ProviderMessage(role="assistant", content=content),
                provider_id=self.metadata.id,
                model=response.model or parameters["model"],
                finish_reason=choice.finish_reason or "unknown",
                usage=usage,
            )
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise ProviderError(
                category=ProviderErrorCategory.PROVIDER_ERROR,
                message="OpenAI returned an invalid response.",
            ) from error

    @staticmethod
    def _usage(usage: Any) -> ProviderUsage | None:
        if usage is None:
            return None
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if input_tokens is None and output_tokens is None and total_tokens is None:
            return None
        return ProviderUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    @staticmethod
    def _translate_error(error: Exception) -> ProviderError:
        if isinstance(error, AuthenticationError):
            category = ProviderErrorCategory.AUTHENTICATION_FAILURE
        elif isinstance(error, RateLimitError):
            category = ProviderErrorCategory.RATE_LIMIT
        elif isinstance(error, BadRequestError):
            category = ProviderErrorCategory.INVALID_REQUEST
        elif isinstance(error, APITimeoutError) or isinstance(error, TimeoutError):
            category = ProviderErrorCategory.TIMEOUT
        elif isinstance(error, (APIConnectionError, APIStatusError)):
            category = ProviderErrorCategory.UNAVAILABLE
        else:
            category = ProviderErrorCategory.UNKNOWN
        return ProviderError(
            category=category,
            message="OpenAI could not complete the request.",
        )
