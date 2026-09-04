import time

from fastapi import APIRouter, Depends, Request

from gateway.api.errors import GatewayAPIError
from gateway.api.schemas import (
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    ProviderInfo,
    ResponseMessage,
    RoutingMetadata,
)
from gateway.application.chat_service import ChatExecutionResult, ChatService
from gateway.application.context import RequestContext
from gateway.domain.provider import ProviderError, ProviderErrorCategory
from gateway.domain.routing import RoutingError, RoutingErrorCategory

router = APIRouter(prefix="/api/v1", tags=["gateway"])
DEFAULT_TIMEOUT_MS = 60_000
MAX_TIMEOUT_MS = 120_000


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def _header_timeout(request: Request) -> int | None:
    raw_timeout = request.headers.get("X-Request-Timeout-Ms")
    if raw_timeout is None:
        return None
    try:
        timeout = int(raw_timeout)
    except ValueError as exc:
        raise GatewayAPIError(
            code="invalid_request",
            message="X-Request-Timeout-Ms must be a positive integer.",
            status_code=400,
        ) from exc
    if timeout <= 0 or timeout > MAX_TIMEOUT_MS:
        raise GatewayAPIError(
            code="invalid_request",
            message="The request timeout must be between 1 and 120000 milliseconds.",
            status_code=400,
        )
    return timeout


def _provider_error(error: ProviderError) -> GatewayAPIError:
    mapping = {
        ProviderErrorCategory.INVALID_REQUEST: (400, False),
        ProviderErrorCategory.UNSUPPORTED_CAPABILITY: (422, False),
        ProviderErrorCategory.TIMEOUT: (504, True),
        ProviderErrorCategory.UNAVAILABLE: (503, True),
        ProviderErrorCategory.RATE_LIMIT: (429, True),
    }
    status_code, retryable = mapping.get(error.category, (502, False))
    public_codes = {
        ProviderErrorCategory.INVALID_REQUEST: "invalid_request",
        ProviderErrorCategory.UNSUPPORTED_CAPABILITY: "unsupported_capability",
        ProviderErrorCategory.TIMEOUT: "gateway_timeout",
        ProviderErrorCategory.UNAVAILABLE: "all_providers_unavailable",
        ProviderErrorCategory.RATE_LIMIT: "rate_limited",
    }
    return GatewayAPIError(
        code=public_codes.get(error.category, "provider_error"),
        message="The provider could not complete the request.",
        status_code=status_code,
        retryable=retryable,
    )


def _routing_error(error: RoutingError) -> GatewayAPIError:
    if error.category is RoutingErrorCategory.UNSUPPORTED_CAPABILITY:
        return GatewayAPIError(
            code="unsupported_capability",
            message="The requested provider does not support the required capability.",
            status_code=422,
        )
    if error.category is RoutingErrorCategory.MODEL_NOT_SUPPORTED:
        return GatewayAPIError(
            code="model_not_supported",
            message="The requested provider does not support the requested model.",
            status_code=422,
        )
    if error.category is RoutingErrorCategory.COST_UNAVAILABLE:
        return GatewayAPIError(
            code="cost_unavailable",
            message="No eligible provider has configured pricing for this request.",
            status_code=422,
        )
    if error.category is RoutingErrorCategory.COST_LIMIT_EXCEEDED:
        return GatewayAPIError(
            code="cost_limit_exceeded",
            message="No eligible provider satisfies the configured cost ceiling.",
            status_code=422,
        )
    if error.category is RoutingErrorCategory.LATENCY_UNAVAILABLE:
        return GatewayAPIError(
            code="latency_unavailable",
            message="No eligible provider has configured latency for this request.",
            status_code=422,
        )
    if error.category is RoutingErrorCategory.LATENCY_LIMIT_EXCEEDED:
        return GatewayAPIError(
            code="latency_limit_exceeded",
            message="No eligible provider satisfies the configured latency ceiling.",
            status_code=422,
        )
    if error.category is RoutingErrorCategory.UNSUPPORTED_OBJECTIVE:
        return GatewayAPIError(
            code="invalid_request",
            message="The requested routing objective is not supported yet.",
            status_code=400,
        )
    return GatewayAPIError(
        code="all_providers_unavailable",
        message="No eligible provider is available for this request.",
        status_code=503,
        retryable=True,
    )


def _to_api_response(
    execution: ChatExecutionResult, request_id: str, latency_ms: int
) -> ChatResponse:
    provider_response = execution.provider_response
    decision = execution.routing_decision
    return ChatResponse(
        id=request_id,
        object="chat.response",
        status="completed",
        message=ResponseMessage(
            role="assistant", content=provider_response.message.content
        ),
        provider=ProviderInfo(
            id=provider_response.provider_id, model=provider_response.model
        ),
        finish_reason=provider_response.finish_reason,
        usage=(
            provider_response.usage.model_dump() if provider_response.usage else None
        ),
        latency_ms=latency_ms,
        routing=RoutingMetadata(
            policy_version=decision.policy_version,
            decision_reason=decision.reason,
            fallback_used=False,
            category=execution.classification.category.value,
            complexity_level=execution.classification.complexity_level.value,
            complexity_score=execution.classification.complexity_score,
            estimated_cost_usd=(
                float(execution.routing_decision.estimated_cost_usd)
                if execution.routing_decision.estimated_cost_usd is not None
                else None
            ),
            estimated_latency_ms=execution.routing_decision.estimated_latency_ms,
        ),
        request_id=request_id,
    )


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
    },
)
def chat(
    request: Request,
    body: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    header_timeout = _header_timeout(request)
    if header_timeout is not None and body.timeout_ms is not None:
        raise GatewayAPIError(
            code="invalid_request",
            message="Specify the request timeout in the body or header, not both.",
            status_code=400,
        )

    if body.stream:
        raise GatewayAPIError(
            code="streaming_not_supported",
            message="Streaming is not supported by the initial v1 implementation.",
            status_code=501,
        )

    timeout_ms = body.timeout_ms or header_timeout or DEFAULT_TIMEOUT_MS
    started = time.perf_counter()
    context = RequestContext(
        request_id=request.state.request_id,
        timeout_ms=timeout_ms,
        deadline_monotonic=started + (timeout_ms / 1000),
    )
    try:
        execution = service.complete(body, context)
    except RoutingError as error:
        raise _routing_error(error) from error
    except ProviderError as error:
        raise _provider_error(error) from error
    except LookupError as error:
        raise GatewayAPIError(
            code="all_providers_unavailable",
            message="No provider is available for this request.",
            status_code=503,
            retryable=True,
        ) from error
    latency_ms = round((time.perf_counter() - started) * 1000)
    return _to_api_response(execution, context.request_id, latency_ms)
