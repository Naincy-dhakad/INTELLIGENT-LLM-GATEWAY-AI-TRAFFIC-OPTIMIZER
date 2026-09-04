"""Pure helpers for configured, estimated model latency."""

from gateway.domain.provider import ModelLatency

MAX_CONFIGURED_LATENCY_MS = 120_000


def configured_latency_ms(
    latency_metadata: tuple[ModelLatency, ...], model_id: str
) -> int | None:
    """Return configured latency for a model; missing metadata remains unknown."""

    return next(
        (item.estimated_latency_ms for item in latency_metadata if item.model_id == model_id),
        None,
    )
