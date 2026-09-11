"""Application-level observability ports and dependency-free implementations."""

from collections import Counter
from threading import Lock
from typing import Mapping, Protocol

from gateway.application.observability_events import ObservabilityEvent


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


_FORBIDDEN_LABELS = frozenset({
    "request_id", "api_key", "x_api_key", "authorization", "principal_id",
    "prompt", "completion", "token", "raw_error",
})
_ALLOWED_LABELS = frozenset({
    "route", "outcome", "status_class", "provider_id", "model_id", "attempt_role",
    "error_category", "objective", "policy_version", "reason", "environment",
})


class InMemoryMetrics:
    """Bounded-label test/local implementation; not a production metrics store."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._observations: list[tuple[str, float, tuple[tuple[str, str], ...]]] = []
        self._lock = Lock()

    @staticmethod
    def _labels(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
        normalized = dict(labels or {})
        if any(key in _FORBIDDEN_LABELS or key not in _ALLOWED_LABELS for key in normalized):
            raise ValueError("metric labels must use the bounded safe label vocabulary")
        if any(len(value) > 128 for value in normalized.values()):
            raise ValueError("metric label values must be bounded")
        return tuple(sorted(normalized.items()))

    def increment(self, metric: str, labels: Mapping[str, str] | None = None, value: int = 1) -> None:
        if not metric or value < 0:
            raise ValueError("metric increment must be named and non-negative")
        key = (metric, self._labels(labels))
        with self._lock:
            self._counts[key] += value

    def observe(self, metric: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        if not metric:
            raise ValueError("metric must be named")
        with self._lock:
            self._observations.append((metric, float(value), self._labels(labels)))

    def counts(self) -> dict[tuple[str, tuple[tuple[str, str], ...]], int]:
        with self._lock:
            return dict(self._counts)

    def observations(self) -> tuple[tuple[str, float, tuple[tuple[str, str], ...]], ...]:
        with self._lock:
            return tuple(self._observations)
