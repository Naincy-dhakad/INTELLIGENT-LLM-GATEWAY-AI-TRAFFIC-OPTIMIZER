from dataclasses import dataclass
from decimal import Decimal

from gateway.api.schemas import ChatRequest
from gateway.application.context import RequestContext
from gateway.domain.cost import CostMessage, estimate_token_counts
from gateway.domain.classification import (
    ClassificationMessage,
    ClassificationResult,
    DeterministicRequestClassifier,
)
from gateway.domain.provider import (
    Capability,
    ProviderChatRequest,
    ProviderChatResponse,
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerationSettings,
    ProviderMessage,
)
from gateway.domain.provider_registry import ProviderRegistry
from gateway.domain.routing import (
    DeterministicRoutingPolicy,
    RoutingCandidate,
    RoutingDecision,
    RoutingRequest,
)


@dataclass(frozen=True)
class ChatExecutionResult:
    provider_response: ProviderChatResponse
    routing_decision: RoutingDecision
    classification: ClassificationResult


class ChatService:
    """Orchestrates validation output, deterministic routing, and provider execution."""

    def __init__(
        self,
        registry: ProviderRegistry,
        routing_policy: DeterministicRoutingPolicy | None = None,
        classifier: DeterministicRequestClassifier | None = None,
    ) -> None:
        self._registry = registry
        self._routing_policy = routing_policy or DeterministicRoutingPolicy()
        self._classifier = classifier or DeterministicRequestClassifier()

    def complete(self, request: ChatRequest, context: RequestContext) -> ChatExecutionResult:
        required_capabilities = frozenset(
            Capability(capability)
            for capability in (
                request.requirements.capabilities if request.requirements else []
            )
        )
        classification = self._classifier.classify(
            tuple(
                ClassificationMessage(role=message.role, content=message.content)
                for message in request.messages
            )
        )
        token_estimate = estimate_token_counts(
            tuple(CostMessage(message.content) for message in request.messages),
            request.generation.max_output_tokens if request.generation else None,
        )
        routing_request = RoutingRequest(
            requested_provider_id=request.provider,
            requested_model_id=request.model,
            required_capabilities=required_capabilities,
            objective=request.routing.objective if request.routing else "balanced",
            classification=classification,
            token_estimate=token_estimate,
            max_cost_usd=(
                Decimal(str(request.routing.max_cost_usd))
                if request.routing and request.routing.max_cost_usd is not None
                else None
            ),
            max_latency_ms=(
                request.routing.max_latency_ms
                if request.routing and request.routing.max_latency_ms is not None
                else None
            ),
        )
        candidates = tuple(
            RoutingCandidate.from_metadata(provider.metadata)
            for provider in self._registry.list()
        )
        decision = self._routing_policy.route(
            routing_request,
            candidates,
            default_provider_id=self._registry.default_provider_id,
        )
        provider = self._registry.get(decision.selected_provider_id)
        if provider is None:
            # Registry contents are the only source of candidates; this guards
            # against mutation between decision and execution.
            raise LookupError("selected provider is no longer available")

        provider_request = ProviderChatRequest(
            messages=tuple(
                ProviderMessage(role=message.role, content=message.content)
                for message in request.messages
            ),
            model=decision.selected_model_id,
            generation=(
                ProviderGenerationSettings(**request.generation.model_dump())
                if request.generation
                else None
            ),
            timeout_ms=context.timeout_ms,
            deadline_monotonic=context.deadline_monotonic,
            required_capabilities=required_capabilities,
        )
        unsupported = required_capabilities - provider.metadata.capabilities
        if unsupported:
            raise ProviderError(
                category=ProviderErrorCategory.UNSUPPORTED_CAPABILITY,
                message="The selected provider does not support the required capability.",
            )
        return ChatExecutionResult(
            provider_response=provider.chat(provider_request),
            routing_decision=decision,
            classification=classification,
        )
