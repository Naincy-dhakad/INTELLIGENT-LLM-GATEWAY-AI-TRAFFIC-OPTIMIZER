from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    """Request-scoped values shared with future routing/provider work."""

    request_id: str
    timeout_ms: int
    deadline_monotonic: float
