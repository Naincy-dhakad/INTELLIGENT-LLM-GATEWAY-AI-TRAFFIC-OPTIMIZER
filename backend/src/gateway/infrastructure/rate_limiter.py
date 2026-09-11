"""Redis implementation of the gateway fixed-window rate limiter."""

from gateway.application.rate_limiting import RateLimitResult, RateLimitUnavailable


class RedisRateLimiter:
    """Uses one atomic Redis Lua operation per authenticated request."""

    _INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""

    def __init__(self, redis_url: str) -> None:
        self._script = None
        try:
            from redis import Redis

            client = Redis.from_url(redis_url, decode_responses=False)
            self._script = client.register_script(self._INCREMENT_SCRIPT)
        except Exception as exc:
            self._initialization_error = exc

    @staticmethod
    def _redis_key(principal_id: str) -> str:
        return f"gateway:rate_limit:{principal_id}"

    def check_and_consume(
        self, principal_id: str, limit: int, window_seconds: int
    ) -> RateLimitResult:
        if self._script is None:
            raise RateLimitUnavailable from self._initialization_error
        try:
            count, ttl = self._script(
                keys=[self._redis_key(principal_id)], args=[window_seconds]
            )
            count = int(count)
            ttl = int(ttl)
        except Exception as exc:
            raise RateLimitUnavailable from exc

        if count > limit:
            return RateLimitResult(
                allowed=False,
                count=count,
                remaining=0,
                retry_after_seconds=max(0, ttl) if ttl >= 0 else None,
            )
        return RateLimitResult(
            allowed=True,
            count=count,
            remaining=max(0, limit - count),
        )
