"""Application-level observability ports and dependency-free implementations."""

from collections import Counter
from threading import Lock
from typing import Mapping, Protocol

from gateway.application.metrics import metric_definition, validate_labels
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


class InMemoryMetrics:
    """Bounded-label test/local implementation; not a production metrics store."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._observations: list[tuple[str, float, tuple[tuple[str, str], ...]]] = []
        self._lock = Lock()

    @staticmethod
    def _labels(metric: str, labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
        return validate_labels(metric, labels)

    def increment(self, metric: str, labels: Mapping[str, str] | None = None, value: int = 1) -> None:
        if not metric or value < 0:
            raise ValueError("metric increment must be named and non-negative")
        definition = metric_definition(metric)
        if definition.metric_type.value != "counter":
            raise ValueError("metric is not a counter")
        key = (metric, self._labels(metric, labels))
        with self._lock:
            self._counts[key] += value

    def observe(self, metric: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        definition = metric_definition(metric)
        if definition.metric_type.value != "histogram":
            raise ValueError("metric is not a histogram")
        if value < 0:
            raise ValueError("histogram values must be non-negative")
        normalized_labels = self._labels(metric, labels)
        with self._lock:
            self._observations.append((metric, float(value), normalized_labels))

    def counts(self) -> dict[tuple[str, tuple[tuple[str, str], ...]], int]:
        with self._lock:
            return dict(self._counts)

    def observations(self) -> tuple[tuple[str, float, tuple[tuple[str, str], ...]], ...]:
        with self._lock:
            return tuple(self._observations)
