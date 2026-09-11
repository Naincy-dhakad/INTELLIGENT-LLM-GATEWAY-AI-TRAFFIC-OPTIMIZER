from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RequestContext:
    """Request-scoped values shared with future routing/provider work."""

    request_id: str
    timeout_ms: int
    deadline_monotonic: float
    execution_metadata: dict[str, object] | None = None
    principal_id: str | None = None
    budget_usd: Decimal | None = None
    historical_spend_usd: Decimal | None = None
    execution_metadata: dict[str, object] | None = None
