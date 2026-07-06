from league_api.riot.rate_limiter import RiotRateLimit, RiotRateLimitManager


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
