"""Technology-neutral metric definitions for the gateway.

This module defines the application metrics contract only. It has no exporter,
network, persistence, or metrics-library dependency.
"""

from dataclasses import dataclass, field
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping


class MetricType(StrEnum):
    COUNTER = "counter"
    HISTOGRAM = "histogram"


_GATEWAY_NAME = re.compile(r"^gateway_[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:/-]{0,127}$")
_METRIC_MAX_LENGTH = 128
_LABEL_MAX_LENGTH = 128

GATEWAY_REQUEST_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)
PROVIDER_ATTEMPT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


def validate_metric_name(name: str) -> None:
    if not isinstance(name, str) or len(name) > _METRIC_MAX_LENGTH or not _GATEWAY_NAME.fullmatch(name):
        raise ValueError("metric name must be a bounded lowercase gateway snake_case name")


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    metric_type: MetricType
    description: str
    allowed_labels: frozenset[str] = frozenset()
    label_values: Mapping[str, frozenset[str]] = field(default_factory=dict)
    buckets: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        validate_metric_name(self.name)
        if self.metric_type is MetricType.HISTOGRAM and not self.buckets:
            raise ValueError("histograms require bounded buckets")
        if any(left >= right for left, right in zip(self.buckets, self.buckets[1:])):
            raise ValueError("histogram buckets must be strictly increasing")
        if not set(self.label_values).issubset(self.allowed_labels):
            raise ValueError("bounded label values must belong to allowed labels")


_METRIC_DEFINITIONS = (
    MetricDefinition("gateway_requests_total", MetricType.COUNTER, "Total gateway requests.", frozenset({"route", "outcome", "status_class"})),
    MetricDefinition("gateway_request_duration_seconds", MetricType.HISTOGRAM, "End-to-end gateway request duration in seconds.", frozenset({"route", "outcome", "status_class"}), buckets=GATEWAY_REQUEST_BUCKETS),
    MetricDefinition("gateway_authentication_results_total", MetricType.COUNTER, "Authentication outcomes.", frozenset({"outcome", "route", "status_class"})),
    MetricDefinition("gateway_rate_limit_results_total", MetricType.COUNTER, "Rate-limit outcomes.", frozenset({"outcome", "route", "status_class"})),
    MetricDefinition("gateway_classification_total", MetricType.COUNTER, "Completed classifications.", frozenset({"category", "complexity_level", "policy_version"})),
    MetricDefinition("gateway_budget_decisions_total", MetricType.COUNTER, "Budget decisions.", frozenset({"outcome", "objective", "policy_version", "error_code"})),
    MetricDefinition("gateway_routing_decisions_total", MetricType.COUNTER, "Routing decisions.", frozenset({"outcome", "objective", "policy_version", "reason_code", "provider_id", "model_id"})),
    MetricDefinition("gateway_provider_attempts_total", MetricType.COUNTER, "Actual provider attempts.", frozenset({"provider_id", "model_id", "attempt_role", "outcome"})),
    MetricDefinition("gateway_provider_attempt_duration_seconds", MetricType.HISTOGRAM, "Actual provider attempt duration in seconds.", frozenset({"provider_id", "model_id", "attempt_role", "outcome"}), buckets=PROVIDER_ATTEMPT_BUCKETS),
    MetricDefinition("gateway_retries_scheduled_total", MetricType.COUNTER, "Retries actually scheduled.", frozenset({"provider_id", "model_id", "error_category", "attempt_number"})),
    MetricDefinition("gateway_fallbacks_selected_total", MetricType.COUNTER, "Fallbacks actually selected.", frozenset({"from_provider_id", "to_provider_id", "reason_code", "attempt_number"})),
    MetricDefinition("gateway_validation_results_total", MetricType.COUNTER, "Request validation results.", frozenset({"outcome", "status_class", "error_code"})),
    MetricDefinition("gateway_errors_total", MetricType.COUNTER, "Normalized gateway errors.", frozenset({"error_code", "status_class", "error_domain"})),
    # Compatibility with the existing foundation test/local usage. It is not a
    # first-phase application metric and has no instrumentation attached.
    MetricDefinition("gateway_latency_seconds", MetricType.HISTOGRAM, "Legacy local histogram name.", frozenset({"provider_id"}), buckets=PROVIDER_ATTEMPT_BUCKETS),
)

METRIC_CATALOG: Mapping[str, MetricDefinition] = MappingProxyType({item.name: item for item in _METRIC_DEFINITIONS})

_FORBIDDEN_LABELS = frozenset({
    "request_id", "api_key", "api_key_hash", "x_api_key", "principal_id", "user_id",
    "authorization", "prompt", "completion", "request_body", "response_body",
    "provider_exception", "raw_exception", "error_message", "database_url", "redis_url",
    "credentials", "exact_budget", "accumulated_spend", "remaining_budget", "estimated_cost_usd",
})
_BOUNDED_VALUES = {
    "outcome": frozenset({"success", "failure", "rejected", "disabled", "allowed", "unavailable"}),
    "status_class": frozenset({"2xx", "4xx", "5xx"}),
    "attempt_role": frozenset({"initial", "retry", "fallback"}),
    "complexity_level": frozenset({"LOW", "MEDIUM", "HIGH"}),
    "error_domain": frozenset({"authentication", "rate_limit", "validation", "budget", "routing", "provider", "internal"}),
    "route": frozenset({"chat", "/api/v1/chat", "health", "/health"}),
}


def metric_definition(name: str) -> MetricDefinition:
    validate_metric_name(name)
    try:
        return METRIC_CATALOG[name]
    except KeyError as exc:
        raise ValueError(f"unknown metric: {name}") from exc


def validate_labels(name: str, labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    definition = metric_definition(name)
    normalized = dict(labels or {})
    if any(not isinstance(key, str) or not _IDENTIFIER.fullmatch(key) for key in normalized):
        raise ValueError("metric label names must be bounded safe identifiers")
    if any(key in _FORBIDDEN_LABELS for key in normalized):
        raise ValueError("forbidden metric label")
    if not set(normalized).issubset(definition.allowed_labels):
        raise ValueError(f"labels are not allowed for {name}")
    for key, value in normalized.items():
        if not isinstance(value, str) or len(value) > _LABEL_MAX_LENGTH:
            raise ValueError("metric label values must be bounded safe identifiers")
        allowed = definition.label_values.get(key) or _BOUNDED_VALUES.get(key)
        if allowed is not None:
            if value not in allowed:
                raise ValueError(f"invalid value for metric label: {key}")
            continue
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("metric label values must be bounded safe identifiers")
    return tuple(sorted(normalized.items()))
