import logging

import httpx
import pytest
import respx
from pytest import LogCaptureFixture

from league_api.riot.client import RiotClient, RiotRequestEvent
from league_api.riot.errors import RiotApiError, RiotConfigurationError, RiotRateLimitError
from league_api.riot.rate_limiter import RiotRateLimit, RiotRateLimitManager
from league_api.riot.routing import get_platform_base_url, get_regional_base_url


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


@pytest.mark.asyncio
async def test_riot_client_builds_match_history_url_with_all_filters(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/by-puuid/player-1/ids",
        params={
            "startTime": "1710000000",
            "endTime": "1710003600",
            "queue": "420",
            "type": "ranked",
            "start": "0",
            "count": "100",
        },
    ).mock(return_value=httpx.Response(200, json=["OC1_1"]))

    async with RiotClient(api_key="test-key") as client:
        match_ids = await client.get_match_v5(
            "/lol/match/v5/matches/by-puuid/player-1/ids",
            params={
                "startTime": 1710000000,
                "endTime": 1710003600,
                "queue": 420,
                "type": "ranked",
                "start": 0,
                "count": 100,
            },
        )

    assert route.called
    assert match_ids == ["OC1_1"]
    assert (
        str(route.calls.last.request.url)
        == "https://sea.api.riotgames.com/lol/match/v5/matches/by-puuid/"
        "player-1/ids?startTime=1710000000&endTime=1710003600&queue=420&type=ranked"
        "&start=0&count=100"
    )


@pytest.mark.asyncio
async def test_riot_client_builds_match_detail_url(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(return_value=httpx.Response(200, json={"metadata": {"matchId": "OC1_1"}}))

    async with RiotClient(api_key="test-key") as client:
        match = await client.get_match_v5("/lol/match/v5/matches/OC1_1")

    assert route.called
    assert match == {"metadata": {"matchId": "OC1_1"}}
    assert str(route.calls.last.request.url) == (
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1"
    )


@pytest.mark.asyncio
async def test_riot_client_builds_generic_match_v5_url(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1/timeline",
    ).mock(return_value=httpx.Response(200, json={"metadata": {"matchId": "OC1_1"}}))

    async with RiotClient(api_key="test-key") as client:
        timeline = await client.get_match_v5("/lol/match/v5/matches/OC1_1/timeline")

    assert route.called
    assert timeline == {"metadata": {"matchId": "OC1_1"}}


@pytest.mark.asyncio
async def test_riot_client_builds_generic_league_v4_url(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(
        "https://oc1.api.riotgames.com/lol/league/v4/entries/by-puuid/player-1",
    ).mock(return_value=httpx.Response(200, json=[]))

    async with RiotClient(api_key="test-key") as client:
        entries = await client.get_league_v4("/lol/league/v4/entries/by-puuid/player-1")

    assert route.called
    assert entries == []


@pytest.mark.asyncio
async def test_riot_client_missing_api_key_raises_configuration_error() -> None:
    async with RiotClient(api_key=None) as client:
        with pytest.raises(RiotConfigurationError, match="RIOT_API_KEY"):
            await client.get_match_v5("/lol/match/v5/matches/OC1_1")


@pytest.mark.asyncio
async def test_riot_client_rate_limit_error_includes_retry_after(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(return_value=httpx.Response(429, headers={"Retry-After": "17"}))

    async with RiotClient(api_key="test-key") as client:
        with pytest.raises(RiotRateLimitError, match="Retry-After: 17") as exc_info:
            await client.get_match_v5("/lol/match/v5/matches/OC1_1")

    assert exc_info.value.retry_after == "17"


@pytest.mark.asyncio
async def test_riot_client_request_budget_wait_is_reported_as_riot_rate_limit(
    respx_mock: respx.MockRouter,
    caplog: LogCaptureFixture,
) -> None:
    clock = FakeClock()
    limiter = RiotRateLimitManager(
        limits=[RiotRateLimit(request_count=1, window_seconds=10.0)],
        max_retries=0,
        retry_after_buffer_seconds=0.0,
        retry_after_fallback_seconds=120.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    events: list[RiotRequestEvent] = []

    async def record_event(event: RiotRequestEvent) -> None:
        events.append(event)

    respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(return_value=httpx.Response(200, json={"metadata": {"matchId": "OC1_1"}}))

    async with RiotClient(
        api_key="test-key",
        rate_limiter=limiter,
        request_event_handler=record_event,
        request_logs_enabled=True,
    ) as client:
        with caplog.at_level(logging.INFO, logger="league_api.riot.client"):
            await client.get_match_v5("/lol/match/v5/matches/OC1_1")
            await client.get_match_v5("/lol/match/v5/matches/OC1_1")

    wait_events = [event for event in events if event.event_type == "rate_limit_wait"]
    assert clock.sleeps == [10.0]
    assert len(wait_events) == 1
    assert wait_events[0].rate_limit_reason == "riot_rate_limit"
    assert any(
        "Riot      rate-limit wait limit=1/10s reason=riot_rate_limit resumes_at=" in message
        for message in caplog.messages
    )


@pytest.mark.asyncio
async def test_riot_client_waits_and_retries_after_rate_limit(
    respx_mock: respx.MockRouter,
    caplog: LogCaptureFixture,
) -> None:
    clock = FakeClock()
    limiter = RiotRateLimitManager(
        limits=[
            RiotRateLimit(request_count=20, window_seconds=1.0),
            RiotRateLimit(request_count=100, window_seconds=120.0),
        ],
        max_retries=1,
        retry_after_buffer_seconds=0.0,
        retry_after_fallback_seconds=120.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    calls = 0
    events: list[RiotRequestEvent] = []

    async def record_event(event: RiotRequestEvent) -> None:
        events.append(event)

    def response(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "17"}, request=request)
        return httpx.Response(200, json={"metadata": {"matchId": "OC1_1"}}, request=request)

    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(side_effect=response)

    async with RiotClient(
        api_key="test-key",
        rate_limiter=limiter,
        request_event_handler=record_event,
        request_logs_enabled=True,
    ) as client:
        with caplog.at_level(logging.INFO, logger="league_api.riot.client"):
            match = await client.get_match_v5("/lol/match/v5/matches/OC1_1")

    assert route.call_count == 2
    assert match == {"metadata": {"matchId": "OC1_1"}}
    assert clock.sleeps == [17.0]
    assert [event.event_type for event in events] == [
        "request_started",
        "request_failed",
        "rate_limit_wait",
        "request_started",
        "request_succeeded",
    ]
    wait_event = events[2]
    assert wait_event.rate_limit_reason == "riot_429"
    assert wait_event.resume_at is not None
    assert any(
        'Riot      "GET /lol/match/v5/matches/OC1_1" 429 Too Many Requests '
        "attempt=1 retry_after=17 limit=20/1s-100/120s" in message
        for message in caplog.messages
    )
    assert any(
        "Riot      rate-limit wait limit=20/1s-100/120s reason=riot_429 resumes_at=" in message
        for message in caplog.messages
    )
    assert any(
        'Riot      "GET /lol/match/v5/matches/OC1_1" 200 OK '
        "attempt=2 limit=20/1s-100/120s" in message
        for message in caplog.messages
    )


@pytest.mark.asyncio
async def test_riot_client_request_logs_can_be_disabled(
    respx_mock: respx.MockRouter,
    caplog: LogCaptureFixture,
) -> None:
    respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(return_value=httpx.Response(200, json={"metadata": {"matchId": "OC1_1"}}))

    async with RiotClient(api_key="test-key", request_logs_enabled=False) as client:
        with caplog.at_level(logging.INFO, logger="league_api.riot.client"):
            match = await client.get_match_v5("/lol/match/v5/matches/OC1_1")

    assert match == {"metadata": {"matchId": "OC1_1"}}
    assert not any(message.startswith("Riot      ") for message in caplog.messages)


def test_riot_route_builders_reject_non_riot_hosts() -> None:
    with pytest.raises(RiotApiError, match="Unsupported Riot regional route"):
        get_regional_base_url("attacker.example/anything")

    with pytest.raises(RiotApiError, match="Unsupported Riot platform route"):
        get_platform_base_url("attacker.example/anything")


def test_riot_route_builders_accept_documented_routes_case_insensitively() -> None:
    assert get_regional_base_url("SEA") == "https://sea.api.riotgames.com"
    assert get_platform_base_url("OC1") == "https://oc1.api.riotgames.com"
