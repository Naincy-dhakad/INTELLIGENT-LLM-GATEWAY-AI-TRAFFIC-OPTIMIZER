"""Safe, normalized event vocabulary for gateway observability.

This module intentionally contains no logging, HTTP, database, Redis, provider
SDK, or telemetry-library imports. Events accept only an allow-listed set of
normalized operational attributes.
"""

from dataclasses import dataclass
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping


class EventType(StrEnum):
    REQUEST_STARTED = "gateway_request_started"
    AUTHENTICATION_RESULT = "gateway_authentication_result"
    RATE_LIMIT_RESULT = "gateway_rate_limit_result"
    REQUEST_VALIDATION_RESULT = "gateway_request_validation_result"
    CLASSIFICATION_COMPLETED = "gateway_classification_completed"
    BUDGET_DECISION = "gateway_budget_decision"
    ROUTING_DECISION = "gateway_routing_decision"
    PROVIDER_ATTEMPT = "gateway_provider_attempt"
    RETRY_SCHEDULED = "gateway_retry_scheduled"
    FALLBACK_SELECTED = "gateway_fallback_selected"
    REQUEST_COMPLETED = "gateway_request_completed"
    USAGE_PERSISTENCE_FAILED = "gateway_usage_persistence_failed"


class AuthenticationOutcome(StrEnum):
    DISABLED = "disabled"
    SUCCESS = "success"
    FAILURE = "failure"


class EventOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    REJECTED = "rejected"


_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FORBIDDEN = frozenset(
    {
        "api_key", "x_api_key", "authorization", "password", "token",
        "prompt", "completion", "request_body", "response_body",
        "provider_response", "provider_exception", "database_url", "redis_url",
        "credentials", "session_cookie", "session_cookies",
    }
)

# Only these normalized fields can be emitted for each event. This also stops
# callers from using the event API as an arbitrary structured logging channel.
_ALLOWED_FIELDS: dict[EventType, frozenset[str]] = {
    EventType.REQUEST_STARTED: frozenset({"route", "method", "environment"}),
    EventType.AUTHENTICATION_RESULT: frozenset({"outcome", "principal_id", "route"}),
    EventType.RATE_LIMIT_RESULT: frozenset({"outcome", "limit", "window_seconds", "retry_after_seconds"}),
    EventType.REQUEST_VALIDATION_RESULT: frozenset({"outcome", "error_code", "status_code"}),
    EventType.CLASSIFICATION_COMPLETED: frozenset({"category", "complexity_level", "complexity_score", "policy_version"}),
    EventType.BUDGET_DECISION: frozenset({"outcome", "error_code", "objective", "policy_version"}),
    EventType.ROUTING_DECISION: frozenset({"outcome", "objective", "policy_version", "provider_id", "model_id", "reason_code", "estimated_cost_usd", "estimated_latency_ms", "health_score"}),
    EventType.PROVIDER_ATTEMPT: frozenset({"provider_id", "model_id", "attempt_number", "attempt_role", "outcome", "error_category", "latency_ms", "timeout_ms"}),
    EventType.RETRY_SCHEDULED: frozenset({"provider_id", "model_id", "attempt_number", "error_category", "delay_ms"}),
    EventType.FALLBACK_SELECTED: frozenset({"from_provider_id", "from_model_id", "to_provider_id", "to_model_id", "reason_code", "attempt_number"}),
    EventType.REQUEST_COMPLETED: frozenset({"outcome", "status_code", "provider_id", "model_id", "attempt_count", "fallback_used", "latency_ms", "error_code"}),
    EventType.USAGE_PERSISTENCE_FAILED: frozenset({"outcome", "error_category"}),
}


SafeValue = str | int | float | bool | None


@dataclass(frozen=True)
class ObservabilityEvent:
    event_type: EventType
    request_id: str
    attributes: Mapping[str, SafeValue]

    def __post_init__(self) -> None:
        if not _REQUEST_ID.fullmatch(self.request_id):
            raise ValueError("request_id must be a bounded safe identifier")
        allowed = _ALLOWED_FIELDS[self.event_type]
        attributes = dict(self.attributes)
        for key, value in attributes.items():
            if not _SAFE_KEY.fullmatch(key) or key in _FORBIDDEN:
                raise ValueError(f"unsafe observability field: {key}")
            if key not in allowed:
                raise ValueError(f"field {key} is not allowed for {self.event_type.value}")
            if isinstance(value, str) and len(value) > 256:
                raise ValueError(f"observability field {key} is too long")
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise TypeError(f"unsupported observability value for {key}")
        object.__setattr__(self, "attributes", MappingProxyType(attributes))

    def as_dict(self) -> dict[str, object]:
        return {
            "event": self.event_type.value,
            "request_id": self.request_id,
            **dict(self.attributes),
        }


def make_event(event_type: EventType, request_id: str, **attributes: SafeValue) -> ObservabilityEvent:
    """Construct an event through the allow-listed normalized schema."""
    return ObservabilityEvent(event_type, request_id, attributes)
