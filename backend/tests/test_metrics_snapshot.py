from concurrent.futures import ThreadPoolExecutor

import pytest

from gateway.application.observability import InMemoryMetrics


def test_empty_snapshot_is_immutable_and_empty():
    snapshot = InMemoryMetrics().snapshot()
    assert snapshot.counters == ()
    assert snapshot.histograms == ()
    with pytest.raises(AttributeError):
        snapshot.counters = ()


def test_counter_snapshot_copies_definition_labels_and_value():
    metrics = InMemoryMetrics()
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"}, 3)
    snapshot = metrics.snapshot()
    assert len(snapshot.counters) == 1
    item = snapshot.counters[0]
    assert item.definition.name == "gateway_requests_total"
    assert item.labels == (("outcome", "success"), ("route", "chat"))
    assert item.value == 3


def test_histogram_snapshot_has_coherent_buckets_count_and_sum():
    metrics = InMemoryMetrics()
    for value in (0.01, 0.2, 3.0):
        metrics.observe("gateway_request_duration_seconds", value, {"route": "chat"})
    histogram = metrics.snapshot().histograms[0]
    assert histogram.observations == (0.01, 0.2, 3.0)
    assert histogram.count == 3
    assert histogram.sum == pytest.approx(3.21)
    assert histogram.bucket_counts[0] == (0.005, 0)
    assert dict(histogram.bucket_counts)[0.25] == 2
    assert dict(histogram.bucket_counts)[5] == 3


def test_snapshot_is_detached_from_future_source_mutation():
    metrics = InMemoryMetrics()
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"})
    metrics.observe("gateway_request_duration_seconds", 0.1, {"route": "chat"})
    snapshot = metrics.snapshot()
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"})
    metrics.observe("gateway_request_duration_seconds", 0.2, {"route": "chat"})
    assert snapshot.counters[0].value == 1
    assert snapshot.histograms[0].count == 1


def test_snapshot_metric_and_label_order_is_deterministic():
    metrics = InMemoryMetrics()
    metrics.increment("gateway_errors_total", {"error_code": "invalid_request", "status_class": "4xx"})
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"})
    metrics.increment("gateway_requests_total", {"route": "health", "outcome": "success"})
    snapshot = metrics.snapshot()
    assert [item.definition.name for item in snapshot.counters] == [
        "gateway_errors_total", "gateway_requests_total", "gateway_requests_total"
    ]
    assert snapshot.counters[1].labels == (("outcome", "success"), ("route", "chat"))
    assert snapshot.counters[2].labels == (("outcome", "success"), ("route", "health"))


def test_concurrent_updates_and_snapshot_are_safe():
    metrics = InMemoryMetrics()

    def update(_):
        metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"})
        metrics.observe("gateway_request_duration_seconds", 0.1, {"route": "chat"})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(update, range(100)))
    snapshot = metrics.snapshot()
    assert snapshot.counters[0].value == 100
    assert snapshot.histograms[0].count == 100


def test_snapshot_rejects_no_unknown_state_by_construction():
    metrics = InMemoryMetrics()
    with pytest.raises(ValueError):
        metrics.increment("unknown_metric", {})
    assert metrics.snapshot().counters == ()
    assert metrics.snapshot().histograms == ()


def test_histogram_aggregation_remains_bounded_after_many_observations():
    metrics = InMemoryMetrics()
    for _ in range(10_000):
        metrics.observe("gateway_request_duration_seconds", 0.1, {"route": "chat"})

    histogram = metrics.snapshot().histograms[0]
    assert histogram.count == 10_000
    assert histogram.sum == pytest.approx(1_000)
    assert len(histogram.observations) <= 256
    assert len(metrics.observations()) <= 256


def test_invalid_histogram_observation_does_not_mutate_aggregate_state():
    metrics = InMemoryMetrics()
    with pytest.raises(ValueError):
        metrics.observe("gateway_request_duration_seconds", -1, {"route": "chat"})
    assert metrics.snapshot().histograms == ()


def test_repeated_updates_use_one_bounded_accumulator_per_label_set():
    metrics = InMemoryMetrics()
    for _ in range(2_000):
        metrics.observe("gateway_request_duration_seconds", 0.1, {"route": "chat"})

    assert len(metrics._histograms) == 1
    assert len(metrics._histograms[("gateway_request_duration_seconds", (("route", "chat"),))].diagnostic_samples) == 256
    assert metrics.snapshot().histograms[0].count == 2_000
