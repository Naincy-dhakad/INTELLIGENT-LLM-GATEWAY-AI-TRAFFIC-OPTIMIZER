from dataclasses import FrozenInstanceError

import pytest

from gateway.application.metrics import MetricsSnapshot
from gateway.application.metrics_exporter import MetricsExporter, TextMetricsExporter
from gateway.application.observability import InMemoryMetrics


def test_exporter_contract_and_empty_snapshot():
    exporter = TextMetricsExporter()
    assert callable(exporter.export)
    assert exporter.export(MetricsSnapshot()) == ""


def test_counter_rendering_is_deterministic_and_sorted():
    metrics = InMemoryMetrics()
    metrics.increment("gateway_requests_total", {"route": "health", "outcome": "success"}, 2)
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"}, 3)
    output = TextMetricsExporter().export(metrics.snapshot())
    assert output == (
        "# HELP gateway_requests_total Total gateway requests.\n"
        "# TYPE gateway_requests_total counter\n"
        'gateway_requests_total{outcome="success",route="chat"} 3\n'
        'gateway_requests_total{outcome="success",route="health"} 2\n'
    )


def test_histogram_rendering_contains_buckets_sum_and_count():
    metrics = InMemoryMetrics()
    metrics.observe("gateway_request_duration_seconds", 0.01, {"route": "chat"})
    metrics.observe("gateway_request_duration_seconds", 0.2, {"route": "chat"})
    output = TextMetricsExporter().export(metrics.snapshot())
    assert "# TYPE gateway_request_duration_seconds histogram" in output
    assert 'gateway_request_duration_seconds_bucket{route="chat",le="0.005"} 0' in output
    assert 'gateway_request_duration_seconds_bucket{route="chat",le="0.25"} 2' in output
    assert 'gateway_request_duration_seconds_bucket{route="chat",le="+Inf"} 2' in output
    assert 'gateway_request_duration_seconds_sum{route="chat"} 0.21' in output
    assert 'gateway_request_duration_seconds_count{route="chat"} 2' in output


def test_labels_are_escaped_and_snapshot_is_not_mutated():
    metrics = InMemoryMetrics()
    metrics.increment("gateway_provider_attempts_total", {"provider_id": "safe/provider", "model_id": "model-v1", "attempt_role": "initial", "outcome": "success"})
    snapshot = metrics.snapshot()
    before = snapshot
    output = TextMetricsExporter().export(snapshot)
    assert 'provider_id="safe/provider"' in output
    assert snapshot == before
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        snapshot.counters = ()


def test_sensitive_labels_cannot_reach_exporter():
    metrics = InMemoryMetrics()
    for labels in ({"request_id": "req_1"}, {"api_key": "secret"}, {"prompt": "secret prompt"}, {"estimated_cost_usd": "1"}):
        with pytest.raises(ValueError):
            metrics.increment("gateway_requests_total", labels)
    assert "secret" not in TextMetricsExporter().export(metrics.snapshot())
