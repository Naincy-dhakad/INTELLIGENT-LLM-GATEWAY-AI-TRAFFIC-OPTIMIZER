from dataclasses import dataclass
from decimal import Decimal
import math
import time
from typing import Callable

from gateway.api.schemas import ChatRequest
from gateway.application.context import RequestContext
from gateway.application.retry_policy import RetryPolicy
from gateway.domain.classification import (
    ClassificationMessage,
    ClassificationResult,
    DeterministicRequestClassifier,
)
from gateway.domain.cost import CostMessage, estimate_token_counts
from gateway.domain.provider import (
    Capability,
    Provider,
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
    fallback_used: bool = False
    attempt_count: int = 1


class ChatService:
    """Orchestrates one route and bounded deadline-aware provider execution."""

    def __init__(
        self,
        registry: ProviderRegistry,
        routing_policy: DeterministicRoutingPolicy | None = None,
        classifier: DeterministicRequestClassifier | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._registry = registry
        self._routing_policy = routing_policy or DeterministicRoutingPolicy()
        self._classifier = classifier or DeterministicRequestClassifier()
        self._clock = clock
        self._sleeper = sleeper

    def complete(self, request: ChatRequest, context: RequestContext) -> ChatExecutionResult:
        required_capabilities = frozenset(
            Capability(capability)
            for capability in (request.requirements.capabilities if request.requirements else [])
        )
        classification = self._classifier.classify(
            tuple(ClassificationMessage(role=m.role, content=m.content) for m in request.messages)
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
                if request.routing and request.routing.max_cost_usd is not None else None
            ),
            max_latency_ms=(
                request.routing.max_latency_ms
                if request.routing and request.routing.max_latency_ms is not None else None
            ),
        )
        candidates = tuple(
            RoutingCandidate.from_metadata(provider.metadata)
            for provider in self._registry.list()
        )
        decision = self._routing_policy.route(
            routing_request, candidates,
            default_provider_id=self._registry.default_provider_id,
        )
        primary = self._registry.get(decision.selected_provider_id)
        if primary is None:
            raise LookupError("selected provider is no longer available")

        base_request = self._provider_request(request, required_capabilities, decision.selected_model_id, context)
        attempted = {(decision.selected_provider_id, decision.selected_model_id)}
        attempt_count = 0
        last_error: ProviderError | None = None

        # Initial attempt plus at most one retry of the same primary provider.
        for retry_number in range(RetryPolicy.MAX_TOTAL_ATTEMPTS - 1):
            attempt_count += 1
            try:
                response = self._attempt(primary, base_request, context)
                return ChatExecutionResult(response, decision, classification, False, attempt_count)
            except ProviderError as error:
                last_error = error
                if not RetryPolicy.is_retryable(error.category):
                    raise
                if attempt_count >= RetryPolicy.MAX_TOTAL_ATTEMPTS - 1:
                    break
                delay = RetryPolicy.delay_seconds(retry_number + 1)
                if self._remaining_seconds(context) <= delay:
                    break
                self._sleeper(delay)

        if (
            last_error is not None
            and RetryPolicy.is_retryable(last_error.category)
            and request.provider is None
            and attempt_count < RetryPolicy.MAX_TOTAL_ATTEMPTS
            and self._remaining_seconds(context) > 0
        ):
            alternatives = self._routing_policy.fallback_options(
                routing_request, candidates,
                default_provider_id=self._registry.default_provider_id,
                attempted=frozenset(attempted),
            )
            if alternatives:
                fallback_candidate, fallback_model = alternatives[0]
                attempted.add((fallback_candidate.provider_id, fallback_model))
                fallback_provider = self._registry.get(fallback_candidate.provider_id)
                if fallback_provider is not None:
                    attempt_count += 1
                    fallback_request = self._provider_request(
                        request, required_capabilities, fallback_model, context
                    )
                    try:
                        response = self._attempt(fallback_provider, fallback_request, context)
                        return ChatExecutionResult(response, decision, classification, True, attempt_count)
                    except ProviderError as error:
                        last_error = error

        if last_error is not None:
            raise last_error
        raise ProviderError(
            category=ProviderErrorCategory.TIMEOUT,
            message="The request deadline expired before provider execution.",
        )

    def _provider_request(self, request, required_capabilities, model, context):
        return ProviderChatRequest(
            messages=tuple(ProviderMessage(role=m.role, content=m.content) for m in request.messages),
            model=model,
            generation=(
                ProviderGenerationSettings(**request.generation.model_dump())
                if request.generation else None
            ),
            timeout_ms=context.timeout_ms,
            deadline_monotonic=context.deadline_monotonic,
            required_capabilities=required_capabilities,
        )

    def _attempt(self, provider: Provider, request: ProviderChatRequest, context: RequestContext):
        remaining_ms = math.ceil(self._remaining_seconds(context) * 1000)
        if remaining_ms <= 0:
            raise ProviderError(
                category=ProviderErrorCategory.TIMEOUT,
                message="The request deadline expired before provider execution.",
            )
        bounded_request = request.model_copy(
            update={"timeout_ms": min(request.timeout_ms, remaining_ms)}
        )
        return provider.chat(bounded_request)

    def _remaining_seconds(self, context: RequestContext) -> float:
        return max(0.0, context.deadline_monotonic - self._clock())
