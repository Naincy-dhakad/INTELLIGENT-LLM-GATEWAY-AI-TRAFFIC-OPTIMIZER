from decimal import Decimal

import pytest
from pydantic import ValidationError

from gateway.domain.cost import TokenEstimate
from gateway.domain.latency import configured_latency_ms
from gateway.domain.provider import Capability, ModelLatency, ModelPricing
from gateway.domain.routing import (
    DeterministicRoutingPolicy,
    RoutingCandidate,
    RoutingError,
    RoutingErrorCategory,
    RoutingRequest,
)


def candidate(provider_id: str, latency: int | None, cost: str = "1", model: str = "model") -> RoutingCandidate:
    return RoutingCandidate(
        provider_id=provider_id,
        provider_name=provider_id,
        capabilities=frozenset({Capability.TEXT_GENERATION}),
        model_ids=(model,),
        supports_streaming=False,
        pricing=(ModelPricing(model_id=model, input_usd_per_million_tokens=Decimal(cost), output_usd_per_million_tokens=Decimal(cost)),),
        latency=((ModelLatency(model_id=model, estimated_latency_ms=latency),) if latency is not None else ()),
    )


def request(**overrides: object) -> RoutingRequest:
    values = {
        "requested_provider_id": None,
        "requested_model_id": None,
        "required_capabilities": frozenset({Capability.TEXT_GENERATION}),
        "objective": "latency",
        "token_estimate": TokenEstimate(1000, 1000),
    }
    values.update(overrides)
    return RoutingRequest(**values)


def test_latency_metadata_is_valid_bounded_and_unknown_by_omission():
    assert ModelLatency(model_id="m", estimated_latency_ms=1).estimated_latency_ms == 1
    assert configured_latency_ms((), "m") is None
    with pytest.raises(ValidationError):
        ModelLatency(model_id="m", estimated_latency_ms=0)
    with pytest.raises(ValidationError):
        ModelLatency(model_id="m", estimated_latency_ms=-1)
    with pytest.raises(ValidationError):
        ModelLatency(model_id="m", estimated_latency_ms=120001)


def test_latency_objective_selects_lowest_known_latency():
    decision = DeterministicRoutingPolicy().route(
        request(), (candidate("slow", 200), candidate("fast", 50)), default_provider_id="slow"
    )
    assert decision.selected_provider_id == "fast"
    assert decision.estimated_latency_ms == 50
    assert decision.policy_version == "classification-cost-latency-v1"
    assert "lowest configured estimated latency" in decision.reason


def test_capability_and_explicit_provider_model_constraints_precede_latency():
    reasoning = RoutingCandidate(
        provider_id="reasoning", provider_name="reasoning",
        capabilities=frozenset({Capability.TEXT_GENERATION, Capability.REASONING}),
        model_ids=("model",), supports_streaming=False,
        latency=(ModelLatency(model_id="model", estimated_latency_ms=200),),
    )
    decision = DeterministicRoutingPolicy().route(
        request(required_capabilities=frozenset({Capability.REASONING})),
        (candidate("cheap-fast", 1), reasoning), default_provider_id=None,
    )
    assert decision.selected_provider_id == "reasoning"
    explicit = DeterministicRoutingPolicy().route(
        request(requested_provider_id="slow", requested_model_id="model"),
        (candidate("slow", 200), candidate("fast", 1)), default_provider_id="fast",
    )
    assert explicit.selected_provider_id == "slow"
    assert explicit.selected_model_id == "model"


def test_latency_ties_use_provider_then_model_id():
    decision = DeterministicRoutingPolicy().route(
        request(), (candidate("zeta", 10), candidate("alpha", 10)), default_provider_id=None
    )
    assert decision.selected_provider_id == "alpha"


def test_unknown_latency_never_beats_known_latency_and_all_unknown_fails():
    decision = DeterministicRoutingPolicy().route(
        request(), (candidate("unknown", None, cost="0"), candidate("known", 10)), default_provider_id=None
    )
    assert decision.selected_provider_id == "known"
    with pytest.raises(RoutingError) as raised:
        DeterministicRoutingPolicy().route(request(), (candidate("unknown", None),), default_provider_id=None)
    assert raised.value.category is RoutingErrorCategory.LATENCY_UNAVAILABLE


@pytest.mark.parametrize("ceiling", [50, 75])
def test_latency_ceiling_excludes_above_and_accepts_exact_or_below(ceiling: int):
    decision = DeterministicRoutingPolicy().route(
        request(max_latency_ms=ceiling), (candidate("fast", 50), candidate("slow", 100)), default_provider_id=None
    )
    assert decision.selected_provider_id == "fast"
    assert decision.estimated_latency_ms == 50


def test_latency_ceiling_failure_is_safe_and_unknown_cannot_satisfy_it():
    with pytest.raises(RoutingError) as raised:
        DeterministicRoutingPolicy().route(request(max_latency_ms=10), (candidate("slow", 100),), default_provider_id=None)
    assert raised.value.category is RoutingErrorCategory.LATENCY_LIMIT_EXCEEDED
    with pytest.raises(RoutingError) as raised:
        DeterministicRoutingPolicy().route(request(max_latency_ms=100), (candidate("unknown", None),), default_provider_id=None)
    assert raised.value.category is RoutingErrorCategory.LATENCY_UNAVAILABLE


def test_cost_and_latency_constraints_are_applied_before_objective():
    decision = DeterministicRoutingPolicy().route(
        request(objective="latency", max_cost_usd=Decimal("0.002"), max_latency_ms=100),
        (candidate("cheap-slow", 200, cost="1"), candidate("fast-expensive", 50, cost="10"), candidate("both", 100, cost="1")),
        default_provider_id=None,
    )
    assert decision.selected_provider_id == "both"


def test_balanced_with_latency_ceiling_keeps_balanced_policy_and_cost_still_works():
    balanced = DeterministicRoutingPolicy().route(
        request(objective="balanced", max_latency_ms=100),
        (candidate("slow", 200), candidate("fast", 100)), default_provider_id="fast",
    )
    assert balanced.selected_provider_id == "fast"
    assert balanced.policy_version == "classification-cost-latency-v1"
    cost = DeterministicRoutingPolicy().route(
        request(objective="cost"), (candidate("slow", 200, cost="10"), candidate("fast", 100, cost="1")), default_provider_id=None
    )
    assert cost.selected_provider_id == "fast"
