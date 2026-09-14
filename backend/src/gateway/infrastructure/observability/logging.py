"""Minimal structured logging sink for already-validated safe events."""

import json
import logging

from gateway.application.observability_events import ObservabilityEvent


class StructuredEventLogger:
    """Serializes only normalized event fields through stdlib logging."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("gateway.observability")

    def emit(self, event: ObservabilityEvent) -> None:
        payload = {
            key: value
            for key, value in event.as_dict().items()
            if key not in {"request_id", "principal_id"}
        }
        self._logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
