import httpx
import pytest
import respx

from league_api.riot.client import RiotClient
from league_api.riot.errors import RiotConfigurationError, RiotRateLimitError


@pytest.mark.asyncio
async def test_riot_client_builds_apex_ladder_url(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(
        "https://oc1.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5",
    ).mock(return_value=httpx.Response(200, json={"entries": [{"puuid": "player-1"}]}))

    async with RiotClient(api_key="test-key") as client:
        entries = await client.fetch_ladder_page(
            queue="RANKED_SOLO_5x5",
            tier="CHALLENGER",
        )

    assert route.called
    assert entries[0].puuid == "player-1"
    assert route.calls.last.request.headers["X-Riot-Token"] == "test-key"
    assert str(route.calls.last.request.url) == (
        "https://oc1.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5"
    )


@pytest.mark.asyncio
async def test_riot_client_builds_division_ladder_url(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(
        "https://oc1.api.riotgames.com/lol/league/v4/entries/RANKED_SOLO_5x5/DIAMOND/I",
        params={"page": "1"},
    ).mock(return_value=httpx.Response(200, json=[{"puuid": "player-1"}]))

    async with RiotClient(api_key="test-key") as client:
        entries = await client.fetch_ladder_page(
            queue="RANKED_SOLO_5x5",
            tier="DIAMOND",
            division="I",
            page=1,
        )

    assert route.called
    assert entries[0].puuid == "player-1"
    assert route.calls.last.request.headers["X-Riot-Token"] == "test-key"
    assert (
        str(route.calls.last.request.url) == "https://oc1.api.riotgames.com/lol/league/v4/entries/"
        "RANKED_SOLO_5x5/DIAMOND/I?page=1"
    )


@pytest.mark.asyncio
async def test_riot_client_builds_match_history_url(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/by-puuid/player-1/ids",
        params={"start": "0", "count": "20"},
    ).mock(return_value=httpx.Response(200, json=["OC1_1"]))

    async with RiotClient(api_key="test-key") as client:
        match_ids = await client.fetch_match_ids_by_puuid("player-1", count=20)

    assert route.called
    assert match_ids == ["OC1_1"]
    assert (
        str(route.calls.last.request.url)
        == "https://sea.api.riotgames.com/lol/match/v5/matches/by-puuid/"
        "player-1/ids?start=0&count=20"
    )


@pytest.mark.asyncio
async def test_riot_client_builds_match_detail_url(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(return_value=httpx.Response(200, json={"metadata": {"matchId": "OC1_1"}}))

    async with RiotClient(api_key="test-key") as client:
        match = await client.fetch_match_by_id("OC1_1")

    assert route.called
    assert match == {"metadata": {"matchId": "OC1_1"}}
    assert str(route.calls.last.request.url) == (
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1"
    )


@pytest.mark.asyncio
async def test_riot_client_missing_api_key_raises_configuration_error() -> None:
    async with RiotClient(api_key=None) as client:
        with pytest.raises(RiotConfigurationError, match="RIOT_API_KEY"):
            await client.fetch_match_by_id("OC1_1")


@pytest.mark.asyncio
async def test_riot_client_rate_limit_error_includes_retry_after(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(return_value=httpx.Response(429, headers={"Retry-After": "17"}))

    async with RiotClient(api_key="test-key") as client:
        with pytest.raises(RiotRateLimitError, match="Retry-After: 17") as exc_info:
            await client.fetch_match_by_id("OC1_1")

    assert exc_info.value.retry_after == "17"
