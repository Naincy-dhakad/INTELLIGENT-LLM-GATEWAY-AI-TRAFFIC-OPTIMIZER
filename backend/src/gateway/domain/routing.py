from dataclasses import dataclass
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


class DeterministicRoutingPolicy:
    """Eligibility-first balanced, cost, and configured-latency routing."""

    POLICY_VERSION = "classification-v1"
    COST_POLICY_VERSION = "classification-cost-v1"
    LATENCY_POLICY_VERSION = "classification-cost-latency-v1"
    QUALITY_POLICY_VERSION = "classification-cost-latency-quality-v1"
    SUPPORTED_OBJECTIVES = frozenset({"balanced", "cost", "latency", "quality"})
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
        if request.objective not in self.SUPPORTED_OBJECTIVES:
            raise RoutingError(
                RoutingErrorCategory.UNSUPPORTED_OBJECTIVE,
                "The requested routing objective is not supported yet.",
            )

        constrained = tuple(
            candidate
            for candidate in candidates
            if candidate.provider_id == request.requested_provider_id
        ) if request.requested_provider_id else candidates
        if request.requested_provider_id and not constrained:
            raise RoutingError(
                RoutingErrorCategory.UNKNOWN_PROVIDER,
                "The requested provider is not configured.",
            )
        eligible = self._eligible(request, constrained)
        if not eligible:
            if request.requested_provider_id:
                self._raise_ineligibility(request, constrained[0])
            raise RoutingError(
                RoutingErrorCategory.NO_ELIGIBLE_PROVIDER,
                "No configured provider satisfies the request.",
            )
        health_eligible = tuple(
            candidate for candidate in eligible if not self._candidate_unavailable(candidate, request)
        )
        if not health_eligible:
            if request.requested_provider_id and self._candidate_unavailable(eligible[0], request):
                raise RoutingError(RoutingErrorCategory.PROVIDER_UNHEALTHY, "The requested provider is unavailable for routing.")
            raise RoutingError(RoutingErrorCategory.NO_ELIGIBLE_PROVIDER, "No configured provider satisfies the request.")
        eligible = health_eligible

        needs_constraints = request.max_cost_usd is not None or request.max_latency_ms is not None
        if request.objective in {"cost", "latency", "quality"} or needs_constraints:
            models = self._constrained_models(request, eligible)
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
            )

        if request.requested_provider_id:
            winner = eligible[0]
            reason = f'Explicit provider "{winner.provider_id}" selected.'
            return RoutingDecision(
                winner.provider_id,
                request.requested_model_id or winner.model_ids[0],
                reason,
                self.POLICY_VERSION,
            )

        winner, score = self._select_balanced(request, eligible, default_provider_id)
        return RoutingDecision(
            winner.provider_id,
            request.requested_model_id or winner.model_ids[0],
            self._reason(request, winner, score, default_provider_id),
            self.POLICY_VERSION,
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
        self, request: RoutingRequest, eligible: tuple[RoutingCandidate, ...]
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
                    continue
                price = prices.get(model_id)
                cost = None
                if price is not None and request.token_estimate is not None:
                    cost = estimated_cost_usd(
                        request.token_estimate,
                        price.input_usd_per_million_tokens,
                        price.output_usd_per_million_tokens,
                    )
                models.append(
                    _ModelCandidate(
                        candidate, model_id, cost,
                        configured_latency_ms(candidate.latency, model_id),
                        health_for_model(candidate.health, model_id).health_score,
                        health_for_model(candidate.health, model_id).status.value,
                    )
                )

        if request.max_cost_usd is not None:
            known = tuple(item for item in models if item.estimated_cost is not None)
            if not known:
                raise RoutingError(RoutingErrorCategory.COST_UNAVAILABLE, "No eligible candidate has configured pricing for this request.")
            models = [item for item in known if item.estimated_cost <= request.max_cost_usd]
            if not models:
                raise RoutingError(RoutingErrorCategory.COST_LIMIT_EXCEEDED, "No eligible candidate satisfies the configured cost ceiling.")

        if request.max_latency_ms is not None:
            known = tuple(item for item in models if item.estimated_latency_ms is not None)
            if not known:
                raise RoutingError(RoutingErrorCategory.LATENCY_UNAVAILABLE, "No eligible candidate has configured latency for this request.")
            models = [item for item in known if item.estimated_latency_ms <= request.max_latency_ms]
            if not models:
                raise RoutingError(RoutingErrorCategory.LATENCY_LIMIT_EXCEEDED, "No eligible candidate satisfies the configured latency ceiling.")

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
    def _eligible(request, candidates):
        return tuple(candidate for candidate in candidates if request.required_capabilities <= candidate.capabilities
                     and (request.requested_model_id is None or request.requested_model_id in candidate.model_ids)
                     and candidate.model_ids)

    @staticmethod
    def _raise_ineligibility(request, candidate):
        if not request.required_capabilities <= candidate.capabilities:
            raise RoutingError(RoutingErrorCategory.UNSUPPORTED_CAPABILITY, "The requested provider does not support the required capability.")
        if request.requested_model_id and request.requested_model_id not in candidate.model_ids:
            raise RoutingError(RoutingErrorCategory.MODEL_NOT_SUPPORTED, "The requested provider does not support the requested model.")
        raise RoutingError(RoutingErrorCategory.NO_ELIGIBLE_PROVIDER, "The requested provider is not eligible for this request.")
