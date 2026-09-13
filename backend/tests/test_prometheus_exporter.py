import pytest

from gateway.application.metrics import CounterSnapshot, MetricsSnapshot, metric_definition
from gateway.application.metrics_exporter import TextMetricsExporter
from gateway.application.observability import InMemoryMetrics
from gateway.infrastructure.observability.prometheus import PrometheusMetricsExporter


def test_prometheus_adapter_consumes_snapshot_and_renders_text():
    metrics = InMemoryMetrics()
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"}, 2)
    exporter = PrometheusMetricsExporter()
    output = exporter.export(metrics.snapshot())
    assert isinstance(output, str)
    assert "# TYPE gateway_requests_total counter" in output


def test_prometheus_output_is_deterministic_and_empty_snapshot_is_empty():
    exporter = PrometheusMetricsExporter()
    assert exporter.export(MetricsSnapshot()) == ""
    metrics = InMemoryMetrics()
    metrics.increment("gateway_requests_total", {"route": "health", "outcome": "success"})
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"})
    snapshot = metrics.snapshot()
    assert exporter.export(snapshot) == exporter.export(snapshot)


def test_prometheus_histogram_has_inf_sum_count_and_help_type():
    metrics = InMemoryMetrics()
    metrics.observe("gateway_request_duration_seconds", 0.1, {"route": "chat"})
    output = PrometheusMetricsExporter().export(metrics.snapshot())
    assert "# HELP gateway_request_duration_seconds" in output
    assert "# TYPE gateway_request_duration_seconds histogram" in output
    assert 'le="+Inf"' in output
    assert "gateway_request_duration_seconds_sum" in output
    assert "gateway_request_duration_seconds_count" in output
    assert output.endswith("\n")


def test_text_renderer_escapes_label_values_without_mutating_snapshot():
    definition = metric_definition("gateway_requests_total")
    snapshot = MetricsSnapshot((CounterSnapshot(definition, (("route", 'safe\\\"value'),), 1),), ())
    before = snapshot
    output = TextMetricsExporter().export(snapshot)
    assert 'route="safe\\\\\\\"value"' in output
    assert snapshot == before


def test_malformed_snapshot_fails_with_safe_error():
    definition = metric_definition("gateway_requests_total")
    malformed = MetricsSnapshot((CounterSnapshot(definition, (("request_id", "secret"),), 1),), ())
    with pytest.raises(ValueError, match="invalid metrics snapshot") as raised:
        PrometheusMetricsExporter().export(malformed)
    assert "request_id" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_snapshot_immutability_is_preserved():
    metrics = InMemoryMetrics()
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"})
    snapshot = metrics.snapshot()
    output = PrometheusMetricsExporter().export(snapshot)
    assert output
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"})
    assert " 1\n" in PrometheusMetricsExporter().export(snapshot)


def test_prometheus_histogram_uses_aggregate_count_sum_and_buckets():
    metrics = InMemoryMetrics()
    for value in (0.01, 0.25, 1.0):
        metrics.observe("gateway_request_duration_seconds", value, {"route": "chat"})

    output = PrometheusMetricsExporter().export(metrics.snapshot())
    assert 'gateway_request_duration_seconds_bucket{route="chat",le="0.25"} 2' in output
    assert 'gateway_request_duration_seconds_sum{route="chat"} 1.26' in output
    assert 'gateway_request_duration_seconds_count{route="chat"} 3' in output
