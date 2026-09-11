"""Minimal structured logging sink for already-validated safe events."""

import json
import logging

from gateway.application.observability_events import ObservabilityEvent


class StructuredEventLogger:
    """Serializes only normalized event fields through stdlib logging."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("gateway.observability")

    def emit(self, event: ObservabilityEvent) -> None:
        self._logger.info(json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":")))
