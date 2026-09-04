from dataclasses import dataclass, field
import time

import pytest
from gateway.api.schemas import ChatRequest
from gateway.application.chat_service import ChatService
from gateway.application.context import RequestContext
from gateway.domain.provider import (
    Capability,
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorCategory,
    ProviderHealth,
    ProviderMetadata,
    HealthStatus,
)
from gateway.domain.provider_registry import ProviderRegistry


@dataclass
class FakeProvider:
    provider_id: str
    outcomes: list[object] = field(default_factory=list)
    health_score: int = 100
    calls: list[ProviderChatRequest] = field(default_factory=list)

    @property
    def metadata(self):
        return ProviderMetadata(
            id=self.provider_id,
            name=self.provider_id,
            capabilities=frozenset({Capability.TEXT_GENERATION}),
            model_ids=("model",),
            health=(ProviderHealth(model_id="model", health_score=self.health_score, status=HealthStatus.HEALTHY),),
        )

    def chat(self, request: ProviderChatRequest):
        self.calls.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if isinstance(outcome, ProviderError):
            raise outcome
        return ProviderChatResponse(
            message={"role": "assistant", "content": f"response from {self.provider_id}"},
            provider_id=self.provider_id,
            model=request.model or "model",
            finish_reason="stop",
        )


def error(category):
    return ProviderError(category=category, message="safe normalized failure")


def context(seconds=10):
    return RequestContext("req_test", 10_000, time.monotonic() + seconds)


def request(**kwargs):
    return ChatRequest(messages=[{"role": "user", "content": "hello"}], **kwargs)


def service(primary, fallback=None, **kwargs):
    providers = (primary,) if fallback is None else (primary, fallback)
    return ChatService(ProviderRegistry(providers, primary.provider_id), **kwargs)


def test_successful_first_attempt_has_no_retry_or_fallback():
    primary = FakeProvider("primary")
    result = service(primary).complete(request(), context())
    assert result.fallback_used is False
    assert result.attempt_count == 1
    assert len(primary.calls) == 1


@pytest.mark.parametrize("category", [ProviderErrorCategory.TIMEOUT, ProviderErrorCategory.UNAVAILABLE, ProviderErrorCategory.RATE_LIMIT])
def test_retryable_errors_retry_same_provider(category):
    primary = FakeProvider("primary", [error(category), "success"])
    result = service(primary).complete(request(), context())
    assert result.fallback_used is False
    assert result.attempt_count == 2
    assert len(primary.calls) == 2


@pytest.mark.parametrize("category", [ProviderErrorCategory.INVALID_REQUEST, ProviderErrorCategory.AUTHENTICATION_FAILURE, ProviderErrorCategory.UNSUPPORTED_CAPABILITY])
def test_non_retryable_errors_do_not_retry(category):
    primary = FakeProvider("primary", [error(category)])
    with pytest.raises(ProviderError):
        service(primary).complete(request(), context())
    assert len(primary.calls) == 1


def test_retry_and_fallback_are_bounded_and_fallback_is_deterministic():
    primary = FakeProvider("primary", [error(ProviderErrorCategory.UNAVAILABLE), error(ProviderErrorCategory.TIMEOUT)])
    fallback = FakeProvider("fallback")
    result = service(primary, fallback).complete(request(), context())
    assert result.fallback_used is True
    assert result.attempt_count == 3
    assert len(primary.calls) == 2
    assert len(fallback.calls) == 1


def test_explicit_provider_prevents_cross_provider_fallback():
    primary = FakeProvider("primary", [error(ProviderErrorCategory.UNAVAILABLE), error(ProviderErrorCategory.UNAVAILABLE)])
    fallback = FakeProvider("fallback")
    with pytest.raises(ProviderError):
        service(primary, fallback).complete(request(provider="primary"), context())
    assert len(primary.calls) == 2
    assert not fallback.calls


def test_deadline_is_shared_and_prevents_retry_or_fallback():
    primary = FakeProvider("primary", [error(ProviderErrorCategory.TIMEOUT)])
    fallback = FakeProvider("fallback")
    with pytest.raises(ProviderError) as raised:
        service(primary, fallback).complete(request(), context(seconds=-1))
    assert raised.value.category is ProviderErrorCategory.TIMEOUT
    assert not primary.calls
    assert not fallback.calls


def test_injected_sleep_is_bounded_and_request_timeout_shrinks():
    sleeps = []
    primary = FakeProvider("primary", [error(ProviderErrorCategory.TIMEOUT), "success"])
    result = service(primary, sleeper=sleeps.append).complete(request(), context())
    assert result.attempt_count == 2
    assert sleeps == [0.05]
    assert primary.calls[1].timeout_ms <= primary.calls[0].timeout_ms


