"""Prometheus text exposition adapter for immutable application snapshots."""

from math import isfinite

from gateway.application.metrics import metric_definition, validate_labels
from gateway.application.metrics_exporter import MetricsExporter, TextMetricsExporter
from gateway.application.metrics import MetricsSnapshot


class PrometheusMetricsExporter:
    """Infrastructure adapter using the dependency-free deterministic renderer."""

    def __init__(self) -> None:
        self._renderer: MetricsExporter = TextMetricsExporter()

    def export(self, snapshot: MetricsSnapshot) -> str:
        try:
            self._validate_snapshot(snapshot)
            return self._renderer.export(snapshot)
        except Exception as exc:
            raise ValueError("invalid metrics snapshot") from exc

    @staticmethod
    def _validate_snapshot(snapshot: MetricsSnapshot) -> None:
        for item in snapshot.counters:
            definition = metric_definition(item.definition.name)
            if definition.metric_type is not item.definition.metric_type:
                raise ValueError("metric type mismatch")
            if validate_labels(definition.name, dict(item.labels)) != item.labels:
                raise ValueError("non-canonical metric labels")
            if not isinstance(item.value, int) or item.value < 0:
                raise ValueError("invalid counter value")

        for item in snapshot.histograms:
            definition = metric_definition(item.definition.name)
            if definition.metric_type is not item.definition.metric_type:
                raise ValueError("metric type mismatch")
            if validate_labels(definition.name, dict(item.labels)) != item.labels:
                raise ValueError("non-canonical metric labels")
            if item.count != len(item.observations) or item.count < 0:
                raise ValueError("invalid histogram count")
            if not isfinite(item.sum) or any(not isfinite(value) or value < 0 for value in item.observations):
                raise ValueError("invalid histogram value")
            if tuple(bucket for bucket, _ in item.bucket_counts) != definition.buckets:
                raise ValueError("invalid histogram buckets")
            if any(count < 0 for _, count in item.bucket_counts):
                raise ValueError("invalid histogram bucket count")
            expected_counts = tuple(
                sum(value <= bucket for value in item.observations)
                for bucket in definition.buckets
            )
            if tuple(count for _, count in item.bucket_counts) != expected_counts:
                raise ValueError("incoherent histogram buckets")
