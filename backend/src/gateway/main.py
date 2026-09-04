from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from gateway.api.chat import router as chat_router
from gateway.api.errors import (
    GatewayAPIError,
    gateway_error_handler,
    internal_error_handler,
    validation_error_handler,
)
from gateway.api.health import router as health_router
from gateway.api.middleware import RequestIDMiddleware
from gateway.application.chat_service import ChatService
from gateway.config.settings import get_settings
from gateway.domain.provider_registry import ProviderRegistry
from gateway.infrastructure.providers.anthropic import AnthropicProvider
from gateway.infrastructure.providers.gemini import GeminiProvider
from gateway.infrastructure.providers.mock import MockProvider
from gateway.infrastructure.providers.ollama import OllamaProvider
from gateway.infrastructure.providers.openai import OpenAIProvider


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(GatewayAPIError, gateway_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, internal_error_handler)

    providers = [
        MockProvider(
            input_usd_per_million_tokens=settings.phase3_mock_input_usd_per_million_tokens,
            output_usd_per_million_tokens=settings.phase3_mock_output_usd_per_million_tokens,
            estimated_latency_ms=settings.phase3_mock_latency_ms,
        )
    ]
    if settings.openai_api_key:
        providers.append(
            OpenAIProvider(
                api_key=settings.openai_api_key.get_secret_value(),
                default_model=settings.openai_default_model,
            )
        )
    if settings.anthropic_api_key:
        providers.append(
            AnthropicProvider(
                api_key=settings.anthropic_api_key.get_secret_value(),
                default_model=settings.anthropic_default_model,
            )
        )
    if settings.gemini_api_key:
        providers.append(
            GeminiProvider(
                api_key=settings.gemini_api_key.get_secret_value(),
                default_model=settings.gemini_default_model,
            )
        )
    if settings.ollama_base_url:
        providers.append(
            OllamaProvider(
                base_url=settings.ollama_base_url,
                default_model=settings.ollama_default_model,
            )
        )

    registry = ProviderRegistry(
        providers=tuple(providers),
        default_provider_id=settings.default_provider_id,
    )
    if registry.default() is None:
        raise ValueError(
            "DEFAULT_PROVIDER_ID must identify a provider with valid configuration"
        )
    app.state.chat_service = ChatService(registry)
    app.include_router(health_router)
    app.include_router(chat_router)
    return app


app = create_app()
