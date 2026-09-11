from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from gateway.api.authentication import GatewayAuthenticator
from gateway.api.chat import router as chat_router
from gateway.api.errors import (
    GatewayAPIError,
    gateway_error_handler,
    internal_error_handler,
    validation_error_handler,
)
from gateway.api.health import router as health_router
from gateway.api.middleware import RequestIDMiddleware
from gateway.application.budget import BudgetService
from gateway.application.chat_service import ChatService
from gateway.application.observability import NoopObservability, ObservabilityPort
from gateway.application.rate_limiting import DisabledRateLimiter
from gateway.application.usage_tracking import UsageRecorder
from gateway.config.settings import get_settings
from gateway.domain.provider_registry import ProviderRegistry
from gateway.infrastructure.providers.anthropic import AnthropicProvider
from gateway.infrastructure.providers.gemini import GeminiProvider
from gateway.infrastructure.providers.mock import MockProvider
from gateway.infrastructure.providers.ollama import OllamaProvider
from gateway.infrastructure.providers.openai import OpenAIProvider
from gateway.infrastructure.rate_limiter import RedisRateLimiter


class _UnavailableUsageRepository:
    def record_usage(self, _record) -> None:
        raise RuntimeError("usage repository unavailable")


def create_app(observability: ObservabilityPort | None = None) -> FastAPI:
    settings = get_settings()
    observability = observability or NoopObservability()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.observability = observability
    app.state.gateway_authenticator = GatewayAuthenticator(
        settings.gateway_auth_enabled, settings.gateway_api_keys
    )
    app.state.rate_limit_enabled = settings.rate_limit_enabled
    app.state.rate_limit_requests = settings.rate_limit_requests
    app.state.rate_limit_window_seconds = settings.rate_limit_window_seconds
    app.state.rate_limiter = (
        RedisRateLimiter(settings.redis_url)
        if settings.rate_limit_enabled
        else DisabledRateLimiter()
    )
    usage_repository = None
    if settings.usage_tracking_enabled:
        try:
            from gateway.infrastructure.database.repositories import SqlAlchemyUsageRecordRepository
            from gateway.infrastructure.database.session import create_session_factory

            usage_repository = SqlAlchemyUsageRecordRepository(
                create_session_factory(settings.database_url)
            )
        except Exception:
            usage_repository = _UnavailableUsageRepository()
    app.state.usage_recorder = UsageRecorder(
        usage_repository, settings.usage_tracking_enabled
    )
    app.state.budget_service = BudgetService(
        usage_repository, settings.usage_tracking_enabled
    )
    app.add_middleware(
        RequestIDMiddleware,
        observability=observability,
        environment=settings.app_env,
    )
    app.add_exception_handler(GatewayAPIError, gateway_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, internal_error_handler)

    providers = [
        MockProvider(
            input_usd_per_million_tokens=settings.phase3_mock_input_usd_per_million_tokens,
            output_usd_per_million_tokens=settings.phase3_mock_output_usd_per_million_tokens,
            estimated_latency_ms=settings.phase3_mock_latency_ms,
            health_score=settings.phase3_mock_health_score,
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
    app.state.chat_service = ChatService(registry, observability=observability)
    app.include_router(health_router)
    app.include_router(chat_router)
    return app


app = create_app()
