import pytest

from gateway.domain.classification import (
    ClassificationResult,
    ComplexityLevel,
    RequestCategory,
)
from gateway.domain.provider import Capability, ProviderMetadata
from gateway.domain.routing import (
    DeterministicRoutingPolicy,
    RoutingCandidate,
    RoutingError,
    RoutingErrorCategory,
    RoutingRequest,
)


def candidate(provider_id: str, *models: str, capabilities=(Capability.TEXT_GENERATION,)) -> RoutingCandidate:
    return RoutingCandidate(
        provider_id=provider_id,
        provider_name=f"Provider {provider_id}",
        capabilities=frozenset(capabilities),
        model_ids=tuple(models),
        supports_streaming=False,
    )


def request(**overrides: object) -> RoutingRequest:
    values = {
        "requested_provider_id": None,
        "requested_model_id": None,
        "required_capabilities": frozenset({Capability.TEXT_GENERATION}),
        "objective": "balanced",
    }
    values.update(overrides)
    return RoutingRequest(**values)


def test_explicit_provider_is_selected_without_considering_another_provider() -> None:
    policy = DeterministicRoutingPolicy()
    decision = policy.route(
        request(requested_provider_id="openai"),
        (candidate("anthropic", "claude"), candidate("openai", "gpt")),
        default_provider_id="anthropic",
    )
    assert decision.selected_provider_id == "openai"
    assert decision.selected_model_id == "gpt"
    assert decision.reason == 'Explicit provider "openai" selected.'


def test_default_provider_is_selected_when_no_provider_is_requested() -> None:
    policy = DeterministicRoutingPolicy()
    decision = policy.route(
        request(),
        (candidate("openai", "gpt"), candidate("anthropic", "claude")),
        default_provider_id="anthropic",
    )
    assert decision.selected_provider_id == "anthropic"
    assert decision.selected_model_id == "claude"
    assert "Default provider \"anthropic\"" in decision.reason


def test_default_provider_ineligible_uses_provider_id_tie_break() -> None:
    policy = DeterministicRoutingPolicy()
    decision = policy.route(
        request(),
        (
            candidate("zeta", "z-model"),
            candidate("alpha", "a-model"),
            candidate("default", "d-model", capabilities=(Capability.CODING,)),
        ),
        default_provider_id="default",
    )
    assert decision.selected_provider_id == "alpha"
    assert decision.selected_model_id == "a-model"
    assert "deterministic tie-breaker" in decision.reason


def test_supported_explicit_model_is_preserved() -> None:
    decision = DeterministicRoutingPolicy().route(
        request(requested_model_id="gpt-large"),
        (candidate("openai", "gpt-small", "gpt-large"),),
        default_provider_id="openai",
    )
    assert decision.selected_model_id == "gpt-large"


@pytest.mark.parametrize(
    "routing_request, candidates, category",
    [
        (
            request(requested_provider_id="missing"),
            (candidate("openai", "gpt"),),
            RoutingErrorCategory.UNKNOWN_PROVIDER,
        ),
        (
            request(requested_provider_id="openai", required_capabilities=frozenset({Capability.REASONING})),
            (candidate("openai", "gpt"),),
            RoutingErrorCategory.UNSUPPORTED_CAPABILITY,
        ),
        (
            request(requested_provider_id="openai", requested_model_id="unknown-model"),
            (candidate("openai", "gpt"),),
            RoutingErrorCategory.MODEL_NOT_SUPPORTED,
        ),
        (
            request(required_capabilities=frozenset({Capability.REASONING})),
            (candidate("openai", "gpt"),),
            RoutingErrorCategory.NO_ELIGIBLE_PROVIDER,
        ),
    ],
)
def test_ineligible_requests_raise_structured_routing_errors(
    routing_request: RoutingRequest,
    candidates: tuple[RoutingCandidate, ...],
    category: RoutingErrorCategory,
) -> None:
    with pytest.raises(RoutingError) as raised:
        DeterministicRoutingPolicy().route(
            routing_request, candidates, default_provider_id="openai"
        )
    assert raised.value.category is category
    assert "secret" not in raised.value.message.lower()


def test_unsupported_objective_is_rejected_without_fake_optimization() -> None:
    with pytest.raises(RoutingError) as raised:
        DeterministicRoutingPolicy().route(
            request(objective="quality"),
            (candidate("openai", "gpt"),),
            default_provider_id="openai",
        )
    assert raised.value.category is RoutingErrorCategory.UNSUPPORTED_OBJECTIVE


def test_routing_is_deterministic_for_repeated_identical_inputs() -> None:
    policy = DeterministicRoutingPolicy()
    routing_request = request()
    candidates = (candidate("zeta", "z-model"), candidate("alpha", "a-model"))
    decisions = [
        policy.route(routing_request, candidates, default_provider_id=None)
        for _ in range(20)
    ]
    assert len(set(decisions)) == 1


def classification(
    category: RequestCategory, level: ComplexityLevel = ComplexityLevel.MEDIUM
) -> ClassificationResult:
    return ClassificationResult(
        category=category,
        complexity_level=level,
        complexity_score=50 if level is ComplexityLevel.MEDIUM else (10 if level is ComplexityLevel.LOW else 90),
        matched_signals=(),
        explanation="safe deterministic explanation",
    )


def test_coding_prefers_declared_coding_capability_over_default() -> None:
    decision = DeterministicRoutingPolicy().route(
        request(classification=classification(RequestCategory.CODING)),
        (
            candidate("default", "default-model"),
            candidate("coding", "coding-model", capabilities=(Capability.TEXT_GENERATION, Capability.CODING)),
        ),
        default_provider_id="default",
    )
    assert decision.selected_provider_id == "coding"
    assert "coding" in decision.reason
    assert decision.policy_version == "classification-v1"


def test_debugging_and_architecture_use_only_declared_reasoning_or_coding_capabilities() -> None:
    policy = DeterministicRoutingPolicy()
    candidates = (
        candidate("plain", "plain-model"),
        candidate("debug", "debug-model", capabilities=(Capability.TEXT_GENERATION, Capability.CODING)),
        candidate("design", "design-model", capabilities=(Capability.TEXT_GENERATION, Capability.REASONING)),
    )
    debugging = policy.route(
        request(classification=classification(RequestCategory.DEBUGGING)),
        candidates,
        default_provider_id="plain",
    )
    architecture = policy.route(
        request(classification=classification(RequestCategory.ARCHITECTURE_DESIGN)),
        candidates,
        default_provider_id="plain",
    )
    assert debugging.selected_provider_id == "debug"
    assert architecture.selected_provider_id == "design"


def test_reasoning_high_complexity_prefers_declared_reasoning_capability() -> None:
    decision = DeterministicRoutingPolicy().route(
        request(classification=classification(RequestCategory.QUESTION_ANSWER, ComplexityLevel.HIGH)),
        (candidate("plain", "plain-model"), candidate("reasoning", "reasoning-model", capabilities=(Capability.TEXT_GENERATION, Capability.REASONING))),
        default_provider_id="plain",
    )
    assert decision.selected_provider_id == "reasoning"
    assert "HIGH" in decision.reason


@pytest.mark.parametrize("level", list(ComplexityLevel))
def test_complexity_levels_produce_repeatable_policy_behavior(level: ComplexityLevel) -> None:
    policy = DeterministicRoutingPolicy()
    routing_request = request(
        classification=classification(RequestCategory.REASONING, level)
    )
    candidates = (candidate("zeta", "z-model"), candidate("alpha", "a-model", capabilities=(Capability.REASONING,)))
    decisions = [policy.route(routing_request, candidates, default_provider_id=None) for _ in range(5)]
    assert len(set(decisions)) == 1
    assert decisions[0].policy_version == "classification-v1"


def test_required_capability_and_explicit_model_cannot_be_overridden_by_classification() -> None:
    decision = DeterministicRoutingPolicy().route(
        request(
            requested_model_id="shared-model",
            required_capabilities=frozenset({Capability.STRUCTURED_OUTPUT}),
            classification=classification(RequestCategory.CODING),
        ),
        (
            candidate("coding", "shared-model", capabilities=(Capability.TEXT_GENERATION, Capability.CODING)),
            candidate("plain", "shared-model", capabilities=(Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT)),
        ),
        default_provider_id="coding",
    )
    assert decision.selected_provider_id == "plain"
    assert decision.selected_model_id == "shared-model"


def test_explicit_provider_remains_hard_constraint_under_classification() -> None:
    decision = DeterministicRoutingPolicy().route(
        request(
            requested_provider_id="plain",
            classification=classification(RequestCategory.CODING),
        ),
        (candidate("plain", "plain-model"), candidate("coding", "coding-model", capabilities=(Capability.CODING,))),
        default_provider_id="coding",
    )
    assert decision.selected_provider_id == "plain"
    assert decision.reason == 'Explicit provider "plain" selected.'


def test_routing_candidates_are_built_from_normalized_metadata() -> None:
    metadata = ProviderMetadata(
        id="openai",
        name="OpenAI",
        capabilities=frozenset({Capability.TEXT_GENERATION}),
        model_ids=("gpt-configured",),
    )
    normalized = RoutingCandidate.from_metadata(metadata)
    assert normalized.provider_id == "openai"
    assert normalized.model_ids == ("gpt-configured",)
