from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from gateway.domain.classification import (
    ClassificationResult,
    ComplexityLevel,
    RequestCategory,
)
from gateway.domain.cost import TokenEstimate, estimated_cost_usd
from gateway.domain.health import health_for_model
from gateway.domain.latency import configured_latency_ms
from gateway.domain.provider import (
    Capability,
    ModelLatency,
    ModelPricing,
    ProviderHealth,
    ProviderMetadata,
)


class RoutingErrorCategory(StrEnum):
    UNKNOWN_PROVIDER = "unknown_provider"
    NO_ELIGIBLE_PROVIDER = "no_eligible_provider"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    MODEL_NOT_SUPPORTED = "model_not_supported"
    UNSUPPORTED_OBJECTIVE = "unsupported_objective"
    COST_UNAVAILABLE = "cost_unavailable"
    COST_LIMIT_EXCEEDED = "cost_limit_exceeded"
    LATENCY_UNAVAILABLE = "latency_unavailable"
    LATENCY_LIMIT_EXCEEDED = "latency_limit_exceeded"
    QUALITY_UNAVAILABLE = "quality_unavailable"
    PROVIDER_UNHEALTHY = "provider_unhealthy"
    BUDGET_UNAVAILABLE = "budget_unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"
    BUDGET_LIMIT_EXCEEDED = "budget_limit_exceeded"


@dataclass(frozen=True)
class RoutingError(Exception):
    """Safe domain error raised when deterministic routing cannot produce a decision."""

    category: RoutingErrorCategory
    message: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


@dataclass(frozen=True)
class RoutingRequest:
    requested_provider_id: str | None
    requested_model_id: str | None
    required_capabilities: frozenset[Capability]
    objective: str
    classification: ClassificationResult | None = None
    token_estimate: TokenEstimate | None = None
    max_cost_usd: Decimal | None = None
    max_latency_ms: int | None = None
    max_budget_usd: Decimal | None = None
    historical_spend_usd: Decimal | None = None


@dataclass(frozen=True)
class RoutingCandidate:
    provider_id: str
    provider_name: str
    capabilities: frozenset[Capability]
    model_ids: tuple[str, ...]
    supports_streaming: bool
    pricing: tuple[ModelPricing, ...] = ()
    latency: tuple[ModelLatency, ...] = ()
    health: tuple[ProviderHealth, ...] = ()

    @classmethod
    def from_metadata(cls, metadata: ProviderMetadata) -> "RoutingCandidate":
        return cls(
            provider_id=metadata.id,
            provider_name=metadata.name,
            capabilities=metadata.capabilities,
            model_ids=metadata.model_ids,
            supports_streaming=metadata.supports_streaming,
            pricing=metadata.pricing,
            latency=metadata.latency,
            health=metadata.health,
        )


@dataclass(frozen=True)
class RoutingDecision:
    selected_provider_id: str
    selected_model_id: str
    reason: str
    policy_version: str = "classification-v1"
    estimated_cost_usd: Decimal | None = None
    estimated_latency_ms: int | None = None
    health_score: int | None = None
    # Internal only; API projections intentionally ignore this field.
    trace: "RoutingTrace | None" = field(default=None, compare=False, repr=False)


class RoutingExclusionReason(StrEnum):
    """Stable, provider-neutral reasons a route was not eligible."""

    UNKNOWN_PROVIDER = "unknown_provider"
    PROVIDER_NOT_REQUESTED = "provider_not_requested"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    MODEL_NOT_SUPPORTED = "model_not_supported"
    NO_MODEL = "no_model"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    COST_UNAVAILABLE = "cost_unavailable"
    COST_LIMIT_EXCEEDED = "cost_limit_exceeded"
    LATENCY_UNAVAILABLE = "latency_unavailable"
    LATENCY_LIMIT_EXCEEDED = "latency_limit_exceeded"
    HEALTH_UNAVAILABLE = "health_unavailable"
    BUDGET_UNAVAILABLE = "budget_unavailable"
    BUDGET_LIMIT_EXCEEDED = "budget_limit_exceeded"
    NOT_SELECTED = "not_selected"


class CandidateEvaluationStatus(StrEnum):
    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"
    SELECTED = "selected"


class RoutingTraceStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


_MAX_EXPLANATION_IDENTIFIER_LENGTH = 128


def _check_explanation_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > _MAX_EXPLANATION_IDENTIFIER_LENGTH:
        raise ValueError(f"{field_name} must be a non-empty identifier of at most 128 characters")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain whitespace or control characters")


def _check_tuple(value: object, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be an immutable tuple")


@dataclass(frozen=True)
class ClassificationExplanation:
    """Structured classification facts; it deliberately contains no request text."""

    category: RequestCategory
    complexity_level: ComplexityLevel
    complexity_score: int
    matched_signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.category, RequestCategory) or not isinstance(self.complexity_level, ComplexityLevel):
            raise TypeError("classification values must use the domain enums")
        if not isinstance(self.complexity_score, int) or self.complexity_score < 0:
            raise ValueError("complexity_score must be a non-negative integer")
        _check_tuple(self.matched_signals, "matched_signals")
        for signal in self.matched_signals:
            _check_explanation_identifier(signal, "matched signal")


@dataclass(frozen=True)
class CapabilityExplanation:
    required: frozenset[Capability]
    available: frozenset[Capability]
    missing: frozenset[Capability] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.required, frozenset) or not isinstance(self.available, frozenset) or not isinstance(self.missing, frozenset):
            raise TypeError("capability collections must be immutable frozensets")
        if self.missing != self.required - self.available:
            raise ValueError("missing capabilities must equal required capabilities not available")


@dataclass(frozen=True)
class BudgetExplanation:
    """Budget facts intentionally expose availability and eligibility only, never amounts."""

    limit_configured: bool
    historical_spend_available: bool
    eligible: bool | None = None
    reason: RoutingExclusionReason | None = None


@dataclass(frozen=True)
class ConstraintExplanation:
    cost_limit_configured: bool = False
    latency_limit_configured: bool = False
    cost_eligible: bool | None = None
    latency_eligible: bool | None = None


@dataclass(frozen=True)
class CandidateEvaluation:
    provider_id: str
    model_id: str
    status: CandidateEvaluationStatus
    exclusion_reason: RoutingExclusionReason | None = None

    def __post_init__(self) -> None:
        _check_explanation_identifier(self.provider_id, "provider_id")
        _check_explanation_identifier(self.model_id, "model_id")
        if self.status is CandidateEvaluationStatus.EXCLUDED and self.exclusion_reason is None:
            raise ValueError("excluded candidates require an exclusion reason")
        if self.status is not CandidateEvaluationStatus.EXCLUDED and self.exclusion_reason is not None:
            raise ValueError("only excluded candidates may have an exclusion reason")


@dataclass(frozen=True)
class CandidateExplanation:
    evaluation: CandidateEvaluation
    estimated_cost_usd: Decimal | None = None
    estimated_latency_ms: int | None = None
    health_score: int | None = None
    health_status: str | None = None

    def __post_init__(self) -> None:
        if self.estimated_cost_usd is not None and not isinstance(self.estimated_cost_usd, Decimal):
            raise TypeError("estimated_cost_usd must be Decimal or None")
        if self.estimated_cost_usd is not None and self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must not be negative")
        if self.estimated_latency_ms is not None and (not isinstance(self.estimated_latency_ms, int) or self.estimated_latency_ms <= 0):
            raise ValueError("estimated_latency_ms must be a positive integer or None")
        if self.health_score is not None and (not isinstance(self.health_score, int) or not 0 <= self.health_score <= 100):
            raise ValueError("health_score must be between 0 and 100 or None")
        if self.health_status is not None:
            _check_explanation_identifier(self.health_status, "health_status")


@dataclass(frozen=True)
class SelectionExplanation:
    objective: str
    policy_version: str
    reason: str
    selected: CandidateEvaluation
    alternatives: tuple[CandidateEvaluation, ...] = ()

    def __post_init__(self) -> None:
        _check_explanation_identifier(self.objective, "objective")
        _check_explanation_identifier(self.policy_version, "policy_version")
        if not isinstance(self.reason, str) or not self.reason or "\n" in self.reason or "\r" in self.reason:
            raise ValueError("reason must be a non-empty single-line value")
        _check_tuple(self.alternatives, "alternatives")
        if self.selected.status is not CandidateEvaluationStatus.SELECTED:
            raise ValueError("selected candidate must have selected status")


@dataclass(frozen=True)
class RoutingTrace:
    """Immutable trace produced by one deterministic routing pass."""

    status: RoutingTraceStatus
    evaluations: tuple[CandidateExplanation, ...] = ()
    error_reason: RoutingExclusionReason | None = None
    objective: str | None = None
    policy_version: str | None = None
    capabilities: CapabilityExplanation | None = None
    constraints: ConstraintExplanation | None = None
    budget: BudgetExplanation | None = None

    def __post_init__(self) -> None:
        _check_tuple(self.evaluations, "evaluations")
        if self.objective is not None:
            _check_explanation_identifier(self.objective, "objective")
        if self.policy_version is not None:
            _check_explanation_identifier(self.policy_version, "policy_version")
        ordered = tuple(sorted(self.evaluations, key=lambda item: (item.evaluation.provider_id, item.evaluation.model_id)))
        if ordered != self.evaluations:
            raise ValueError("evaluations must be in deterministic provider/model order")
        if self.status is RoutingTraceStatus.FAILED and self.error_reason is None:
            raise ValueError("failed traces require an error reason")
        if self.status is RoutingTraceStatus.COMPLETED and self.error_reason is not None:
            raise ValueError("completed traces must not have an error reason")


@dataclass(frozen=True)
class RoutingExplanation:
    """Complete internal explanation data, not a public API response."""

    policy_version: str
    objective: str
    classification: ClassificationExplanation | None
    capabilities: CapabilityExplanation
    budget: BudgetExplanation
    constraints: ConstraintExplanation
    candidates: tuple[CandidateExplanation, ...]
    selection: SelectionExplanation | None
    trace: RoutingTrace

    def __post_init__(self) -> None:
        _check_explanation_identifier(self.policy_version, "policy_version")
        _check_explanation_identifier(self.objective, "objective")
        _check_tuple(self.candidates, "candidates")
        ordered = tuple(sorted(self.candidates, key=lambda item: (item.evaluation.provider_id, item.evaluation.model_id)))
        if ordered != self.candidates:
            raise ValueError("candidates must be in deterministic provider/model order")
        if self.selection is not None and self.selection.selected not in tuple(item.evaluation for item in self.candidates):
            raise ValueError("selected candidate must be present in candidates")


@dataclass(frozen=True)
class _PreferenceScore:
    classification_match_score: int = 0
    complexity_match_score: int = 0
    default_provider_bonus: int = 0

    @property
    def total(self) -> int:
        return (
            self.classification_match_score
            + self.complexity_match_score
            + self.default_provider_bonus
        )


@dataclass(frozen=True)
class _ModelCandidate:
    candidate: RoutingCandidate
    model_id: str
    estimated_cost: Decimal | None
    estimated_latency_ms: int | None
    health_score: int | None
    health_status: str


class _RoutingTraceCollector:
    """Mutable only for the duration of one route call; final output is immutable."""

    def __init__(self, request: RoutingRequest, candidates: tuple[RoutingCandidate, ...]) -> None:
        self._items: dict[tuple[str, str], CandidateExplanation] = {}
        available = frozenset().union(*(candidate.capabilities for candidate in candidates))
        self._capabilities = CapabilityExplanation(request.required_capabilities, available)
        self._constraints = ConstraintExplanation(
            cost_limit_configured=request.max_cost_usd is not None,
            latency_limit_configured=request.max_latency_ms is not None,
        )
        self._budget = BudgetExplanation(
            request.max_budget_usd is not None,
            request.historical_spend_usd is not None,
        )
        self._objective = request.objective

    def record(
        self,
        provider_id: str,
        model_id: str,
        status: CandidateEvaluationStatus,
        reason: RoutingExclusionReason | None = None,
        *,
        cost: Decimal | None = None,
        latency_ms: int | None = None,
        health_score: int | None = None,
        health_status: str | None = None,
    ) -> None:
        key = (provider_id, model_id)
        self._items[key] = CandidateExplanation(
            CandidateEvaluation(provider_id, model_id, status, reason),
            cost,
            latency_ms,
            health_score,
            health_status,
        )

    def mark_excluded(self, item: _ModelCandidate, reason: RoutingExclusionReason) -> None:
        self.record(
            item.candidate.provider_id,
            item.model_id,
            CandidateEvaluationStatus.EXCLUDED,
            reason,
            cost=item.estimated_cost,
            latency_ms=item.estimated_latency_ms,
            health_score=item.health_score,
            health_status=item.health_status,
        )

    def mark_selected(self, provider_id: str, model_id: str) -> None:
        item = self._items[(provider_id, model_id)]
        self.record(
            provider_id,
            model_id,
            CandidateEvaluationStatus.SELECTED,
            cost=item.estimated_cost_usd,
            latency_ms=item.estimated_latency_ms,
            health_score=item.health_score,
            health_status=item.health_status,
        )

    def finish(self, selected: tuple[str, str], policy_version: str) -> RoutingTrace:
        self.mark_selected(*selected)
        for key, item in tuple(self._items.items()):
            if key != selected and item.evaluation.status is CandidateEvaluationStatus.ELIGIBLE:
                self.record(
                    *key,
                    CandidateEvaluationStatus.EXCLUDED,
                    RoutingExclusionReason.NOT_SELECTED,
                    cost=item.estimated_cost_usd,
                    latency_ms=item.estimated_latency_ms,
                    health_score=item.health_score,
                    health_status=item.health_status,
                )
        return RoutingTrace(
            RoutingTraceStatus.COMPLETED,
            tuple(self._items[key] for key in sorted(self._items)),
            objective=self._objective,
            policy_version=policy_version,
            capabilities=self._capabilities,
            constraints=self._constraints,
            budget=self._budget,
        )


class DeterministicRoutingPolicy:
    """Eligibility-first balanced, cost, and configured-latency routing."""

    POLICY_VERSION = "classification-v1"
    COST_POLICY_VERSION = "classification-cost-v1"
    LATENCY_POLICY_VERSION = "classification-cost-latency-v1"
    QUALITY_POLICY_VERSION = "classification-cost-latency-quality-v1"
    BUDGET_POLICY_VERSION = "classification-cost-latency-quality-budget-v1"
    SUPPORTED_OBJECTIVES = frozenset({"balanced", "cost", "latency", "quality", "budget"})
    _DEFAULT_PROVIDER_BONUS = 5
    _CATEGORY_CAPABILITY_BONUS = 20
    _COMPLEXITY_CAPABILITY_BONUS = 10

    def route(
        self,
        request: RoutingRequest,
        candidates: tuple[RoutingCandidate, ...],
        *,
        default_provider_id: str | None,
    ) -> RoutingDecision:
        trace = _RoutingTraceCollector(request, candidates)
        if request.objective not in self.SUPPORTED_OBJECTIVES:
            raise RoutingError(
                RoutingErrorCategory.UNSUPPORTED_OBJECTIVE,
                "The requested routing objective is not supported yet.",
            )
        if request.objective == "budget" and request.max_budget_usd is None:
            raise RoutingError(RoutingErrorCategory.BUDGET_UNAVAILABLE, "A budget is required for budget routing.")
        if request.max_budget_usd is not None and request.historical_spend_usd is None:
            raise RoutingError(RoutingErrorCategory.BUDGET_UNAVAILABLE, "Historical usage is unavailable for budget routing.")

        constrained = tuple(
            candidate
            for candidate in candidates
            if candidate.provider_id == request.requested_provider_id
        ) if request.requested_provider_id else candidates
        if request.requested_provider_id:
            for candidate in candidates:
                if candidate.provider_id != request.requested_provider_id:
                    model_ids = candidate.model_ids or (request.requested_model_id or "no-model",)
                    for model_id in model_ids:
                        trace.record(
                            candidate.provider_id,
                            model_id,
                            CandidateEvaluationStatus.EXCLUDED,
                            RoutingExclusionReason.PROVIDER_NOT_REQUESTED,
                        )
        if request.requested_provider_id and not constrained:
            raise RoutingError(
                RoutingErrorCategory.UNKNOWN_PROVIDER,
                "The requested provider is not configured.",
            )
        eligible = self._eligible(request, constrained, trace)
        if not eligible:
            if request.requested_provider_id:
                self._raise_ineligibility(request, constrained[0])
            raise RoutingError(
                RoutingErrorCategory.NO_ELIGIBLE_PROVIDER,
                "No configured provider satisfies the request.",
            )
        health_eligible_list = []
        for candidate in eligible:
            if self._candidate_unavailable(candidate, request):
                model_ids = ((request.requested_model_id,) if request.requested_model_id else candidate.model_ids)
                for model_id in model_ids:
                    model_health = health_for_model(candidate.health, model_id)
                    trace.record(
                        candidate.provider_id,
                        model_id,
                        CandidateEvaluationStatus.EXCLUDED,
                        RoutingExclusionReason.PROVIDER_UNAVAILABLE,
                        latency_ms=configured_latency_ms(candidate.latency, model_id),
                        health_score=model_health.health_score,
                        health_status=model_health.status.value,
                    )
            else:
                health_eligible_list.append(candidate)
        health_eligible = tuple(health_eligible_list)
        if not health_eligible:
            if request.requested_provider_id and self._candidate_unavailable(eligible[0], request):
                raise RoutingError(RoutingErrorCategory.PROVIDER_UNHEALTHY, "The requested provider is unavailable for routing.")
            raise RoutingError(RoutingErrorCategory.NO_ELIGIBLE_PROVIDER, "No configured provider satisfies the request.")
        eligible = health_eligible

        needs_constraints = request.max_cost_usd is not None or request.max_latency_ms is not None or request.max_budget_usd is not None
        if request.objective in {"cost", "latency", "quality", "budget"} or needs_constraints:
            models = self._constrained_models(request, eligible, trace)
            winner = self._select_models(request, models, default_provider_id)
            policy_version = self._policy_version(request)
            if request.requested_provider_id:
                reason = f'Explicit provider "{winner.candidate.provider_id}" selected.'
            elif request.objective == "cost":
                reason = (
                    f'Provider "{winner.candidate.provider_id}" model "{winner.model_id}" selected '
                    "with the lowest estimated cost among eligible candidates."
                )
            elif request.objective == "latency":
                reason = (
                    f'Provider "{winner.candidate.provider_id}" model "{winner.model_id}" selected '
                    "because it has the lowest configured estimated latency among eligible candidates."
                )
            elif request.objective == "quality":
                reason = (
                    f'Provider "{winner.candidate.provider_id}" model "{winner.model_id}" selected '
                    "because it has the highest configured health score among eligible candidates."
                )
            elif request.objective == "budget":
                reason = (
                    f'Provider "{winner.candidate.provider_id}" model "{winner.model_id}" selected '
                    "as the lowest estimated-cost route within the remaining budget."
                )
            else:
                reason = self._reason(request, winner.candidate, self._preference_score(request, winner.candidate, default_provider_id), default_provider_id)
            return RoutingDecision(
                winner.candidate.provider_id,
                winner.model_id,
                reason,
                policy_version,
                winner.estimated_cost,
                winner.estimated_latency_ms,
                winner.health_score,
                trace.finish((winner.candidate.provider_id, winner.model_id), policy_version),
            )

        if request.requested_provider_id:
            winner = eligible[0]
            reason = f'Explicit provider "{winner.provider_id}" selected.'
            return RoutingDecision(
                winner.provider_id,
                request.requested_model_id or winner.model_ids[0],
                reason,
                self.POLICY_VERSION,
                trace=trace.finish((winner.provider_id, request.requested_model_id or winner.model_ids[0]), self.POLICY_VERSION),
            )

        winner, score = self._select_balanced(request, eligible, default_provider_id)
        return RoutingDecision(
            winner.provider_id,
            request.requested_model_id or winner.model_ids[0],
            self._reason(request, winner, score, default_provider_id),
            self.POLICY_VERSION,
            trace=trace.finish((winner.provider_id, request.requested_model_id or winner.model_ids[0]), self.POLICY_VERSION),
        )

    @staticmethod
    def _candidate_unavailable(candidate: RoutingCandidate, request: RoutingRequest) -> bool:
        model_ids = ((request.requested_model_id,) if request.requested_model_id else candidate.model_ids)
        return any(
            health_for_model(candidate.health, model_id).status.value == "unavailable"
            for model_id in model_ids
        ) and all(
            health_for_model(candidate.health, model_id).status.value == "unavailable"
            for model_id in model_ids
        )

    def _constrained_models(
        self, request: RoutingRequest, eligible: tuple[RoutingCandidate, ...], trace=None
    ) -> tuple[_ModelCandidate, ...]:
        models: list[_ModelCandidate] = []
        for candidate in eligible:
            model_ids = (
                (request.requested_model_id,)
                if request.requested_model_id
                else candidate.model_ids
            )
            prices = {item.model_id: item for item in candidate.pricing}
            for model_id in model_ids:
                model_health = health_for_model(candidate.health, model_id)
                if model_health.status.value == "unavailable":
                    if trace is not None:
                        trace.record(
                            candidate.provider_id,
                            model_id,
                            CandidateEvaluationStatus.EXCLUDED,
                            RoutingExclusionReason.PROVIDER_UNAVAILABLE,
                            latency_ms=configured_latency_ms(candidate.latency, model_id),
                            health_score=model_health.health_score,
                            health_status=model_health.status.value,
                        )
                    continue
                price = prices.get(model_id)
                cost = None
                if price is not None and request.token_estimate is not None:
                    cost = estimated_cost_usd(
                        request.token_estimate,
                        price.input_usd_per_million_tokens,
                        price.output_usd_per_million_tokens,
                    )
                estimated_latency = configured_latency_ms(candidate.latency, model_id)
                health_score = model_health.health_score
                health_status = model_health.status.value
                models.append(
                    _ModelCandidate(
                        candidate, model_id, cost,
                        estimated_latency,
                        health_score,
                        health_status,
                    )
                )
                if trace is not None:
                    trace.record(
                        candidate.provider_id,
                        model_id,
                        CandidateEvaluationStatus.ELIGIBLE,
                        cost=cost,
                        latency_ms=estimated_latency,
                        health_score=health_score,
                        health_status=health_status,
                    )

        if request.max_cost_usd is not None:
            known = tuple(item for item in models if item.estimated_cost is not None)
            if not known:
                if trace is not None:
                    for item in models:
                        trace.mark_excluded(item, RoutingExclusionReason.COST_UNAVAILABLE)
                raise RoutingError(RoutingErrorCategory.COST_UNAVAILABLE, "No eligible candidate has configured pricing for this request.")
            unavailable = tuple(item for item in models if item.estimated_cost is None)
            models = [item for item in known if item.estimated_cost <= request.max_cost_usd]
            excluded = tuple(item for item in known if item not in models)
            if trace is not None:
                for item in unavailable:
                    trace.mark_excluded(item, RoutingExclusionReason.COST_UNAVAILABLE)

                for item in excluded:
                    trace.mark_excluded(item, RoutingExclusionReason.COST_LIMIT_EXCEEDED)
            if not models:
                raise RoutingError(RoutingErrorCategory.COST_LIMIT_EXCEEDED, "No eligible candidate satisfies the configured cost ceiling.")

        if request.max_latency_ms is not None:
            known = tuple(item for item in models if item.estimated_latency_ms is not None)
            if not known:
                if trace is not None:
                    for item in models:
                        trace.mark_excluded(item, RoutingExclusionReason.LATENCY_UNAVAILABLE)
                raise RoutingError(RoutingErrorCategory.LATENCY_UNAVAILABLE, "No eligible candidate has configured latency for this request.")
            unavailable = tuple(item for item in models if item.estimated_latency_ms is None)
            models = [item for item in known if item.estimated_latency_ms <= request.max_latency_ms]
            excluded = tuple(item for item in known if item not in models)
            if trace is not None:
                for item in unavailable:
                    trace.mark_excluded(item, RoutingExclusionReason.LATENCY_UNAVAILABLE)

                for item in excluded:
                    trace.mark_excluded(item, RoutingExclusionReason.LATENCY_LIMIT_EXCEEDED)
            if not models:
                raise RoutingError(RoutingErrorCategory.LATENCY_LIMIT_EXCEEDED, "No eligible candidate satisfies the configured latency ceiling.")

        if request.max_budget_usd is not None:
            remaining = request.max_budget_usd - request.historical_spend_usd
            known = tuple(item for item in models if item.estimated_cost is not None)
            if not known:
                if trace is not None:
                    for item in models:
                        trace.mark_excluded(item, RoutingExclusionReason.BUDGET_UNAVAILABLE)
                raise RoutingError(RoutingErrorCategory.BUDGET_UNAVAILABLE, "No eligible candidate has a usable estimated cost for this budget.")
            unavailable = tuple(item for item in models if item.estimated_cost is None)
            models = [item for item in known if item.estimated_cost <= remaining]
            excluded = tuple(item for item in known if item not in models)
            if trace is not None:
                for item in unavailable:
                    trace.mark_excluded(item, RoutingExclusionReason.BUDGET_UNAVAILABLE)

                for item in excluded:
                    trace.mark_excluded(item, RoutingExclusionReason.BUDGET_LIMIT_EXCEEDED)
            if not models:
                category = RoutingErrorCategory.BUDGET_EXHAUSTED if remaining < 0 else RoutingErrorCategory.BUDGET_LIMIT_EXCEEDED
                raise RoutingError(category, "No eligible route fits within the remaining budget.")

        if not models:
            if request.objective == "cost":
                raise RoutingError(RoutingErrorCategory.COST_UNAVAILABLE, "No eligible candidate has configured pricing for cost routing.")
            if request.objective == "latency":
                raise RoutingError(RoutingErrorCategory.LATENCY_UNAVAILABLE, "No eligible candidate has configured latency for latency routing.")
        return tuple(models)

    def _select_models(self, request, models, default_provider_id):
        if request.objective == "quality":
            usable = [item for item in models if item.health_score is not None]
            if not usable:
                raise RoutingError(RoutingErrorCategory.QUALITY_UNAVAILABLE, "No eligible candidate has configured health for quality routing.")
            return min(usable, key=lambda item: (-item.health_score, item.candidate.provider_id, item.model_id))
        if request.objective == "cost":
            usable = [item for item in models if item.estimated_cost is not None]
            if not usable:
                raise RoutingError(RoutingErrorCategory.COST_UNAVAILABLE, "No eligible candidate has configured pricing for cost routing.")
            return min(usable, key=lambda item: (item.estimated_cost, item.candidate.provider_id, item.model_id))
        if request.objective == "latency":
            usable = [item for item in models if item.estimated_latency_ms is not None]
            if not usable:
                raise RoutingError(RoutingErrorCategory.LATENCY_UNAVAILABLE, "No eligible candidate has configured latency for latency routing.")
            return min(usable, key=lambda item: (item.estimated_latency_ms, item.candidate.provider_id, item.model_id))
        if request.objective == "budget":
            usable = [item for item in models if item.estimated_cost is not None]
            if not usable:
                raise RoutingError(RoutingErrorCategory.BUDGET_UNAVAILABLE, "No eligible candidate has configured pricing for budget routing.")
            return min(usable, key=lambda item: (item.estimated_cost, item.candidate.provider_id, item.model_id))
        return min(
            models,
            key=lambda item: (
                -self._preference_score(request, item.candidate, default_provider_id).total,
                item.candidate.provider_id,
                item.model_id,
            ),
        )

    def fallback_options(
        self,
        request: RoutingRequest,
        candidates: tuple[RoutingCandidate, ...],
        *,
        default_provider_id: str | None,
        attempted: frozenset[tuple[str, str]],
    ) -> tuple[tuple[RoutingCandidate, str], ...]:
        """Return bounded deterministic alternatives without making a new route decision."""

        eligible = self._eligible(request, candidates)
        eligible = tuple(
            candidate
            for candidate in eligible
            if not self._candidate_unavailable(candidate, request)
        )
        try:
            models = self._constrained_models(request, eligible)
        except RoutingError:
            return ()
        models = tuple(
            item for item in models
            if (item.candidate.provider_id, item.model_id) not in attempted
        )
        if request.objective == "quality":
            models = tuple(item for item in models if item.health_score is not None)
            key = lambda item: (-item.health_score, item.candidate.provider_id, item.model_id)
        elif request.objective == "cost":
            models = tuple(item for item in models if item.estimated_cost is not None)
            key = lambda item: (item.estimated_cost, item.candidate.provider_id, item.model_id)
        elif request.objective == "latency":
            models = tuple(item for item in models if item.estimated_latency_ms is not None)
            key = lambda item: (item.estimated_latency_ms, item.candidate.provider_id, item.model_id)
        else:
            key = lambda item: (
                -self._preference_score(request, item.candidate, default_provider_id).total,
                item.candidate.provider_id,
                item.model_id,
            )
        return tuple((item.candidate, item.model_id) for item in sorted(models, key=key))

    def _policy_version(self, request: RoutingRequest) -> str:
        if request.objective == "budget" or request.max_budget_usd is not None:
            return self.BUDGET_POLICY_VERSION
        if request.objective == "quality":
            return self.QUALITY_POLICY_VERSION
        if request.objective == "latency" or request.max_latency_ms is not None:
            return self.LATENCY_POLICY_VERSION
        if request.objective == "cost" or request.max_cost_usd is not None:
            return self.COST_POLICY_VERSION
        return self.POLICY_VERSION

    def _select_balanced(self, request, eligible, default_provider_id):
        scored = tuple((candidate, self._preference_score(request, candidate, default_provider_id)) for candidate in eligible)
        return min(scored, key=lambda item: (-item[1].total, item[0].provider_id))

    def _preference_score(self, request, candidate, default_provider_id):
        classification = request.classification
        category_score = complexity_score = 0
        if classification is not None:
            preferred = self._category_capability(classification.category)
            if preferred is not None and preferred in candidate.capabilities:
                category_score = self._CATEGORY_CAPABILITY_BONUS
            if classification.complexity_level is ComplexityLevel.HIGH and Capability.REASONING in candidate.capabilities:
                complexity_score = self._COMPLEXITY_CAPABILITY_BONUS
        return _PreferenceScore(category_score, complexity_score, self._DEFAULT_PROVIDER_BONUS if candidate.provider_id == default_provider_id else 0)

    @staticmethod
    def _category_capability(category):
        if category in {RequestCategory.CODING, RequestCategory.DEBUGGING}:
            return Capability.CODING
        if category in {RequestCategory.REASONING, RequestCategory.ARCHITECTURE_DESIGN}:
            return Capability.REASONING
        return None

    def _reason(self, request, winner, winner_score, default_provider_id):
        classification = request.classification
        if classification is not None and (winner_score.classification_match_score or winner_score.complexity_match_score):
            return (f'Classified as {classification.category.value} with {classification.complexity_level.value} complexity; '
                    f'provider "{winner.provider_id}" received the highest deterministic policy score among eligible candidates using declared capability and complexity preferences.')
        if winner.provider_id == default_provider_id:
            return f'Default provider "{winner.provider_id}" selected because no provider was explicitly requested.'
        return f'Provider "{winner.provider_id}" selected because the configured default was not eligible and it is the deterministic tie-breaker.'

    @staticmethod
    def _eligible(request, candidates, trace=None):
        eligible = []
        for candidate in candidates:
            model_ids = ((request.requested_model_id,) if request.requested_model_id else candidate.model_ids)
            model_id = model_ids[0] if model_ids else "no-model"
            if not request.required_capabilities <= candidate.capabilities:
                if trace is not None:
                    trace.record(candidate.provider_id, model_id, CandidateEvaluationStatus.EXCLUDED, RoutingExclusionReason.UNSUPPORTED_CAPABILITY)
                continue
            if request.requested_model_id and request.requested_model_id not in candidate.model_ids:
                if trace is not None:
                    trace.record(candidate.provider_id, request.requested_model_id, CandidateEvaluationStatus.EXCLUDED, RoutingExclusionReason.MODEL_NOT_SUPPORTED)
                continue
            if not candidate.model_ids:
                if trace is not None:
                    trace.record(candidate.provider_id, model_id, CandidateEvaluationStatus.EXCLUDED, RoutingExclusionReason.NO_MODEL)
                continue
            eligible.append(candidate)
            if trace is not None:
                for candidate_model_id in model_ids:
                    trace.record(candidate.provider_id, candidate_model_id, CandidateEvaluationStatus.ELIGIBLE)
        return tuple(eligible)

    @staticmethod
    def _raise_ineligibility(request, candidate):
        if not request.required_capabilities <= candidate.capabilities:
            raise RoutingError(RoutingErrorCategory.UNSUPPORTED_CAPABILITY, "The requested provider does not support the required capability.")
        if request.requested_model_id and request.requested_model_id not in candidate.model_ids:
            raise RoutingError(RoutingErrorCategory.MODEL_NOT_SUPPORTED, "The requested provider does not support the requested model.")
        raise RoutingError(RoutingErrorCategory.NO_ELIGIBLE_PROVIDER, "The requested provider is not eligible for this request.")
