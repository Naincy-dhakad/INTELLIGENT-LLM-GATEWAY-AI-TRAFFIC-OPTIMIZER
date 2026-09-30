from dataclasses import FrozenInstanceError, dataclass, field, fields, is_dataclass
import time

import pytest
from gateway.api.schemas import ChatRequest
from gateway.application.chat_service import ChatService
from gateway.application.chat_service import ChatExecutionResult
from gateway.application.context import RequestContext
from gateway.domain.classification import ClassificationResult, ComplexityLevel, RequestCategory
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
from gateway.domain.execution import AttemptOutcome, AttemptRole, ExecutionExplanation
from gateway.domain.routing import DeterministicRoutingPolicy, RoutingCandidate, RoutingExplanation, RoutingRequest


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


class SpyRoutingPolicy:
    def __init__(self, decision):
        self._decision = decision
        self.calls = 0

    def route(self, routing_request, candidates, *, default_provider_id=None):
        self.calls += 1
        return self._decision

    def fallback_options(self, routing_request, candidates, *, default_provider_id=None, attempted=frozenset()):
        return (
            (
                RoutingCandidate(
                    provider_id="fallback",
                    provider_name="fallback",
                    capabilities=frozenset({Capability.TEXT_GENERATION}),
                    model_ids=("model",),
                    supports_streaming=False,
                ),
                "model",
            ),
        )


def make_decision(provider_id: str = "primary", model_id: str = "model"):
    candidate = RoutingCandidate(
        provider_id=provider_id,
        provider_name=provider_id,
        capabilities=frozenset({Capability.TEXT_GENERATION}),
        model_ids=(model_id,),
        supports_streaming=False,
    )
    return DeterministicRoutingPolicy().route(
        RoutingRequest(None, None, frozenset({Capability.TEXT_GENERATION}), "balanced"),
        (candidate,),
        default_provider_id=provider_id,
    )


def test_successful_first_attempt_has_no_retry_or_fallback():
    decision = make_decision()
    policy = SpyRoutingPolicy(decision)
    primary = FakeProvider("primary")
    result = service(primary, routing_policy=policy).complete(request(), context())
    assert policy.calls == 1
    assert result.routing_trace is decision.trace
    assert result.routing_trace is result.routing_decision.trace
    assert result.routing_explanation is not None
    assert result.routing_explanation.trace is decision.trace
    assert result.routing_explanation.selection is not None
    assert result.routing_explanation.selection.reason == result.routing_decision.reason
    assert result.provider_response.provider_id == result.routing_decision.selected_provider_id == "primary"
    assert result.provider_response.model == result.routing_decision.selected_model_id == "model"
    assert result.execution_explanation is not None
    assert result.execution_explanation.initial_provider_id == "primary"
    assert result.execution_explanation.initial_model_id == "model"
    assert len(result.execution_explanation.attempts) == 1
    attempt = result.execution_explanation.attempts[0]
    assert (attempt.provider_id, attempt.model_id, attempt.attempt_number, attempt.attempt_role, attempt.outcome) == (
        "primary", "model", 1, AttemptRole.INITIAL, AttemptOutcome.SUCCESS
    )
    assert attempt.error_category is None
    assert result.execution_explanation is not result.routing_explanation
    assert result.routing_decision is decision
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


def test_retry_preserves_the_same_routing_explanation_and_trace_object(monkeypatch):
    decision = make_decision()
    policy = SpyRoutingPolicy(decision)
    primary = FakeProvider("primary", [error(ProviderErrorCategory.UNAVAILABLE), "success"])
    explanations = []
    original_from_trace = RoutingExplanation.from_trace

    def capture_explanation(trace, **kwargs):
        result = original_from_trace(trace, **kwargs)
        explanations.append(result)
        return result

    monkeypatch.setattr(RoutingExplanation, "from_trace", staticmethod(capture_explanation))
    result = service(primary, routing_policy=policy).complete(request(), context())
    assert policy.calls == 1
    assert len(explanations) == 1
    assert result.routing_explanation is explanations[0]
    assert result.routing_trace is decision.trace
    assert result.routing_trace is result.routing_decision.trace
    assert result.routing_explanation is not None
    assert result.routing_explanation.trace is decision.trace
    assert result.routing_explanation.selection is not None
    assert result.routing_explanation.selection.reason == result.routing_decision.reason
    assert result.attempt_count == 2
    assert result.provider_response.provider_id == decision.selected_provider_id
    assert result.provider_response.model == decision.selected_model_id
    attempts = result.execution_explanation.attempts
    assert [(item.provider_id, item.model_id, item.attempt_number, item.attempt_role, item.outcome, item.error_category) for item in attempts] == [
        ("primary", "model", 1, AttemptRole.INITIAL, AttemptOutcome.FAILURE, ProviderErrorCategory.UNAVAILABLE),
        ("primary", "model", 2, AttemptRole.RETRY, AttemptOutcome.SUCCESS, None),
    ]
    assert result.routing_decision is decision


def test_fallback_preserves_the_same_routing_explanation_and_trace_object(monkeypatch):
    decision = make_decision()
    policy = SpyRoutingPolicy(decision)
    primary = FakeProvider("primary", [error(ProviderErrorCategory.UNAVAILABLE), error(ProviderErrorCategory.TIMEOUT)])
    fallback = FakeProvider("fallback")
    explanations = []
    original_from_trace = RoutingExplanation.from_trace

    def capture_explanation(trace, **kwargs):
        result = original_from_trace(trace, **kwargs)
        explanations.append(result)
        return result

    monkeypatch.setattr(RoutingExplanation, "from_trace", staticmethod(capture_explanation))
    result = service(primary, fallback, routing_policy=policy).complete(request(), context())
    assert policy.calls == 1
    assert len(explanations) == 1
    assert result.routing_explanation is explanations[0]
    assert result.routing_trace is decision.trace
    assert result.routing_trace is result.routing_decision.trace
    assert result.routing_explanation is not None
    assert result.routing_explanation.trace is decision.trace
    assert result.routing_explanation.selection is not None
    assert result.routing_explanation.selection.reason == result.routing_decision.reason
    assert result.fallback_used is True
    assert result.provider_response.provider_id == fallback.provider_id
    assert result.provider_response.model == "model"
    assert [(item.provider_id, item.model_id, item.attempt_number, item.attempt_role, item.outcome, item.error_category) for item in result.execution_explanation.attempts] == [
        ("primary", "model", 1, AttemptRole.INITIAL, AttemptOutcome.FAILURE, ProviderErrorCategory.UNAVAILABLE),
        ("primary", "model", 2, AttemptRole.RETRY, AttemptOutcome.FAILURE, ProviderErrorCategory.TIMEOUT),
        ("fallback", "model", 3, AttemptRole.FALLBACK, AttemptOutcome.SUCCESS, None),
    ]
    assert result.routing_decision is decision


def test_retry_exhaustion_keeps_normalized_failure_without_creating_another_route():
    decision = make_decision()
    policy = SpyRoutingPolicy(decision)
    primary = FakeProvider(
        "primary",
        [error(ProviderErrorCategory.UNAVAILABLE), error(ProviderErrorCategory.TIMEOUT)],
    )

    with pytest.raises(ProviderError) as raised:
        service(primary, routing_policy=policy).complete(request(provider="primary"), context())

    assert raised.value.category is ProviderErrorCategory.TIMEOUT
    assert policy.calls == 1
    assert len(primary.calls) == 2


def test_fallback_failure_keeps_normalized_failure_without_creating_another_route():
    decision = make_decision()
    policy = SpyRoutingPolicy(decision)
    primary = FakeProvider(
        "primary",
        [error(ProviderErrorCategory.UNAVAILABLE), error(ProviderErrorCategory.TIMEOUT)],
    )
    fallback = FakeProvider("fallback", [error(ProviderErrorCategory.RATE_LIMIT)])

    with pytest.raises(ProviderError) as raised:
        service(primary, fallback, routing_policy=policy).complete(request(), context())

    assert raised.value.category is ProviderErrorCategory.RATE_LIMIT
    assert policy.calls == 1
    assert len(primary.calls) == 2
    assert len(fallback.calls) == 1


def test_execution_explanation_contains_only_normalized_immutable_attempt_data():
    primary = FakeProvider("primary", [error(ProviderErrorCategory.UNAVAILABLE), "success"])
    result = service(primary).complete(request(), context())
    execution = result.execution_explanation
    assert isinstance(execution, ExecutionExplanation)
    assert isinstance(execution.attempts, tuple)
    assert all(is_dataclass(item) for item in execution.attempts)
    forbidden_fields = {
        "prompt", "completion", "api_key", "authorization", "credentials", "url",
        "principal_id", "user_id", "raw_error", "raw_provider_error", "metadata",
    }
    nested_fields = {
        item.name
        for value in (execution, *execution.attempts)
        for item in fields(value)
    }
    assert nested_fields.isdisjoint(forbidden_fields)
    assert all("safe normalized failure" not in repr(item) for item in execution.attempts)
    with pytest.raises(FrozenInstanceError):
        execution.initial_provider_id = "changed"
    with pytest.raises(TypeError):
        ExecutionExplanation("primary", "model", list(execution.attempts))


def test_chat_execution_result_keeps_backward_compatible_default():
    decision = make_decision()
    result = ChatExecutionResult(
        provider_response=ProviderChatResponse(
            message={"role": "assistant", "content": "ok"},
            provider_id="primary",
            model="model",
            finish_reason="stop",
        ),
        routing_decision=decision,
        classification=ClassificationResult(RequestCategory.UNKNOWN, ComplexityLevel.LOW, 0, (), "safe"),
    )
    assert result.execution_explanation is None


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
