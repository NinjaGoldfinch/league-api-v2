import httpx
import pytest
import respx

from league_api.riot.client import RiotClient
from league_api.riot.errors import RiotConfigurationError, RiotRateLimitError


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
