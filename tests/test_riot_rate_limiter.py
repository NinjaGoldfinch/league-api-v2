import pytest

from league_api.riot.errors import RiotConfigurationError
from league_api.riot.rate_limiter import RiotRateLimit, RiotRateLimitAudience, RiotRateLimitManager


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


async def test_rate_limiter_waits_for_sliding_window_capacity() -> None:
    clock = FakeClock()
    limiter = RiotRateLimitManager(
        limits=[RiotRateLimit(request_count=2, window_seconds=10.0)],
        max_retries=3,
        retry_after_buffer_seconds=0.0,
        retry_after_fallback_seconds=120.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()

    assert clock.sleeps == [10.0]
    assert clock.now == 10.0


async def test_rate_limiter_pauses_for_retry_after_with_buffer() -> None:
    clock = FakeClock()
    limiter = RiotRateLimitManager(
        limits=[RiotRateLimit(request_count=100, window_seconds=120.0)],
        max_retries=3,
        retry_after_buffer_seconds=1.0,
        retry_after_fallback_seconds=120.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    await limiter.pause_for_retry_after("17")

    assert clock.sleeps == [18.0]
    assert clock.now == 18.0


async def test_automatic_requests_stop_at_reserved_capacity_while_manual_proceeds() -> None:
    clock = FakeClock()
    limiter = RiotRateLimitManager(
        limits=[RiotRateLimit(request_count=10, window_seconds=100.0)],
        max_retries=3,
        retry_after_buffer_seconds=0.0,
        retry_after_fallback_seconds=120.0,
        manual_reserve_fraction=0.2,
        manual_reserve_unlock_seconds=10.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    for _ in range(8):
        acquired, wait_seconds = limiter.try_acquire(audience=RiotRateLimitAudience.AUTOMATIC)
        assert acquired
        assert wait_seconds == 0.0

    acquired, wait_seconds = limiter.try_acquire(audience=RiotRateLimitAudience.AUTOMATIC)
    assert not acquired
    assert wait_seconds == 90.0

    acquired, wait_seconds = limiter.try_acquire(audience=RiotRateLimitAudience.MANUAL)
    assert acquired
    assert wait_seconds == 0.0


async def test_automatic_requests_use_zero_capacity_window_when_reserve_is_unlocked() -> None:
    clock = FakeClock()
    limiter = RiotRateLimitManager(
        limits=[RiotRateLimit(request_count=1, window_seconds=1.0)],
        max_retries=3,
        retry_after_buffer_seconds=0.0,
        retry_after_fallback_seconds=120.0,
        manual_reserve_fraction=0.2,
        manual_reserve_unlock_seconds=10.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    await limiter.acquire(audience=RiotRateLimitAudience.AUTOMATIC)

    assert clock.sleeps == []


async def test_automatic_requests_with_zero_capacity_fail_cleanly_on_empty_window() -> None:
    clock = FakeClock()
    limiter = RiotRateLimitManager(
        limits=[RiotRateLimit(request_count=1, window_seconds=120.0)],
        max_retries=3,
        retry_after_buffer_seconds=0.0,
        retry_after_fallback_seconds=120.0,
        manual_reserve_fraction=1.0,
        manual_reserve_unlock_seconds=10.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    with pytest.raises(RiotConfigurationError, match="no configured rate-limit capacity"):
        await limiter.acquire(audience=RiotRateLimitAudience.AUTOMATIC)


async def test_automatic_requests_can_use_reserved_capacity_near_window_reset() -> None:
    clock = FakeClock()
    limiter = RiotRateLimitManager(
        limits=[RiotRateLimit(request_count=10, window_seconds=100.0)],
        max_retries=3,
        retry_after_buffer_seconds=0.0,
        retry_after_fallback_seconds=120.0,
        manual_reserve_fraction=0.2,
        manual_reserve_unlock_seconds=10.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    for _ in range(8):
        acquired, wait_seconds = limiter.try_acquire(audience=RiotRateLimitAudience.AUTOMATIC)
        assert acquired
        assert wait_seconds == 0.0

    clock.now = 90.0
    acquired, wait_seconds = limiter.try_acquire(audience=RiotRateLimitAudience.AUTOMATIC)

    assert acquired
    assert wait_seconds == 0.0


async def test_manual_requests_remain_capped_by_total_limit() -> None:
    clock = FakeClock()
    limiter = RiotRateLimitManager(
        limits=[RiotRateLimit(request_count=3, window_seconds=30.0)],
        max_retries=3,
        retry_after_buffer_seconds=0.0,
        retry_after_fallback_seconds=120.0,
        manual_reserve_fraction=0.2,
        manual_reserve_unlock_seconds=10.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    for _ in range(3):
        acquired, wait_seconds = limiter.try_acquire(audience=RiotRateLimitAudience.MANUAL)
        assert acquired
        assert wait_seconds == 0.0

    acquired, wait_seconds = limiter.try_acquire(audience=RiotRateLimitAudience.MANUAL)

    assert not acquired
    assert wait_seconds == 30.0
