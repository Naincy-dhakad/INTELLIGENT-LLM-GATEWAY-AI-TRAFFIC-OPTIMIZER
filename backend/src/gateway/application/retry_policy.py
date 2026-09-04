"""Small deterministic retry policy for normalized provider failures."""

from gateway.domain.provider import ProviderErrorCategory


class RetryPolicy:
    MAX_TOTAL_ATTEMPTS = 3
    RETRYABLE_CATEGORIES = frozenset(
        {
            ProviderErrorCategory.TIMEOUT,
            ProviderErrorCategory.RATE_LIMIT,
            ProviderErrorCategory.UNAVAILABLE,
        }
    )
    DELAYS_SECONDS = (0.05, 0.1)

    @classmethod
    def is_retryable(cls, category: ProviderErrorCategory) -> bool:
        return category in cls.RETRYABLE_CATEGORIES

    @classmethod
    def delay_seconds(cls, retry_number: int) -> float:
        return cls.DELAYS_SECONDS[min(retry_number - 1, len(cls.DELAYS_SECONDS) - 1)]
