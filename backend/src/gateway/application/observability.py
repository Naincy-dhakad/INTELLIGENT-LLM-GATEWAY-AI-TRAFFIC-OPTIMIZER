"""Application-level observability ports and dependency-free implementations."""

from collections import Counter
from dataclasses import dataclass, field
from math import isfinite
from threading import Lock
from typing import Mapping, Protocol

from gateway.application.metrics import (
    CounterSnapshot,
    HistogramSnapshot,
    MetricsSnapshot,
    metric_definition,
    validate_labels,
)
from gateway.application.observability_events import EventType, ObservabilityEvent


class EventSink(Protocol):
    def emit(self, event: ObservabilityEvent) -> None:
        ...


class MetricsSink(Protocol):
    def increment(self, metric: str, labels: Mapping[str, str] | None = None, value: int = 1) -> None:
        ...

    def observe(self, metric: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        ...


class ObservabilityPort(EventSink, MetricsSink, Protocol):
    """Port used by application orchestration without a telemetry dependency."""
    ...


class NoopObservability:
    def emit(self, event: ObservabilityEvent) -> None:
        _ = event

    def increment(self, metric: str, labels: Mapping[str, str] | None = None, value: int = 1) -> None:
        _ = metric, labels, value

    def observe(self, metric: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        _ = metric, value, labels


def _metric_labels(attributes: Mapping[str, object]) -> dict[str, str]:
    labels: dict[str, str] = {}
    route = attributes.get("route")
    if route in {"/api/v1/chat", "/health", "chat", "health"}:
        labels["route"] = str(route)
    status_code = attributes.get("status_code")
    if isinstance(status_code, int):
        labels["status_class"] = f"{status_code // 100}xx"
    for key in ("outcome", "category", "complexity_level", "policy_version", "objective", "error_code", "error_category", "attempt_role", "provider_id", "model_id", "from_provider_id", "to_provider_id", "attempt_number", "error_domain"):
        value = attributes.get(key)
        if value is not None:
            labels[key] = str(value)
    return labels


def record_event_metrics(metrics: MetricsSink, event_type: EventType, attributes: Mapping[str, object]) -> None:
    """Translate an already-normalized event into bounded metrics."""
    labels = _metric_labels(attributes)
    try:
        increment = metrics.increment
        observe = metrics.observe
        if event_type is EventType.REQUEST_COMPLETED:
            request_labels = {key: value for key, value in labels.items() if key in {"route", "outcome", "status_class"}}
            increment("gateway_requests_total", request_labels)
            latency = attributes.get("latency_ms")
            if isinstance(latency, (int, float)):
                observe("gateway_request_duration_seconds", latency / 1000, request_labels)
            if attributes.get("error_code") is not None:
                increment("gateway_errors_total", {key: value for key, value in labels.items() if key in {"error_code", "status_class"}})
        elif event_type is EventType.AUTHENTICATION_RESULT:
            increment("gateway_authentication_results_total", labels)
        elif event_type is EventType.RATE_LIMIT_RESULT:
            increment("gateway_rate_limit_results_total", labels)
        elif event_type is EventType.CLASSIFICATION_COMPLETED:
            increment("gateway_classification_total", labels)
        elif event_type is EventType.BUDGET_DECISION:
            increment("gateway_budget_decisions_total", labels)
        elif event_type is EventType.ROUTING_DECISION:
            increment("gateway_routing_decisions_total", {key: value for key, value in labels.items() if key in {"outcome", "objective", "policy_version", "provider_id", "model_id"}})
        elif event_type is EventType.PROVIDER_ATTEMPT:
            provider_labels = {key: value for key, value in labels.items() if key in {"provider_id", "model_id", "attempt_role", "outcome"}}
            increment("gateway_provider_attempts_total", provider_labels)
            latency = attributes.get("latency_ms")
            if isinstance(latency, (int, float)):
                observe("gateway_provider_attempt_duration_seconds", latency / 1000, provider_labels)
        elif event_type is EventType.RETRY_SCHEDULED:
            increment("gateway_retries_scheduled_total", {key: value for key, value in labels.items() if key in {"provider_id", "model_id", "error_category", "attempt_number"}})
        elif event_type is EventType.FALLBACK_SELECTED:
            increment("gateway_fallbacks_selected_total", {key: value for key, value in labels.items() if key in {"from_provider_id", "to_provider_id", "attempt_number"}})
        elif event_type is EventType.REQUEST_VALIDATION_RESULT:
            increment("gateway_validation_results_total", labels)
    except Exception:
        # Metrics are strictly best effort and cannot affect business behavior.
        pass


class MetricsObservability:
    """Delegating port that records metrics for every normalized event."""

    def __init__(self, delegate: ObservabilityPort) -> None:
        self._delegate = delegate

    def emit(self, event: ObservabilityEvent) -> None:
        try:
            self._delegate.emit(event)
        finally:
            record_event_metrics(self._delegate, event.event_type, event.attributes)

    def increment(self, metric: str, labels: Mapping[str, str] | None = None, value: int = 1) -> None:
        self._delegate.increment(metric, labels, value)

    def observe(self, metric: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        self._delegate.observe(metric, value, labels)


_MAX_DIAGNOSTIC_SAMPLES = 256


@dataclass
class _HistogramAccumulator:
    """Bounded aggregate state for one metric and validated label set."""

    bucket_counts: list[int]
    count: int = 0
    total: float = 0.0
    diagnostic_samples: list[float] = field(default_factory=list)

    def observe(self, value: float, buckets: tuple[float, ...]) -> None:
        for index, bucket in enumerate(buckets):
            if value <= bucket:
                self.bucket_counts[index] += 1
        self.count += 1
        self.total += value
        if len(self.diagnostic_samples) < _MAX_DIAGNOSTIC_SAMPLES:
            self.diagnostic_samples.append(value)


class InMemoryMetrics:
    """Thread-safe bounded in-memory metrics implementation."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], _HistogramAccumulator] = {}
        self._lock = Lock()

    @staticmethod
    def _labels(metric: str, labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
        return validate_labels(metric, labels)

    def increment(self, metric: str, labels: Mapping[str, str] | None = None, value: int = 1) -> None:
        definition = metric_definition(metric)
        if definition.metric_type.value != "counter":
            raise ValueError("metric is not a counter")
        if not isinstance(value, int) or value < 0:
            raise ValueError("metric increment must be named and non-negative")
        key = (metric, self._labels(metric, labels))
        with self._lock:
            self._counts[key] += value

    def observe(self, metric: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        definition = metric_definition(metric)
        if definition.metric_type.value != "histogram":
            raise ValueError("metric is not a histogram")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value) or value < 0:
            raise ValueError("histogram values must be finite and non-negative")
        normalized_labels = self._labels(metric, labels)
        with self._lock:
            key = (metric, normalized_labels)
            accumulator = self._histograms.setdefault(
                key,
                _HistogramAccumulator(bucket_counts=[0] * len(definition.buckets)),
            )
            accumulator.observe(float(value), definition.buckets)

    def snapshot(self) -> MetricsSnapshot:
        """Return a copied, deterministic view without exposing mutable state."""
        with self._lock:
            counts = tuple(self._counts.items())
            histograms = tuple(
                (
                    name,
                    labels,
                    tuple(accumulator.bucket_counts),
                    accumulator.count,
                    accumulator.total,
                    tuple(accumulator.diagnostic_samples),
                )
                for (name, labels), accumulator in self._histograms.items()
            )

        counter_snapshots = tuple(
            CounterSnapshot(metric_definition(name), labels, value)
            for (name, labels), value in sorted(counts, key=lambda item: item[0])
        )
        histogram_snapshots: list[HistogramSnapshot] = []
        for name, labels, bucket_counts, count, total, diagnostic_samples in sorted(histograms):
            definition = metric_definition(name)
            histogram_snapshots.append(
                HistogramSnapshot(
                    definition=definition,
                    labels=labels,
                    observations=diagnostic_samples,
                    bucket_counts=tuple(zip(definition.buckets, bucket_counts)),
                    count=count,
                    sum=total,
                )
            )
        return MetricsSnapshot(tuple(counter_snapshots), tuple(histogram_snapshots))

    def counts(self) -> dict[tuple[str, tuple[tuple[str, str], ...]], int]:
        with self._lock:
            return dict(self._counts)

    def observations(self) -> tuple[tuple[str, float, tuple[tuple[str, str], ...]], ...]:
        """Return bounded diagnostic samples, not the complete observation history."""
        with self._lock:
            return tuple(
                (name, value, labels)
                for (name, labels), accumulator in sorted(self._histograms.items())
                for value in accumulator.diagnostic_samples
            )
