import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from league_api.riot.errors import RiotConfigurationError
from league_api.riot.rate_limiter import RiotRateLimit, RiotRateLimitAudience

RateLimitWaitCallback = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RedisRiotRateLimitManager:
    redis_client: Any
    limits: Sequence[RiotRateLimit]
    max_retries: int
    retry_after_buffer_seconds: float
    retry_after_fallback_seconds: float
    manual_reserve_fraction: float = 0.0
    manual_reserve_unlock_seconds: float = 10.0
    key_prefix: str = "league-api:riot-rate-limit"
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    monotonic: Callable[[], float] = time.monotonic

    @property
    def limit_label(self) -> str:
        return "-".join(
            f"{limit.request_count}/{_format_window_seconds(limit.window_seconds)}"
            for limit in self.limits
        )

    async def acquire(
        self,
        *,
        audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        on_wait: RateLimitWaitCallback | None = None,
    ) -> None:
        while True:
            delay = await self._reserve_or_delay(audience)
            if delay <= 0:
                return
            if on_wait is not None:
                await on_wait(delay)
            await self.sleep(delay)

    async def try_acquire(
        self,
        *,
        audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
    ) -> tuple[bool, float]:
        delay = await self._reserve_or_delay(audience)
        return delay <= 0, max(delay, 0.0)

    async def pause_for_retry_after(
        self,
        retry_after: str | None,
        *,
        on_wait: RateLimitWaitCallback | None = None,
    ) -> None:
        delay = self._retry_after_delay(retry_after)
        if delay <= 0:
            return

        blocked_until = self.monotonic() + delay
        key = f"{self.key_prefix}:blocked-until"
        current_value = await self.redis_client.get(key)
        current = float(current_value) if current_value else 0.0
        if blocked_until > current:
            await self.redis_client.set(key, blocked_until, ex=math.ceil(delay))

        if on_wait is not None:
            await on_wait(delay)
        await self.sleep(delay)

    async def _reserve_or_delay(self, audience: RiotRateLimitAudience) -> float:
        now = self.monotonic()
        lock = self.redis_client.lock(f"{self.key_prefix}:lock", timeout=5)
        await lock.acquire()
        try:
            blocked_until_value = await self.redis_client.get(f"{self.key_prefix}:blocked-until")
            blocked_until = float(blocked_until_value) if blocked_until_value else 0.0
            if blocked_until > now:
                return blocked_until - now

            wait_until = now
            for index, limit in enumerate(self.limits):
                key = f"{self.key_prefix}:window:{index}"
                cutoff = now - limit.window_seconds
                await self.redis_client.zremrangebyscore(key, "-inf", cutoff)
                count = await self.redis_client.zcard(key)

                if count >= limit.request_count:
                    oldest = await self.redis_client.zrange(key, 0, 0, withscores=True)
                    if oldest:
                        wait_until = max(wait_until, float(oldest[0][1]) + limit.window_seconds)
                    continue

                if audience is RiotRateLimitAudience.AUTOMATIC:
                    automatic_capacity = self._automatic_capacity(limit)
                    if count >= automatic_capacity:
                        if count == 0:
                            if limit.window_seconds <= self.manual_reserve_unlock_seconds:
                                continue
                            msg = (
                                "Automatic Riot requests have no configured rate-limit capacity. "
                                "Increase the Riot app rate-limit request counts or reduce "
                                "RIOT_MANUAL_RATE_LIMIT_RESERVE_FRACTION."
                            )
                            raise RiotConfigurationError(msg)
                        oldest = await self.redis_client.zrange(key, 0, 0, withscores=True)
                        if oldest:
                            seconds_until_oldest_expires = (
                                float(oldest[0][1]) + limit.window_seconds - now
                            )
                            if seconds_until_oldest_expires <= self.manual_reserve_unlock_seconds:
                                continue
                            wait_until = max(
                                wait_until,
                                float(oldest[0][1])
                                + limit.window_seconds
                                - self.manual_reserve_unlock_seconds,
                            )

            delay = wait_until - now
            if delay > 0:
                return delay

            reservation_id = f"{now}:{audience.value}"
            for index, limit in enumerate(self.limits):
                key = f"{self.key_prefix}:window:{index}"
                await self.redis_client.zadd(key, {reservation_id: now})
                await self.redis_client.expire(key, math.ceil(limit.window_seconds))
            return 0.0
        finally:
            await lock.release()

    def _automatic_capacity(self, limit: RiotRateLimit) -> int:
        reserved_count = math.ceil(limit.request_count * self.manual_reserve_fraction)
        return max(limit.request_count - reserved_count, 0)

    def _retry_after_delay(self, retry_after: str | None) -> float:
        retry_after_seconds = self._parse_retry_after(retry_after)
        if retry_after_seconds is None:
            retry_after_seconds = self.retry_after_fallback_seconds
        return max(0.0, retry_after_seconds + self.retry_after_buffer_seconds)

    def _parse_retry_after(self, retry_after: str | None) -> float | None:
        if retry_after is None or not retry_after.strip():
            return None

        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass

        try:
            retry_at = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError):
            return None

        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _format_window_seconds(window_seconds: float) -> str:
    if window_seconds.is_integer():
        return f"{int(window_seconds)}s"
    return f"{window_seconds:g}s"
