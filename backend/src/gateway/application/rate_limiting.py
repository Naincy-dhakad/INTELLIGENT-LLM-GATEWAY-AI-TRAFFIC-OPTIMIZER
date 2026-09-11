"""Application boundary for gateway rate limiting."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    count: int
    remaining: int
    retry_after_seconds: int | None = None


class RateLimitUnavailable(Exception):
    """The configured rate-limit coordination store cannot be used."""


class RateLimiter(Protocol):
    def check_and_consume(
        self, principal_id: str, limit: int, window_seconds: int
    ) -> RateLimitResult:
        """Atomically consume one request in the principal's current window."""
        ...


class DisabledRateLimiter:
    """No-op implementation used when rate limiting is explicitly disabled."""

    def check_and_consume(
        self, principal_id: str, limit: int, window_seconds: int
    ) -> RateLimitResult:
        _ = principal_id, limit, window_seconds
        return RateLimitResult(allowed=True, count=0, remaining=limit)
