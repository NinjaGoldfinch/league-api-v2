import asyncio
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from league_api.core.config import Settings

RateLimitWaitCallback = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RiotRateLimit:
    """Sliding-window request budget for the process-local Riot client."""

    request_count: int
    window_seconds: float


class RiotRateLimitManager:
    """Simple process-local Riot rate limiter."""

    def __init__(
        self,
        *,
        limits: Sequence[RiotRateLimit],
        max_retries: int,
        retry_after_buffer_seconds: float,
        retry_after_fallback_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not limits:
            msg = "At least one Riot rate limit must be configured."
            raise ValueError(msg)

        self._limits = list(limits)
        self._windows = [(limit, deque[float]()) for limit in self._limits]
        self._max_retries = max_retries
        self._retry_after_buffer_seconds = retry_after_buffer_seconds
        self._retry_after_fallback_seconds = retry_after_fallback_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def limit_label(self) -> str:
        return "-".join(
            f"{limit.request_count}/{_format_window_seconds(limit.window_seconds)}"
            for limit in self._limits
        )

    async def acquire(self, *, on_wait: RateLimitWaitCallback | None = None) -> None:
        """Reserve one Riot request slot, waiting until all windows have capacity."""

        while True:
            delay = self._reserve_or_delay()
            if delay <= 0:
                return
            if on_wait is not None:
                await on_wait(delay)
            await self._sleep(delay)

    async def pause_for_retry_after(
        self,
        retry_after: str | None,
        *,
        on_wait: RateLimitWaitCallback | None = None,
    ) -> None:
        """Pause this process after a Riot 429 response before replaying the request."""

        delay = self._retry_after_delay(retry_after)
        if delay <= 0:
            return

        with self._lock:
            self._blocked_until = max(self._blocked_until, self._monotonic() + delay)

        if on_wait is not None:
            await on_wait(delay)
        await self._sleep(delay)

    def _reserve_or_delay(self) -> float:
        now = self._monotonic()
        with self._lock:
            blocked_delay = self._blocked_until - now
            if blocked_delay > 0:
                return blocked_delay

            wait_until = now
            for limit, timestamps in self._windows:
                cutoff = now - limit.window_seconds
                while timestamps and timestamps[0] <= cutoff:
                    timestamps.popleft()

                if len(timestamps) >= limit.request_count:
                    wait_until = max(wait_until, timestamps[0] + limit.window_seconds)

            delay = wait_until - now
            if delay > 0:
                return delay

            for _, timestamps in self._windows:
                timestamps.append(now)
            return 0.0

    def _retry_after_delay(self, retry_after: str | None) -> float:
        retry_after_seconds = self._parse_retry_after(retry_after)
        if retry_after_seconds is None:
            retry_after_seconds = self._retry_after_fallback_seconds
        return max(0.0, retry_after_seconds + self._retry_after_buffer_seconds)

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


_shared_rate_limiter_lock = threading.Lock()
_shared_rate_limiter: RiotRateLimitManager | None = None
_shared_rate_limiter_signature: tuple[int, float, int, float, int, float, float] | None = None


def get_riot_rate_limiter(settings: Settings) -> RiotRateLimitManager:
    """Return the process-local Riot app limiter for the active settings."""

    global _shared_rate_limiter, _shared_rate_limiter_signature

    signature = (
        settings.riot_app_rate_limit_short_requests,
        settings.riot_app_rate_limit_short_window_seconds,
        settings.riot_app_rate_limit_long_requests,
        settings.riot_app_rate_limit_long_window_seconds,
        settings.riot_rate_limit_max_retries,
        settings.riot_rate_limit_retry_after_buffer_seconds,
        settings.riot_rate_limit_retry_after_fallback_seconds,
    )

    with _shared_rate_limiter_lock:
        if _shared_rate_limiter is None or signature != _shared_rate_limiter_signature:
            _shared_rate_limiter = RiotRateLimitManager(
                limits=[
                    RiotRateLimit(
                        request_count=settings.riot_app_rate_limit_short_requests,
                        window_seconds=settings.riot_app_rate_limit_short_window_seconds,
                    ),
                    RiotRateLimit(
                        request_count=settings.riot_app_rate_limit_long_requests,
                        window_seconds=settings.riot_app_rate_limit_long_window_seconds,
                    ),
                ],
                max_retries=settings.riot_rate_limit_max_retries,
                retry_after_buffer_seconds=settings.riot_rate_limit_retry_after_buffer_seconds,
                retry_after_fallback_seconds=settings.riot_rate_limit_retry_after_fallback_seconds,
            )
            _shared_rate_limiter_signature = signature

        return _shared_rate_limiter


def _format_window_seconds(window_seconds: float) -> str:
    if window_seconds.is_integer():
        return f"{int(window_seconds)}s"
    return f"{window_seconds:g}s"
