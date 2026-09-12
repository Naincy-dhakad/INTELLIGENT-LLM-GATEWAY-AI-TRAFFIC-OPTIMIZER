from dataclasses import dataclass, field
import time

import pytest

from gateway.api.schemas import ChatRequest
from gateway.application.chat_service import ChatService
from gateway.application.context import RequestContext
from gateway.application.observability_events import ObservabilityEvent
from gateway.domain.provider import (
    Capability,
    HealthStatus,
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorCategory,
    ProviderHealth,
    ProviderMetadata,
)
from gateway.domain.provider_registry import ProviderRegistry


@dataclass
class Collector:
    events: list[ObservabilityEvent] = field(default_factory=list)
    fail: bool = False

    def emit(self, event):
        if self.fail:
            raise RuntimeError("sink failure")
        self.events.append(event)

    def increment(self, *args, **kwargs):
        pass

    def observe(self, *args, **kwargs):
        pass


@dataclass
class FakeProvider:
    provider_id: str
    outcomes: list[object] = field(default_factory=list)
    calls: list[ProviderChatRequest] = field(default_factory=list)

    @property
    def metadata(self):
        return ProviderMetadata(
            id=self.provider_id,
            name=self.provider_id,
            capabilities=frozenset({Capability.TEXT_GENERATION}),
            model_ids=("model",),
            health=(ProviderHealth(model_id="model", health_score=100, status=HealthStatus.HEALTHY),),
        )

    def chat(self, request):
        self.calls.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if isinstance(outcome, ProviderError):
            raise outcome
        return ProviderChatResponse(
            message={"role": "assistant", "content": "safe response"},
            provider_id=self.provider_id,
            model=request.model or "model",
            finish_reason="stop",
        )


def request(**kwargs):
    return ChatRequest(messages=[{"role": "user", "content": "private prompt"}], **kwargs)


def context():
    return RequestContext("provider-request", 10_000, time.monotonic() + 10)


def error(category):
    return ProviderError(category=category, message="raw adapter detail must not be emitted")


def attempt_events(collector):
    return [e for e in collector.events if e.event_type.value == "gateway_provider_attempt"]


def make_service(primary, fallback=None, collector=None):
    providers = (primary,) if fallback is None else (primary, fallback)
    return ChatService(
        ProviderRegistry(providers, primary.provider_id),
        observability=collector or Collector(),
        sleeper=lambda _: None,
    )


def test_successful_provider_attempt_emits_one_safe_event():
    collector = Collector()
    provider = FakeProvider("primary")
    result = make_service(provider, collector=collector).complete(request(), context())
    event = attempt_events(collector)[0]
    assert result.attempt_count == 1
    assert event.request_id == "provider-request"
    assert event.attributes["provider_id"] == "primary"
    assert event.attributes["model_id"] == "model"
    assert event.attributes["attempt_number"] == 1
    assert event.attributes["attempt_role"] == "initial"
    assert event.attributes["outcome"] == "success"
    assert event.attributes["latency_ms"] >= 0


def test_provider_failure_emits_normalized_error_only():
    collector = Collector()
    provider = FakeProvider("primary", [error(ProviderErrorCategory.INVALID_REQUEST)])
    with pytest.raises(ProviderError):
        make_service(provider, collector=collector).complete(request(), context())
    event = attempt_events(collector)[0]
    assert event.attributes["outcome"] == "failure"
    assert event.attributes["error_category"] == "invalid_request"
    assert "raw adapter detail" not in str(event.as_dict())


def test_retry_and_fallback_emit_sequential_attempts():
    collector = Collector()
    primary = FakeProvider("primary", [error(ProviderErrorCategory.UNAVAILABLE), error(ProviderErrorCategory.TIMEOUT)])
    fallback = FakeProvider("fallback")
    result = make_service(primary, fallback, collector).complete(request(), context())
    events = attempt_events(collector)
    assert result.fallback_used is True
    assert [e.attributes["attempt_number"] for e in events] == [1, 2, 3]
    assert [e.attributes["attempt_role"] for e in events] == ["initial", "retry", "fallback"]
    assert events[-1].attributes["provider_id"] == "fallback"
    assert events[-1].attributes["outcome"] == "success"
    assert all(e.request_id == "provider-request" for e in events)


def test_provider_attempt_sink_failure_does_not_change_execution():
    collector = Collector(fail=True)
    primary = FakeProvider("primary", [error(ProviderErrorCategory.UNAVAILABLE), "success"])
    result = make_service(primary, collector=collector).complete(request(), context())
    assert result.attempt_count == 2
    assert len(primary.calls) == 2
