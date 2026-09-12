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


def test_retry_scheduled_event_uses_existing_delay_and_reason():
    collector = Collector()
    provider = FakeProvider("primary", [error(ProviderErrorCategory.UNAVAILABLE), "success"])
    result = make_service(provider, collector=collector).complete(request(), context())
    retries = [e for e in collector.events if e.event_type.value == "gateway_retry_scheduled"]
    assert result.attempt_count == 2
    assert len(retries) == 1
    assert retries[0].request_id == "provider-request"
    assert retries[0].attributes == {
        "provider_id": "primary",
        "model_id": "model",
        "attempt_number": 1,
        "error_category": "unavailable",
        "delay_ms": 50,
    }


def test_non_retryable_failure_does_not_emit_retry_event():
    collector = Collector()
    provider = FakeProvider("primary", [error(ProviderErrorCategory.INVALID_REQUEST)])
    with pytest.raises(ProviderError):
        make_service(provider, collector=collector).complete(request(), context())
    assert not [e for e in collector.events if e.event_type.value == "gateway_retry_scheduled"]


def test_fallback_selected_event_precedes_fallback_attempt():
    collector = Collector()
    primary = FakeProvider("primary", [error(ProviderErrorCategory.UNAVAILABLE), error(ProviderErrorCategory.TIMEOUT)])
    fallback = FakeProvider("fallback")
    result = make_service(primary, fallback, collector).complete(request(), context())
    fallback_events = [e for e in collector.events if e.event_type.value == "gateway_fallback_selected"]
    attempt_events = [e for e in collector.events if e.event_type.value == "gateway_provider_attempt"]
    assert result.fallback_used is True
    assert len(fallback_events) == 1
    assert fallback_events[0].request_id == "provider-request"
    assert fallback_events[0].attributes["to_provider_id"] == "fallback"
    assert fallback_events[0].attributes["to_model_id"] == "model"
    assert fallback_events[0].attributes["attempt_number"] == 3
    assert fallback_events[0].attributes["reason_code"] == "timeout"
    assert collector.events.index(fallback_events[0]) < collector.events.index(attempt_events[-1])


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
