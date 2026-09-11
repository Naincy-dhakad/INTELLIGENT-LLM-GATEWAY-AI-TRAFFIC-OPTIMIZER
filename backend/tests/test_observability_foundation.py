import json
import logging

import pytest

from gateway.application.observability import InMemoryMetrics, NoopObservability
from gateway.application.observability_events import EventType, ObservabilityEvent, make_event
from gateway.infrastructure.observability.logging import StructuredEventLogger


REQUEST_ID = "req_observability_123"


def test_event_requires_bounded_request_id_and_preserves_correlation():
    event = make_event(EventType.REQUEST_STARTED, REQUEST_ID, route="POST /api/v1/chat", method="POST")
    assert event.as_dict()["request_id"] == REQUEST_ID
    assert event.as_dict()["event"] == "gateway_request_started"

    with pytest.raises(ValueError):
        make_event(EventType.REQUEST_STARTED, "not safe / id", route="chat")


def test_event_vocabulary_and_fields_are_normalized():
    event = make_event(
        EventType.ROUTING_DECISION,
        REQUEST_ID,
        objective="cost",
        policy_version="classification-cost-v1",
        provider_id="phase3-mock",
        model_id="phase3-mock-model",
        reason_code="lowest_estimated_cost",
    )
    assert event.event_type is EventType.ROUTING_DECISION
    assert event.attributes["provider_id"] == "phase3-mock"

    with pytest.raises(ValueError):
        make_event(EventType.ROUTING_DECISION, REQUEST_ID, arbitrary_internal_object="x")


def test_sensitive_fields_are_rejected_not_redacted():
    for field in ("api_key", "x_api_key", "authorization", "prompt", "completion", "redis_url", "credentials"):
        with pytest.raises(ValueError):
            make_event(EventType.REQUEST_STARTED, REQUEST_ID, **{field: "secret"})


def test_event_is_immutable_and_does_not_accept_raw_objects():
    event = make_event(EventType.REQUEST_COMPLETED, REQUEST_ID, outcome="success", status_code=200)
    with pytest.raises(TypeError):
        event.attributes["outcome"] = "failure"
    with pytest.raises(TypeError):
        make_event(EventType.REQUEST_COMPLETED, REQUEST_ID, outcome={"raw": "object"})


def test_noop_observability_has_no_side_effects():
    sink = NoopObservability()
    event = make_event(EventType.REQUEST_COMPLETED, REQUEST_ID, outcome="success")
    sink.emit(event)
    sink.increment("gateway_requests_total", {"outcome": "success"})
    sink.observe("gateway_latency_seconds", 0.01, {"outcome": "success"})


def test_metrics_support_bounded_labels_and_reject_sensitive_labels():
    metrics = InMemoryMetrics()
    metrics.increment("gateway_requests_total", {"route": "chat", "outcome": "success"})
    metrics.observe("gateway_latency_seconds", 0.25, {"provider_id": "phase3-mock"})
    assert len(metrics.counts()) == 1
    assert len(metrics.observations()) == 1

    for labels in ({"request_id": REQUEST_ID}, {"api_key": "secret"}, {"unbounded": "x"}):
        with pytest.raises(ValueError):
            metrics.increment("gateway_requests_total", labels)


def test_structured_logger_serializes_only_event_fields(caplog):
    logger = logging.getLogger("test.gateway.observability")
    sink = StructuredEventLogger(logger)
    event = make_event(EventType.REQUEST_COMPLETED, REQUEST_ID, outcome="success", status_code=200)
    with caplog.at_level(logging.INFO, logger=logger.name):
        sink.emit(event)
    payload = json.loads(caplog.records[0].message)
    assert payload["event"] == "gateway_request_completed"
    assert payload["request_id"] == REQUEST_ID
    assert "prompt" not in payload
    assert "api_key" not in payload
