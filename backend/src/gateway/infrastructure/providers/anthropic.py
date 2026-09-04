"""Anthropic adapter; Anthropic SDK types remain inside this module."""

import time
from typing import Any

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    Anthropic,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

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


class AnthropicConfigurationError(ValueError):
    """Raised when the adapter cannot be safely configured."""


class AnthropicProvider:
    """Translates normalized chat requests to Anthropic Messages API."""

    DEFAULT_MAX_TOKENS = 1024

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        client: Any | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise AnthropicConfigurationError(
                "ANTHROPIC_API_KEY is required to enable Anthropic"
            )
        if not default_model or not default_model.strip():
            raise AnthropicConfigurationError(
                "ANTHROPIC_DEFAULT_MODEL must not be empty"
            )
        self._default_model = default_model
        self._client = client or Anthropic(api_key=api_key.strip())
        self._metadata = ProviderMetadata(
            id="anthropic",
            name="Anthropic",
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
                message="The provider deadline expired before the Anthropic request started.",
            )

        system_parts = [message.content for message in request.messages if message.role == "system"]
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]
        if not messages:
            raise ProviderError(
                category=ProviderErrorCategory.INVALID_REQUEST,
                message="Anthropic requires a non-system chat message.",
            )

        parameters: dict[str, Any] = {
            "model": request.model or self._default_model,
            "max_tokens": (
                request.generation.max_output_tokens
                if request.generation and request.generation.max_output_tokens is not None
                else self.DEFAULT_MAX_TOKENS
            ),
            "messages": messages,
        }
        if system_parts:
            parameters["system"] = "\n\n".join(system_parts)
        if request.generation:
            if request.generation.temperature is not None:
                parameters["temperature"] = request.generation.temperature
            if request.generation.top_p is not None:
                parameters["top_p"] = request.generation.top_p

        try:
            response = self._client.with_options(
                timeout=max(0.001, remaining_seconds)
            ).messages.create(**parameters)
        except Exception as error:
            raise self._translate_error(error) from error

        try:
            text_parts = [
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text" and getattr(block, "text", None)
            ]
            content = "\n".join(text_parts)
            if not content:
                raise ValueError("empty response content")
            return ProviderChatResponse(
                message=ProviderMessage(role="assistant", content=content),
                provider_id=self.metadata.id,
                model=response.model or parameters["model"],
                finish_reason=self._finish_reason(getattr(response, "stop_reason", None)),
                usage=self._usage(getattr(response, "usage", None)),
            )
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise ProviderError(
                category=ProviderErrorCategory.PROVIDER_ERROR,
                message="Anthropic returned an invalid response.",
            ) from error

    @staticmethod
    def _finish_reason(value: Any) -> str:
        return {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "tool_use": "tool_call",
        }.get(value, "unknown")

    @staticmethod
    def _usage(usage: Any) -> ProviderUsage | None:
        if usage is None:
            return None
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if input_tokens is None and output_tokens is None:
            return None
        total = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        )
        return ProviderUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
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
            message="Anthropic could not complete the request.",
        )
