from decimal import Decimal

import pytest

from gateway.domain.cost import TokenEstimate
from gateway.domain.provider import Capability, ModelPricing
from gateway.domain.routing import (
    DeterministicRoutingPolicy,
    RoutingCandidate,
    RoutingError,
    RoutingErrorCategory,
    RoutingRequest,
)


def candidate(provider_id, cost):
    return RoutingCandidate(
        provider_id=provider_id,
        provider_name=provider_id,
        capabilities=frozenset({Capability.TEXT_GENERATION}),
        model_ids=("model",),
        supports_streaming=False,
        pricing=(ModelPricing(
            model_id="model",
            input_usd_per_million_tokens=Decimal(cost),
            output_usd_per_million_tokens=Decimal("0"),
        ),),
    )


def request(**overrides):
    values = dict(
        requested_provider_id=None,
        requested_model_id=None,
        required_capabilities=frozenset({Capability.TEXT_GENERATION}),
        objective="budget",
        token_estimate=TokenEstimate(input_tokens=1000, output_tokens=0),
        max_budget_usd=Decimal("1"),
        historical_spend_usd=Decimal("0"),
    )
    values.update(overrides)
    return RoutingRequest(**values)


def test_zero_spend_and_exact_remaining_budget_are_allowed():
    decision = DeterministicRoutingPolicy().route(
        request(), (candidate("provider", "1000"),), default_provider_id=None
    )
    assert decision.selected_provider_id == "provider"
    assert decision.policy_version.endswith("budget-v1")


def test_spend_below_budget_filters_to_routes_that_fit():
    decision = DeterministicRoutingPolicy().route(
        request(historical_spend_usd=Decimal("0.50")),
        (candidate("too-expensive", "1000"), candidate("fits", "400")),
        default_provider_id=None,
    )
    assert decision.selected_provider_id == "fits"


@pytest.mark.parametrize(
    "overrides, category",
    [
        ({"historical_spend_usd": Decimal("1.01")}, RoutingErrorCategory.BUDGET_EXHAUSTED),
        ({"historical_spend_usd": Decimal("0.99")}, RoutingErrorCategory.BUDGET_LIMIT_EXCEEDED),
        ({"historical_spend_usd": None}, RoutingErrorCategory.BUDGET_UNAVAILABLE),
    ],
)
def test_budget_failure_modes_are_normalized(overrides, category):
    with pytest.raises(RoutingError) as raised:
        DeterministicRoutingPolicy().route(
            request(**overrides), (candidate("provider", "1000"),), default_provider_id=None
        )
    assert raised.value.category is category


def test_budget_preserves_explicit_provider_and_capability_constraints():
    decision = DeterministicRoutingPolicy().route(
        request(requested_provider_id="explicit"),
        (candidate("other", "100"), candidate("explicit", "500")),
        default_provider_id="other",
    )
    assert decision.selected_provider_id == "explicit"


def test_budget_decision_is_deterministic():
    policy = DeterministicRoutingPolicy()
    candidates = (candidate("a", "100"), candidate("b", "100"))
    decisions = [policy.route(request(), candidates, default_provider_id=None) for _ in range(10)]
    assert len(set(decisions)) == 1
