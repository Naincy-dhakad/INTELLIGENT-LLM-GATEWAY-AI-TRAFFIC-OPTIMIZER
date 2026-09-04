import pytest
from pydantic import ValidationError

from gateway.domain.health import health_for_model
from gateway.domain.provider import Capability, HealthStatus, ProviderHealth
from gateway.domain.routing import DeterministicRoutingPolicy, RoutingCandidate, RoutingError, RoutingErrorCategory, RoutingRequest


def test_health_model_is_bounded_and_immutable():
    health = ProviderHealth(model_id="model", health_score=80, status=HealthStatus.HEALTHY)
    assert health.health_score == 80
    with pytest.raises(ValidationError):
        ProviderHealth(model_id="model", health_score=-1, status=HealthStatus.DEGRADED)
    with pytest.raises(ValidationError):
        ProviderHealth(model_id="model", health_score=101, status=HealthStatus.HEALTHY)
    with pytest.raises(ValidationError):
        ProviderHealth(model_id="model", health_score=None, status=HealthStatus.HEALTHY)
    with pytest.raises(ValidationError):
        ProviderHealth(model_id="model", health_score=50, status=HealthStatus.UNKNOWN)


def test_unknown_health_is_explicit_and_deterministic():
    assert health_for_model((), "model") == health_for_model((), "model")
    assert health_for_model((), "model").status is HealthStatus.UNKNOWN
    assert health_for_model((), "model").health_score is None


def health_candidate(provider_id: str, score: int | None, status: HealthStatus | None = None, model_id: str = "model"):
    if status is None and score is not None:
        status = HealthStatus.HEALTHY
    health = (ProviderHealth(model_id=model_id, health_score=score, status=status or HealthStatus.UNKNOWN),) if score is not None else ()
    return RoutingCandidate(provider_id, provider_id, frozenset({Capability.TEXT_GENERATION}), (model_id,), False, health=health)


def quality_request(**overrides):
    values = {"requested_provider_id": None, "requested_model_id": None, "required_capabilities": frozenset({Capability.TEXT_GENERATION}), "objective": "quality"}
    values.update(overrides)
    return RoutingRequest(**values)


def test_quality_selects_highest_health_and_ties_deterministically():
    policy = DeterministicRoutingPolicy()
    decision = policy.route(quality_request(), (health_candidate("low", 40, HealthStatus.DEGRADED), health_candidate("high", 90)), default_provider_id=None)
    assert decision.selected_provider_id == "high"
    assert decision.health_score == 90
    tie = policy.route(quality_request(), (health_candidate("zeta", 90), health_candidate("alpha", 90)), default_provider_id=None)
    assert tie.selected_provider_id == "alpha"


def test_unavailable_and_unknown_health_never_win_quality():
    policy = DeterministicRoutingPolicy()
    decision = policy.route(quality_request(), (health_candidate("unknown", None), health_candidate("healthy", 1)), default_provider_id=None)
    assert decision.selected_provider_id == "healthy"
    with pytest.raises(RoutingError) as raised:
        policy.route(quality_request(), (health_candidate("unknown", None),), default_provider_id=None)
    assert raised.value.category is RoutingErrorCategory.QUALITY_UNAVAILABLE
    with pytest.raises(RoutingError) as raised:
        policy.route(quality_request(requested_provider_id="down"), (health_candidate("down", 0, HealthStatus.UNAVAILABLE),), default_provider_id=None)
    assert raised.value.category is RoutingErrorCategory.PROVIDER_UNHEALTHY


def test_status_values_are_normalized():
    assert ProviderHealth(model_id="m", health_score=100, status="healthy").status is HealthStatus.HEALTHY
    assert ProviderHealth(model_id="m", health_score=40, status="degraded").status is HealthStatus.DEGRADED
    assert ProviderHealth(model_id="m", health_score=0, status="unavailable").status is HealthStatus.UNAVAILABLE
