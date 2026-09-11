"""Application-facing durable usage tracking port and safe orchestration."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Protocol

from gateway.application.chat_service import ChatExecutionResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageRecord:
    request_id: str
    principal_id: str | None
    provider_id: str | None
    model_id: str | None
    outcome: str
    error_code: str | None = None
    error_category: str | None = None
    http_status: int | None = None
    attempt_count: int = 0
    fallback_used: bool = False
    routing_objective: str | None = None
    routing_policy_version: str | None = None
    classification_category: str | None = None
    classification_complexity_level: str | None = None
    classification_complexity_score: int | None = None
    estimated_cost_usd: Decimal | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_latency_ms: int | None = None
    actual_gateway_latency_ms: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class UsageRecordRepository(Protocol):
    def record_usage(self, record: UsageRecord) -> None:
        ...


class UsageRecorder:
    def __init__(self, repository: UsageRecordRepository | None, enabled: bool) -> None:
        self._repository = repository
        self._enabled = enabled

    def record(self, record: UsageRecord) -> None:
        if not self._enabled or self._repository is None:
            return
        try:
            self._repository.record_usage(record)
        except Exception as exc:  # persistence must never change the gateway result
            logger.warning(
                "usage_persistence_failed request_id=%s category=%s",
                record.request_id,
                type(exc).__name__,
            )


def principal_id_from_request(request) -> str | None:
    principal = getattr(request.state, "authenticated_principal", None)
    return principal.key_id if principal is not None else None


def record_success(request, execution: ChatExecutionResult, latency_ms: int, objective: str | None) -> None:
    response = execution.provider_response
    usage = response.usage
    decision = execution.routing_decision
    recorder: UsageRecorder = request.app.state.usage_recorder
    recorder.record(
        UsageRecord(
            request_id=request.state.request_id,
            principal_id=principal_id_from_request(request),
            provider_id=response.provider_id,
            model_id=response.model,
            outcome="success",
            http_status=200,
            attempt_count=execution.attempt_count,
            fallback_used=execution.fallback_used,
            routing_objective=objective,
            routing_policy_version=decision.policy_version,
            classification_category=execution.classification.category.value,
            classification_complexity_level=execution.classification.complexity_level.value,
            classification_complexity_score=execution.classification.complexity_score,
            estimated_cost_usd=decision.estimated_cost_usd,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            estimated_latency_ms=decision.estimated_latency_ms,
            actual_gateway_latency_ms=latency_ms,
        )
    )
    request.state.usage_recorded = True


def record_error(
    request, code: str, status_code: int, message: str, error_category: str | None = None
) -> None:
    """Persist only normalized, non-sensitive error metadata."""
    _ = message
    if getattr(request.state, "usage_recorded", False):
        return
    if code == "authentication_required":
        outcome = "authentication_failed"
    elif code == "rate_limited":
        outcome = "rate_limited"
    elif code == "invalid_request":
        outcome = "validation_failed"
    else:
        outcome = "failed"
    recorder: UsageRecorder | None = getattr(request.app.state, "usage_recorder", None)
    if recorder is None:
        return
    execution = getattr(request.state, "usage_execution", {})
    recorder.record(
        UsageRecord(
            request_id=request.state.request_id,
            principal_id=principal_id_from_request(request),
            provider_id=execution.get("provider_id"),
            model_id=execution.get("model_id"),
            outcome=outcome,
            error_code=code,
            error_category=error_category,
            http_status=status_code,
            attempt_count=int(execution.get("attempt_count", 0)),
            fallback_used=bool(execution.get("fallback_used", False)),
        )
    )
    request.state.usage_recorded = True
