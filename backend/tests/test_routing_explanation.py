from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from decimal import Decimal

import pytest

from gateway.domain.classification import ClassificationResult, ComplexityLevel, RequestCategory
from gateway.domain.cost import TokenEstimate
from gateway.domain.execution import (
    AttemptExplanation,
    AttemptOutcome,
    AttemptRole,
    ExecutionExplanation,
    ExplainableRoutingResult,
)
from gateway.domain.provider import Capability, HealthStatus, ModelLatency, ModelPricing, ProviderHealth
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
    RoutingDecision,
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


def candidate(
    provider: str,
    *models: str,
    capabilities=(Capability.TEXT_GENERATION,),
    pricing=(),
    latency=(),
    health=(),
) -> RoutingCandidate:
    return RoutingCandidate(
        provider_id=provider,
        provider_name=f"Provider {provider}",
        capabilities=frozenset(capabilities),
        model_ids=tuple(models),
        supports_streaming=False,
        pricing=pricing,
        latency=latency,
        health=health,
    )


def explain(request: RoutingRequest, candidates: tuple[RoutingCandidate, ...]):
    decision = DeterministicRoutingPolicy().route(request, candidates, default_provider_id=None)
    return decision, RoutingExplanation.from_trace(
        decision.trace,
        request=request,
        classification=request.classification,
        decision=decision,
    )


def recursive_keys_and_values(value):
    if is_dataclass(value):
        keys = set()
        values = set()
        for item in fields(value):
            keys.add(item.name)
            nested_keys, nested_values = recursive_keys_and_values(getattr(value, item.name))
            keys.update(nested_keys)
            values.update(nested_values)
        return keys, values
    if isinstance(value, dict):
        keys = set(value)
        values = set()
        for nested in value.values():
            nested_keys, nested_values = recursive_keys_and_values(nested)
            keys.update(nested_keys)
            values.update(nested_values)
        return keys, values
    if isinstance(value, (tuple, list, set, frozenset)):
        keys = set()
        values = set()
        for nested in value:
            nested_keys, nested_values = recursive_keys_and_values(nested)
            keys.update(nested_keys)
            values.update(nested_values)
        return keys, values
    if value is None:
        return set(), set()
    return set(), {str(value).lower()}


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


def test_routing_explanation_derives_from_single_trace_and_decision():
    request = RoutingRequest(
        requested_provider_id=None,
        requested_model_id=None,
        required_capabilities=frozenset({Capability.TEXT_GENERATION}),
        objective="balanced",
        classification=ClassificationResult(
            RequestCategory.QUESTION_ANSWER,
            ComplexityLevel.MEDIUM,
            42,
            ("question_indicators",),
            "safe",
        ),
    )
    decision = DeterministicRoutingPolicy().route(
        request,
        (
            RoutingCandidate(
                provider_id="zeta",
                provider_name="zeta",
                capabilities=frozenset({Capability.TEXT_GENERATION}),
                model_ids=("z-model",),
                supports_streaming=False,
            ),
            RoutingCandidate(
                provider_id="alpha",
                provider_name="alpha",
                capabilities=frozenset({Capability.TEXT_GENERATION}),
                model_ids=("a-model",),
                supports_streaming=False,
            ),
        ),
        default_provider_id=None,
    )
    explanation = RoutingExplanation.from_trace(
        decision.trace,
        request=request,
        classification=request.classification,
        decision=decision,
    )

    assert explanation.trace is decision.trace
    assert explanation.objective == decision.trace.objective == "balanced"
    assert explanation.policy_version == decision.policy_version == decision.trace.policy_version
    assert explanation.selection is not None
    assert explanation.selection.reason == decision.reason
    assert explanation.selection.selected.provider_id == decision.selected_provider_id
    assert explanation.selection.selected.model_id == decision.selected_model_id
    assert tuple((item.evaluation.provider_id, item.evaluation.model_id) for item in explanation.candidates) == (
        ("alpha", "a-model"),
        ("zeta", "z-model"),
    )
    assert explanation.classification is not None
    assert explanation.classification.category is RequestCategory.QUESTION_ANSWER
    assert explanation.classification.complexity_level is ComplexityLevel.MEDIUM
    forbidden = {"prompt", "completion", "api_key", "authorization", "credentials", "url", "principal_id", "historical_spend"}
    names = {field.name for field in fields(RoutingExplanation)}
    assert names.isdisjoint(forbidden)


@pytest.mark.parametrize("objective", ["balanced", "cost", "latency", "budget"])
def test_explanation_covers_each_supported_routing_objective(objective):
    request = RoutingRequest(
        None,
        None,
        frozenset({Capability.TEXT_GENERATION}),
        objective,
        token_estimate=TokenEstimate(100, 100),
        max_budget_usd=Decimal("1") if objective == "budget" else None,
        historical_spend_usd=Decimal("0") if objective == "budget" else None,
    )
    candidates = (
        candidate(
            "alpha",
            "a-model",
            pricing=(ModelPricing(model_id="a-model", input_usd_per_million_tokens=Decimal("1"), output_usd_per_million_tokens=Decimal("1")),),
            latency=(ModelLatency(model_id="a-model", estimated_latency_ms=100),),
            health=(ProviderHealth(model_id="a-model", health_score=90, status=HealthStatus.HEALTHY),),
        ),
        candidate(
            "beta",
            "b-model",
            pricing=(ModelPricing(model_id="b-model", input_usd_per_million_tokens=Decimal("2"), output_usd_per_million_tokens=Decimal("2")),),
            latency=(ModelLatency(model_id="b-model", estimated_latency_ms=200),),
            health=(ProviderHealth(model_id="b-model", health_score=80, status=HealthStatus.HEALTHY),),
        ),
    )

    decision, explanation = explain(request, candidates)

    assert explanation.objective == objective
    assert explanation.policy_version == decision.policy_version
    assert explanation.selection is not None


def test_explanation_preserves_selected_eligible_and_excluded_candidate_states():
    request = RoutingRequest(None, None, frozenset({Capability.TEXT_GENERATION}), "balanced")
    decision, explanation = explain(
        request,
        (
            candidate("alpha", "a-model"),
            candidate("beta", "b-model"),
            candidate("gamma", "g-model", capabilities=(Capability.REASONING,)),
        ),
    )

    states = {item.evaluation.provider_id: item.evaluation.status for item in explanation.candidates}
    assert states[decision.selected_provider_id] is CandidateEvaluationStatus.SELECTED
    assert states["beta"] is CandidateEvaluationStatus.EXCLUDED
    assert states["gamma"] is CandidateEvaluationStatus.EXCLUDED
    assert any(item.evaluation.exclusion_reason is RoutingExclusionReason.UNSUPPORTED_CAPABILITY for item in explanation.candidates)


def test_explanation_preserves_each_candidate_evaluation_state_in_the_trace():
    selected = CandidateEvaluation("alpha", "a-model", CandidateEvaluationStatus.SELECTED)
    eligible = CandidateEvaluation("beta", "b-model", CandidateEvaluationStatus.ELIGIBLE)
    excluded = CandidateEvaluation(
        "gamma",
        "g-model",
        CandidateEvaluationStatus.EXCLUDED,
        RoutingExclusionReason.PROVIDER_UNAVAILABLE,
    )
    trace = RoutingTrace(
        RoutingTraceStatus.COMPLETED,
        tuple(CandidateExplanation(item) for item in (selected, eligible, excluded)),
        objective="balanced",
        policy_version="classification-v1",
        capabilities=CapabilityExplanation(frozenset({Capability.TEXT_GENERATION}), frozenset({Capability.TEXT_GENERATION})),
        constraints=ConstraintExplanation(),
        budget=BudgetExplanation(False, False),
    )
    decision = RoutingDecision("alpha", "a-model", "selected", trace.policy_version, trace=trace)
    explanation = RoutingExplanation.from_trace(trace, decision=decision)

    assert tuple(item.evaluation.status for item in explanation.candidates) == (
        CandidateEvaluationStatus.SELECTED,
        CandidateEvaluationStatus.ELIGIBLE,
        CandidateEvaluationStatus.EXCLUDED,
    )


@pytest.mark.parametrize(
    "routing_request, candidates, expected_selected, expected_excluded",
    [
        (
            RoutingRequest("alpha", None, frozenset({Capability.TEXT_GENERATION}), "balanced"),
            (candidate("alpha", "a-model"), candidate("beta", "b-model")),
            "alpha",
            {"beta"},
        ),
        (
            RoutingRequest(None, None, frozenset({Capability.REASONING}), "balanced"),
            (candidate("alpha", "a-model"), candidate("beta", "b-model", capabilities=(Capability.TEXT_GENERATION, Capability.REASONING))),
            "beta",
            {"alpha"},
        ),
        (
            RoutingRequest(None, "shared-model", frozenset({Capability.TEXT_GENERATION}), "balanced"),
            (candidate("alpha", "shared-model", "other-model"), candidate("beta", "shared-model")),
            "alpha",
                {"beta"},
        ),
    ],
)
def test_explanation_records_provider_capability_and_model_filtering(routing_request, candidates, expected_selected, expected_excluded):
    decision, explanation = explain(routing_request, candidates)
    assert decision.selected_provider_id == expected_selected
    excluded = {
        item.evaluation.provider_id
        for item in explanation.candidates
        if item.evaluation.status is CandidateEvaluationStatus.EXCLUDED
    }
    assert expected_excluded.issubset(excluded)
    if routing_request.requested_model_id is not None:
        assert {item.evaluation.model_id for item in explanation.candidates} == {routing_request.requested_model_id}


def test_explanation_records_provider_health_filtering():
    request = RoutingRequest(None, None, frozenset({Capability.TEXT_GENERATION}), "balanced")
    decision, explanation = explain(
        request,
        (
            candidate(
                "unhealthy",
                "bad-model",
                health=(ProviderHealth(model_id="bad-model", health_score=0, status=HealthStatus.UNAVAILABLE),),
            ),
            candidate(
                "healthy",
                "good-model",
                health=(ProviderHealth(model_id="good-model", health_score=90, status=HealthStatus.HEALTHY),),
            ),
        ),
    )
    assert decision.selected_provider_id == "healthy"
    excluded = next(item for item in explanation.candidates if item.evaluation.provider_id == "unhealthy")
    assert excluded.evaluation.status is CandidateEvaluationStatus.EXCLUDED
    assert excluded.evaluation.exclusion_reason is RoutingExclusionReason.PROVIDER_UNAVAILABLE


@pytest.mark.parametrize(
    "field, request_kwargs, expected_reason",
    [
        ("cost", {"objective": "cost", "token_estimate": TokenEstimate(100, 100), "max_cost_usd": Decimal("0.0002")}, RoutingExclusionReason.COST_LIMIT_EXCEEDED),
        ("latency", {"objective": "latency", "max_latency_ms": 100}, RoutingExclusionReason.LATENCY_LIMIT_EXCEEDED),
        ("budget", {"objective": "budget", "max_budget_usd": Decimal("0.0002"), "historical_spend_usd": Decimal("0"), "token_estimate": TokenEstimate(100, 100)}, RoutingExclusionReason.BUDGET_LIMIT_EXCEEDED),
    ],
)
def test_explanation_records_constraint_exclusions(field, request_kwargs, expected_reason):
    request = RoutingRequest(None, None, frozenset({Capability.TEXT_GENERATION}), **request_kwargs)
    candidates = (
        candidate(
            "cheap-fast",
            "good-model",
            pricing=(ModelPricing(model_id="good-model", input_usd_per_million_tokens=Decimal("1"), output_usd_per_million_tokens=Decimal("1")),),
            latency=(ModelLatency(model_id="good-model", estimated_latency_ms=50),),
        ),
        candidate(
            "expensive-slow",
            "bad-model",
            pricing=(ModelPricing(model_id="bad-model", input_usd_per_million_tokens=Decimal("10"), output_usd_per_million_tokens=Decimal("10")),),
            latency=(ModelLatency(model_id="bad-model", estimated_latency_ms=200),),
        ),
    )
    _, explanation = explain(request, candidates)
    assert getattr(explanation.constraints, f"{field}_limit_configured", field == "budget") is True
    assert any(item.evaluation.exclusion_reason is expected_reason for item in explanation.candidates)


def test_explanation_is_deterministic_and_selection_matches_decision_exactly():
    request = RoutingRequest(None, None, frozenset({Capability.TEXT_GENERATION}), "balanced")
    candidates = (candidate("zeta", "z-model"), candidate("alpha", "a-model"))
    first_decision, first = explain(request, candidates)
    second_decision, second = explain(request, candidates)

    assert first == second
    assert first_decision == second_decision
    assert tuple((item.evaluation.provider_id, item.evaluation.model_id) for item in first.candidates) == (
        ("alpha", "a-model"),
        ("zeta", "z-model"),
    )
    assert first.selection is not None
    assert first.selection.selected.provider_id == first_decision.selected_provider_id
    assert first.selection.selected.model_id == first_decision.selected_model_id
    assert first.policy_version == first_decision.policy_version
    assert first.selection.reason == first_decision.reason
    assert first.trace is first_decision.trace


def test_recursive_explanation_representation_excludes_sensitive_request_and_provider_data():
    request = RoutingRequest(None, None, frozenset({Capability.TEXT_GENERATION}), "balanced")
    decision, explanation = explain(request, (candidate("alpha", "a-model"),))
    keys, values = recursive_keys_and_values(explanation)
    forbidden = {
        "prompt", "prompts", "completion", "completions", "api_key", "api_keys",
        "authorization", "authorization_header", "authorization_headers", "credentials",
        "url", "provider_url", "provider_urls", "principal_id", "user_id",
        "historical_spend_usd", "remaining_budget",
        "raw_provider_error", "provider_error", "raw_error", "native_error",
        "secret-prompt", "secret-completion", "provider-secret-error",
    }
    field_names = {str(item).lower() for item in keys}
    serialized_values = {str(item).lower() for item in values}
    assert field_names.isdisjoint(forbidden)
    assert all(
        not any(sensitive in item for item in serialized_values)
        for sensitive in forbidden
        if sensitive not in {"historical_spend_usd", "remaining_budget"}
    )
    assert decision.reason.lower() in values


def test_explainable_routing_result_is_immutable_and_reuses_nested_objects():
    request = RoutingRequest(None, None, frozenset({Capability.TEXT_GENERATION}), "balanced")
    decision, routing_explanation = explain(request, (candidate("alpha", "a-model"),))
    execution_explanation = ExecutionExplanation(
        "alpha",
        "a-model",
        (AttemptExplanation("alpha", "a-model", 1, AttemptRole.INITIAL, AttemptOutcome.SUCCESS),),
    )

    complete = ExplainableRoutingResult(routing_explanation, execution_explanation)

    assert complete.routing_explanation is routing_explanation
    assert complete.execution_explanation is execution_explanation
    assert complete.routing_explanation.trace is decision.trace
    assert {item.name for item in fields(complete)} == {"routing_explanation", "execution_explanation"}
    with pytest.raises(FrozenInstanceError):
        complete.execution_explanation = None


@pytest.mark.parametrize(
    "initial_provider_id, initial_model_id",
    [("beta", "a-model"), ("alpha", "b-model")],
)
def test_explainable_routing_result_rejects_execution_selection_mismatch(initial_provider_id, initial_model_id):
    request = RoutingRequest(None, None, frozenset({Capability.TEXT_GENERATION}), "balanced")
    _, routing_explanation = explain(request, (candidate("alpha", "a-model"),))
    execution_explanation = ExecutionExplanation(initial_provider_id, initial_model_id, ())

    with pytest.raises(ValueError, match="match the routing selection"):
        ExplainableRoutingResult(routing_explanation, execution_explanation)


def test_complete_explanation_recursively_excludes_sensitive_fields_and_values():
    request = RoutingRequest(None, None, frozenset({Capability.TEXT_GENERATION}), "balanced")
    _, routing_explanation = explain(request, (candidate("alpha", "a-model"),))
    execution_explanation = ExecutionExplanation(
        "alpha",
        "a-model",
        (AttemptExplanation("alpha", "a-model", 1, AttemptRole.INITIAL, AttemptOutcome.SUCCESS),),
    )
    complete = ExplainableRoutingResult(routing_explanation, execution_explanation)
    keys, values = recursive_keys_and_values(complete)
    field_names = {str(value).lower() for value in keys}
    serialized_values = {str(value).lower() for value in values}
    forbidden_fields = {
        "prompt", "prompts", "completion", "completions", "api_key", "authorization",
        "credentials", "provider_url", "principal_id", "user_id", "raw_provider_error",
        "raw_error", "metadata", "historical_spend_usd", "remaining_budget",
    }
    forbidden_values = {
        "secret-prompt", "private-completion", "test-api-key", "bearer token",
        "provider-secret-url", "principal-123", "user-456", "raw provider error",
        "historical spend amount", "remaining budget amount",
    }

    assert field_names.isdisjoint(forbidden_fields)
    assert serialized_values.isdisjoint(forbidden_values)


def test_routing_explanation_rejects_incomplete_trace_data():
    trace = RoutingTrace(
        RoutingTraceStatus.COMPLETED,
        (
            CandidateExplanation(
                CandidateEvaluation("alpha", "a-model", CandidateEvaluationStatus.SELECTED),
                Decimal("0.10"),
                10,
                90,
                "healthy",
            ),
        ),
        objective="balanced",
        policy_version="classification-v1",
        capabilities=CapabilityExplanation(
            frozenset({Capability.TEXT_GENERATION}),
            frozenset({Capability.TEXT_GENERATION}),
        ),
        constraints=ConstraintExplanation(),
        budget=BudgetExplanation(False, False),
    )
    decision = RoutingDecision(
        selected_provider_id="alpha",
        selected_model_id="a-model",
        reason="selected",
        policy_version="classification-v1",
    )

    incomplete_traces = (
        (replace(trace, objective=None), "trace must include its objective"),
        (replace(trace, policy_version=None), "trace must include its policy version"),
        (replace(trace, capabilities=None), "trace must include its capability explanation"),
        (replace(trace, constraints=None), "trace must include its constraint explanation"),
        (replace(trace, budget=None), "trace must include its budget explanation"),
        (replace(trace, evaluations=()), "selected candidate"),
    )
    for incomplete_trace, message in incomplete_traces:
        with pytest.raises(ValueError, match=message):
            RoutingExplanation.from_trace(incomplete_trace, decision=decision)


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
