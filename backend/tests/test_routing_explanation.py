from dataclasses import FrozenInstanceError, fields
from decimal import Decimal

import pytest

from gateway.domain.classification import ComplexityLevel, RequestCategory
from gateway.domain.provider import Capability
from gateway.domain.routing import (
    BudgetExplanation,
    CandidateEvaluation,
    CandidateEvaluationStatus,
    CandidateExplanation,
    CapabilityExplanation,
    ClassificationExplanation,
    ConstraintExplanation,
    RoutingExclusionReason,
    RoutingExplanation,
    RoutingTrace,
    RoutingTraceStatus,
    SelectionExplanation,
    DeterministicRoutingPolicy,
    RoutingCandidate,
    RoutingRequest,
    RoutingError,
    RoutingErrorCategory,
)


def evaluation(provider: str, model: str, status=CandidateEvaluationStatus.ELIGIBLE):
    return CandidateEvaluation(provider, model, status)


def explanation(provider: str, model: str):
    return CandidateExplanation(evaluation(provider, model), Decimal("0.12"), None, None, None)


def test_explanation_types_are_frozen_and_collections_are_immutable():
    item = explanation("alpha", "model")
    with pytest.raises(FrozenInstanceError):
        item.estimated_cost_usd = Decimal("1")
    with pytest.raises(TypeError):
        RoutingTrace(RoutingTraceStatus.COMPLETED, [item])


def test_candidate_ordering_is_deterministic():
    items = (explanation("alpha", "z"), explanation("beta", "a"))
    trace = RoutingTrace(RoutingTraceStatus.COMPLETED, items)
    assert tuple((i.evaluation.provider_id, i.evaluation.model_id) for i in trace.evaluations) == (("alpha", "z"), ("beta", "a"))
    with pytest.raises(ValueError):
        RoutingTrace(RoutingTraceStatus.COMPLETED, tuple(reversed(items)))


def test_valid_explanation_preserves_decimal_and_missing_values():
    selected = evaluation("alpha", "model", CandidateEvaluationStatus.SELECTED)
    cap = CapabilityExplanation(frozenset({Capability.TEXT_GENERATION}), frozenset({Capability.TEXT_GENERATION}))
    result = RoutingExplanation(
        policy_version="classification-v1",
        objective="balanced",
        classification=ClassificationExplanation(RequestCategory.UNKNOWN, ComplexityLevel.LOW, 0),
        capabilities=cap,
        budget=BudgetExplanation(False, False),
        constraints=ConstraintExplanation(),
        candidates=(CandidateExplanation(selected, Decimal("0.10"), None, None, None),),
        selection=SelectionExplanation("balanced", "classification-v1", "selected", selected),
        trace=RoutingTrace(RoutingTraceStatus.COMPLETED, (CandidateExplanation(selected),)),
    )
    assert result.candidates[0].estimated_cost_usd == Decimal("0.10")
    assert result.candidates[0].estimated_latency_ms is None
    assert result.candidates[0].health_score is None


def test_invalid_values_are_rejected():
    with pytest.raises(ValueError):
        evaluation("bad id", "model")
    with pytest.raises(ValueError):
        CandidateEvaluation("provider", "model", CandidateEvaluationStatus.EXCLUDED)
    with pytest.raises(TypeError):
        CandidateExplanation(evaluation("provider", "model"), 0.1)
    with pytest.raises(ValueError):
        CandidateExplanation(evaluation("provider", "model"), Decimal("-1"))


def test_sensitive_fields_are_not_part_of_domain_types():
    forbidden = {"prompt", "completion", "api_key", "authorization", "credentials", "url", "principal_id", "historical_spend"}
    names = {field.name for cls in (CandidateEvaluation, CandidateExplanation, RoutingTrace, RoutingExplanation) for field in fields(cls)}
    assert names.isdisjoint(forbidden)


def test_excluded_candidate_requires_normalized_reason():
    excluded = CandidateEvaluation("provider", "model", CandidateEvaluationStatus.EXCLUDED, RoutingExclusionReason.COST_LIMIT_EXCEEDED)
    assert excluded.exclusion_reason is RoutingExclusionReason.COST_LIMIT_EXCEEDED


def test_route_attaches_trace_from_the_single_routing_pass():
    def candidate(provider_id: str, model_id: str) -> RoutingCandidate:
        return RoutingCandidate(
            provider_id=provider_id,
            provider_name=provider_id,
            capabilities=frozenset({Capability.TEXT_GENERATION}),
            model_ids=(model_id,),
            supports_streaming=False,
        )

    request = RoutingRequest(None, None, frozenset({Capability.TEXT_GENERATION}), "balanced")
    decision = DeterministicRoutingPolicy().route(
        request,
        (candidate("zeta", "z-model"), candidate("alpha", "a-model")),
        default_provider_id=None,
    )

    assert decision.trace is not None
    assert decision.trace.objective == "balanced"
    assert decision.trace.policy_version == decision.policy_version
    assert tuple((item.evaluation.provider_id, item.evaluation.model_id) for item in decision.trace.evaluations) == (
        ("alpha", "a-model"),
        ("zeta", "z-model"),
    )
    selected = tuple(item for item in decision.trace.evaluations if item.evaluation.status is CandidateEvaluationStatus.SELECTED)
    assert len(selected) == 1
    assert (selected[0].evaluation.provider_id, selected[0].evaluation.model_id) == (
        decision.selected_provider_id,
        decision.selected_model_id,
    )


@pytest.mark.parametrize(
    "requested_provider_id, expected_category",
    [
        (None, RoutingErrorCategory.NO_ELIGIBLE_PROVIDER),
        ("openai", RoutingErrorCategory.UNSUPPORTED_CAPABILITY),
    ],
)
def test_unavailable_capability_preserves_original_routing_error(
    requested_provider_id, expected_category
):
    candidate = RoutingCandidate(
        provider_id="openai",
        provider_name="openai",
        capabilities=frozenset({Capability.TEXT_GENERATION}),
        model_ids=("gpt",),
        supports_streaming=False,
    )
    request = RoutingRequest(
        requested_provider_id,
        None,
        frozenset({Capability.REASONING}),
        "balanced",
    )

    with pytest.raises(RoutingError) as raised:
        DeterministicRoutingPolicy().route(request, (candidate,), default_provider_id="openai")

    assert raised.value.category is expected_category
