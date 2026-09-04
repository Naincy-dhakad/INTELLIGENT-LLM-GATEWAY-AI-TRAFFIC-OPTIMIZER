from decimal import Decimal

import pytest
from pydantic import ValidationError

from gateway.domain.cost import CostMessage, estimate_token_counts, estimated_cost_usd
from gateway.domain.provider import ModelPricing
from gateway.domain.routing import (
    DeterministicRoutingPolicy,
    RoutingCandidate,
    RoutingError,
    RoutingErrorCategory,
    RoutingRequest,
)
from gateway.domain.cost import TokenEstimate
from gateway.domain.provider import Capability


def priced_candidate(provider_id: str, cost: str, model: str = "model") -> RoutingCandidate:
    return RoutingCandidate(
        provider_id=provider_id,
        provider_name=provider_id,
        capabilities=frozenset({Capability.TEXT_GENERATION}),
        model_ids=(model,),
        supports_streaming=False,
        pricing=(ModelPricing(model_id=model, input_usd_per_million_tokens=Decimal(cost), output_usd_per_million_tokens=Decimal(cost)),),
    )


def request(**overrides: object) -> RoutingRequest:
    values = {
        "requested_provider_id": None,
        "requested_model_id": None,
        "required_capabilities": frozenset({Capability.TEXT_GENERATION}),
        "objective": "cost",
        "token_estimate": TokenEstimate(input_tokens=1000, output_tokens=1000),
    }
    values.update(overrides)
    return RoutingRequest(**values)


def test_cost_objective_selects_lowest_priced_eligible_candidate():
    decision = DeterministicRoutingPolicy().route(
        request(),
        (priced_candidate("expensive", "10"), priced_candidate("cheap", "1")),
        default_provider_id="expensive",
    )
    assert decision.selected_provider_id == "cheap"
    assert decision.estimated_cost_usd == Decimal("0.00200000")
    assert "lowest estimated cost" in decision.reason
    assert decision.policy_version == "classification-cost-v1"


def test_capability_and_explicit_constraints_are_applied_before_cost():
    reasoning = RoutingCandidate(
        provider_id="reasoning",
        provider_name="reasoning",
        capabilities=frozenset({Capability.TEXT_GENERATION, Capability.REASONING}),
        model_ids=("model",),
        supports_streaming=False,
        pricing=(ModelPricing(model_id="model", input_usd_per_million_tokens=Decimal("10"), output_usd_per_million_tokens=Decimal("10")),),
    )
    decision = DeterministicRoutingPolicy().route(
        request(required_capabilities=frozenset({Capability.REASONING})),
        (priced_candidate("cheap", "0.01"), reasoning),
        default_provider_id="reasoning",
    )
    assert decision.selected_provider_id == "reasoning"


def test_explicit_provider_and_model_override_cost_selection():
    decision = DeterministicRoutingPolicy().route(
        request(requested_provider_id="expensive", requested_model_id="model"),
        (priced_candidate("expensive", "10"), priced_candidate("cheap", "1")),
        default_provider_id="cheap",
    )
    assert decision.selected_provider_id == "expensive"
    assert decision.selected_model_id == "model"


@pytest.mark.parametrize("ceiling, expected", [(Decimal("0.002"), "cheap"), (Decimal("0.00200001"), "cheap")])
def test_cost_ceiling_includes_exact_boundary_and_lower_cost(ceiling, expected):
    decision = DeterministicRoutingPolicy().route(
        request(max_cost_usd=ceiling),
        (priced_candidate("cheap", "1"), priced_candidate("expensive", "10")),
        default_provider_id="expensive",
    )
    assert decision.selected_provider_id == expected


def test_cost_ceiling_fails_without_over_budget_selection():
    with pytest.raises(RoutingError) as raised:
        DeterministicRoutingPolicy().route(
            request(max_cost_usd=Decimal("0.001")),
            (priced_candidate("cheap", "1"),),
            default_provider_id=None,
        )
    assert raised.value.category is RoutingErrorCategory.COST_LIMIT_EXCEEDED


def test_unknown_pricing_is_not_zero_and_cost_routing_fails_safely():
    unknown = RoutingCandidate(
        provider_id="unknown", provider_name="unknown",
        capabilities=frozenset({Capability.TEXT_GENERATION}), model_ids=("model",), supports_streaming=False,
    )
    with pytest.raises(RoutingError) as raised:
        DeterministicRoutingPolicy().route(request(), (unknown,), default_provider_id=None)
    assert raised.value.category is RoutingErrorCategory.COST_UNAVAILABLE


def test_equal_cost_uses_provider_then_model_tie_breaking():
    decision = DeterministicRoutingPolicy().route(
        request(),
        (priced_candidate("zeta", "1"), priced_candidate("alpha", "1")),
        default_provider_id=None,
    )
    assert decision.selected_provider_id == "alpha"


def test_cost_estimation_is_bounded_deterministic_and_provider_free():
    messages = (CostMessage("abcd"), CostMessage("efgh"))
    assert estimate_token_counts(messages) == estimate_token_counts(messages)
    assert estimate_token_counts(messages).input_tokens == 2
    assert estimate_token_counts(messages, 100_000).output_tokens == 4096
    assert estimated_cost_usd(TokenEstimate(1, 1), Decimal("1"), Decimal("2")) == Decimal("0.00000300")


def test_pricing_rejects_negative_values():
    with pytest.raises(ValidationError):
        ModelPricing(model_id="model", input_usd_per_million_tokens=-1, output_usd_per_million_tokens=1)
