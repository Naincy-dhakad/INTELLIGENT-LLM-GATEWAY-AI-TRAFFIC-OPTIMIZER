from concurrent.futures import ThreadPoolExecutor

import pytest

from gateway.application.metrics import (
    GATEWAY_REQUEST_BUCKETS,
    METRIC_CATALOG,
    PROVIDER_ATTEMPT_BUCKETS,
    metric_definition,
    validate_labels,
    validate_metric_name,
)
from gateway.application.observability import InMemoryMetrics, NoopObservability


FIRST_PHASE_METRICS = {
    "gateway_requests_total", "gateway_request_duration_seconds",
    "gateway_authentication_results_total", "gateway_rate_limit_results_total",
    "gateway_classification_total", "gateway_budget_decisions_total",
    "gateway_routing_decisions_total", "gateway_provider_attempts_total",
    "gateway_provider_attempt_duration_seconds", "gateway_retries_scheduled_total",
    "gateway_fallbacks_selected_total", "gateway_validation_results_total",
    "gateway_errors_total",
}


def test_metric_catalog_constructs_with_name_validation_available():
    assert metric_definition("gateway_requests_total").name == "gateway_requests_total"


def test_catalog_defines_only_first_phase_metrics_plus_legacy_compatibility():
    assert FIRST_PHASE_METRICS.issubset(METRIC_CATALOG)
    assert "gateway_request_success_total" not in METRIC_CATALOG


def test_metric_names_are_strictly_validated():
    validate_metric_name("gateway_requests_total")
    for name in ("Requests_Total", "gateway requests", "gateway_" + "x" * 130, "custom_metric"):
        with pytest.raises(ValueError):
            validate_metric_name(name)
    with pytest.raises(ValueError):
        metric_definition("unknown_metric")


def test_counter_and_histogram_are_deterministically_retrievable():
    metrics = InMemoryMetrics()
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"}, 2)
    metrics.observe("gateway_request_duration_seconds", 0.25, {"route": "chat", "status_class": "2xx"})
    assert metrics.counts() == {("gateway_requests_total", (("outcome", "success"), ("route", "chat"))): 2}
    assert metrics.observations() == (("gateway_request_duration_seconds", 0.25, (("route", "chat"), ("status_class", "2xx"))),)


def test_unknown_forbidden_and_sensitive_labels_are_rejected():
    for labels in (
        {"unknown": "value"}, {"request_id": "req_1"}, {"api_key": "secret"},
        {"prompt": "text"}, {"completion": "text"}, {"exact_budget": "10"},
        {"accumulated_spend": "10"}, {"remaining_budget": "10"},
        {"estimated_cost_usd": "1"}, {"database_url": "db"}, {"redis_url": "redis"},
    ):
        with pytest.raises(ValueError):
            validate_labels("gateway_requests_total", labels)


def test_bounded_values_and_routes_are_enforced():
    with pytest.raises(ValueError):
        validate_labels("gateway_requests_total", {"status_class": "200"})
    with pytest.raises(ValueError):
        validate_labels("gateway_provider_attempts_total", {"attempt_role": "other"})
    with pytest.raises(ValueError):
        validate_labels("gateway_requests_total", {"route": "unbounded-user-route"})
    validate_labels("gateway_requests_total", {"route": "chat", "status_class": "2xx"})


def test_provider_and_model_identifiers_are_bounded_safe_values():
    validate_labels("gateway_provider_attempts_total", {"provider_id": "phase3-mock", "model_id": "phase3-mock-model"})
    with pytest.raises(ValueError):
        validate_labels("gateway_provider_attempts_total", {"provider_id": "provider with spaces"})
    with pytest.raises(ValueError):
        validate_labels("gateway_provider_attempts_total", {"model_id": "x" * 129})


def test_histogram_bucket_configuration_is_fixed():
    assert metric_definition("gateway_request_duration_seconds").buckets == GATEWAY_REQUEST_BUCKETS
    assert metric_definition("gateway_provider_attempt_duration_seconds").buckets == PROVIDER_ATTEMPT_BUCKETS


def test_metric_type_mismatch_and_invalid_values_are_rejected():
    metrics = InMemoryMetrics()
    with pytest.raises(ValueError):
        metrics.observe("gateway_requests_total", 1)
    with pytest.raises(ValueError):
        metrics.increment("gateway_request_duration_seconds")
    with pytest.raises(ValueError):
        metrics.increment("gateway_requests_total", value=-1)
    with pytest.raises(ValueError):
        metrics.observe("gateway_request_duration_seconds", -1)


def test_noop_metrics_remains_optional():
    sink = NoopObservability()
    sink.increment("not-a-catalog-metric", {"anything": "ignored"})
    sink.observe("not-a-catalog-metric", 1, {"anything": "ignored"})


def test_in_memory_metrics_is_thread_safe():
    metrics = InMemoryMetrics()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: metrics.increment("gateway_requests_total", {"outcome": "success"}), range(100)))
    assert sum(metrics.counts().values()) == 100
