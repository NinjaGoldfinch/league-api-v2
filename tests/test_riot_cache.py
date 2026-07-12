import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi import HTTPException
from pytest import LogCaptureFixture

from league_api.api.routes.riot import call_riot
from league_api.core.config import Settings
from league_api.riot.cache import (
    InMemoryRiotCacheStore,
    RiotCacheEntry,
    build_riot_cache_key,
)
from league_api.riot.client import RiotClient


class CacheReadFailsStore(InMemoryRiotCacheStore):
    async def get(self, cache_key: str) -> RiotCacheEntry | None:
        del cache_key
        msg = "cache read is unavailable"
        raise RuntimeError(msg)


class CacheWriteFailsStore(InMemoryRiotCacheStore):
    async def put(
        self,
        *,
        key: Any,
        payload: Any,
        status_code: int,
        headers: dict[str, str],
        ttl_seconds: int,
        stale_while_revalidate_seconds: int,
    ) -> RiotCacheEntry:
        del key, payload, status_code, headers, ttl_seconds, stale_while_revalidate_seconds
        msg = "cache write is unavailable"
        raise RuntimeError(msg)


def test_cache_key_normalizes_param_order_and_drops_none() -> None:
    first = build_riot_cache_key(
        method="GET",
        base_url="https://sea.api.riotgames.com",
        path="/lol/match/v5/matches/by-puuid/player-1/ids",
        params={"count": 20, "queue": None, "start": 0},
    )
    second = build_riot_cache_key(
        method="get",
        base_url="https://sea.api.riotgames.com",
        path="/lol/match/v5/matches/by-puuid/player-1/ids",
        params={"start": 0, "count": 20},
    )

    assert first.cache_key == second.cache_key
    assert first.params_hash == second.params_hash
    assert first.upstream_family == "match_v5"


@pytest.mark.asyncio
async def test_cache_put_prunes_entries_past_stale_window() -> None:
    store = InMemoryRiotCacheStore()
    old_key = build_riot_cache_key(
        method="GET",
        base_url="https://sea.api.riotgames.com",
        path="/lol/match/v5/matches/OC1_OLD",
        params=None,
    )
    now = datetime.now(UTC)
    store._entries[old_key.cache_key] = RiotCacheEntry(
        cache_key=old_key.cache_key,
        payload={"old": True},
        status_code=200,
        headers={},
        fetched_at=now - timedelta(minutes=3),
        expires_at=now - timedelta(minutes=2),
        stale_until=now - timedelta(minutes=1),
    )
    fresh_key = build_riot_cache_key(
        method="GET",
        base_url="https://sea.api.riotgames.com",
        path="/lol/match/v5/matches/OC1_NEW",
        params=None,
    )
    await store.put(
        key=fresh_key,
        payload={"new": True},
        status_code=200,
        headers={},
        ttl_seconds=60,
        stale_while_revalidate_seconds=10,
    )

    assert await store.get(old_key.cache_key) is None
    assert await store.get(fresh_key.cache_key) is not None


@pytest.mark.asyncio
async def test_riot_client_cache_miss_writes_cache(respx_mock: respx.MockRouter) -> None:
    cache_store = InMemoryRiotCacheStore()
    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(return_value=httpx.Response(200, json={"metadata": {"matchId": "OC1_1"}}))

    async with RiotClient(
        api_key="test-key",
        cache_store=cache_store,
        cache_enabled=True,
        cache_stale_while_revalidate_seconds=60,
        settings=Settings(CACHE_ENABLED=True),
    ) as client:
        first = await client.get_match_v5("/lol/match/v5/matches/OC1_1")
        second = await client.get_match_v5("/lol/match/v5/matches/OC1_1")

    assert first == {"metadata": {"matchId": "OC1_1"}}
    assert second == first
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_riot_client_returns_stale_cache_without_riot_call(
    respx_mock: respx.MockRouter,
) -> None:
    del respx_mock
    cache_store = InMemoryRiotCacheStore()
    cache_key = build_riot_cache_key(
        method="GET",
        base_url="https://sea.api.riotgames.com",
        path="/lol/match/v5/matches/OC1_1",
        params=None,
    )
    now = datetime.now(UTC)
    cache_store._entries[cache_key.cache_key] = RiotCacheEntry(
        cache_key=cache_key.cache_key,
        payload={"metadata": {"matchId": "OC1_1"}},
        status_code=200,
        headers={},
        fetched_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=5),
        stale_until=now + timedelta(minutes=5),
    )

    async with RiotClient(
        api_key="test-key",
        cache_store=cache_store,
        cache_enabled=True,
        cache_stale_while_revalidate_seconds=60,
        settings=Settings(CACHE_ENABLED=True),
    ) as client:
        payload = await client.get_match_v5("/lol/match/v5/matches/OC1_1")

    assert payload == {"metadata": {"matchId": "OC1_1"}}


@pytest.mark.asyncio
async def test_riot_client_bypass_cache_fetches_and_replaces_stale_payload(
    respx_mock: respx.MockRouter,
) -> None:
    cache_store = InMemoryRiotCacheStore()
    path = "/lol/match/v5/matches/by-puuid/player-1/ids"
    cache_key = build_riot_cache_key(
        method="GET",
        base_url="https://sea.api.riotgames.com",
        path=path,
        params={"start": 0, "count": 100},
    )
    now = datetime.now(UTC)
    cache_store._entries[cache_key.cache_key] = RiotCacheEntry(
        cache_key=cache_key.cache_key,
        payload=["OC1_OLD"],
        status_code=200,
        headers={},
        fetched_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=5),
        stale_until=now + timedelta(minutes=5),
    )
    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/by-puuid/player-1/ids",
        params={"start": 0, "count": 100},
    ).mock(return_value=httpx.Response(200, json=["OC1_NEW", "OC1_OLD"]))

    async with RiotClient(
        api_key="test-key",
        cache_store=cache_store,
        cache_enabled=True,
        cache_stale_while_revalidate_seconds=60,
        settings=Settings(CACHE_ENABLED=True),
    ) as client:
        payload = await client.get_match_v5(
            path,
            params={"start": 0, "count": 100},
            bypass_cache=True,
        )

    assert payload == ["OC1_NEW", "OC1_OLD"]
    assert route.call_count == 1
    cached = await cache_store.get(cache_key.cache_key)
    assert cached is not None
    assert cached.payload == payload


@pytest.mark.asyncio
async def test_cache_read_failure_logs_header_and_returns_live_response(
    respx_mock: respx.MockRouter,
    caplog: LogCaptureFixture,
) -> None:
    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(return_value=httpx.Response(200, json={"metadata": {"matchId": "OC1_1"}}))

    async with RiotClient(
        api_key="test-key",
        cache_store=CacheReadFailsStore(),
        cache_enabled=True,
        cache_stale_while_revalidate_seconds=60,
        settings=Settings(CACHE_ENABLED=True),
    ) as client:
        with caplog.at_level(logging.WARNING, logger="league_api.riot.client"):
            response = await call_riot(lambda: client.get_match_v5("/lol/match/v5/matches/OC1_1"))

    assert route.call_count == 1
    assert response.status_code == 200
    assert response.headers["x-league-api-cache"] == "error"
    assert response.headers["x-league-api-cache-read"] == "error"
    assert response.headers["x-league-api-cache-write"] == "stored"
    assert response.headers["x-league-api-cache-error"] == "read:RuntimeError"
    assert any(
        "Riot cache read failed; continuing with live Riot request." in message
        for message in caplog.messages
    )


@pytest.mark.asyncio
async def test_cache_write_failure_logs_header_and_returns_live_response(
    respx_mock: respx.MockRouter,
    caplog: LogCaptureFixture,
) -> None:
    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(return_value=httpx.Response(200, json={"metadata": {"matchId": "OC1_1"}}))

    async with RiotClient(
        api_key="test-key",
        cache_store=CacheWriteFailsStore(),
        cache_enabled=True,
        cache_stale_while_revalidate_seconds=60,
        settings=Settings(CACHE_ENABLED=True),
    ) as client:
        with caplog.at_level(logging.WARNING, logger="league_api.riot.client"):
            response = await call_riot(lambda: client.get_match_v5("/lol/match/v5/matches/OC1_1"))

    assert route.call_count == 1
    assert response.status_code == 200
    assert response.headers["x-league-api-cache"] == "error"
    assert response.headers["x-league-api-cache-read"] == "miss"
    assert response.headers["x-league-api-cache-write"] == "error"
    assert response.headers["x-league-api-cache-error"] == "write:RuntimeError"
    assert any(
        "Riot cache write failed; returning live Riot response." in message
        for message in caplog.messages
    )


@pytest.mark.asyncio
async def test_cache_disabled_still_returns_cache_bypass_headers(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(
        "https://sea.api.riotgames.com/lol/match/v5/matches/OC1_1",
    ).mock(return_value=httpx.Response(200, json={"metadata": {"matchId": "OC1_1"}}))

    async with RiotClient(
        api_key="test-key",
        cache_enabled=False,
        settings=Settings(CACHE_ENABLED=False),
    ) as client:
        response = await call_riot(lambda: client.get_match_v5("/lol/match/v5/matches/OC1_1"))

    assert route.call_count == 1
    assert response.status_code == 200
    assert response.headers["x-league-api-cache"] == "bypass"
    assert response.headers["x-league-api-cache-read"] == "disabled"
    assert response.headers["x-league-api-cache-write"] == "disabled"


@pytest.mark.asyncio
async def test_config_error_keeps_cache_headers_when_cache_unavailable() -> None:
    async with RiotClient(
        api_key=None,
        cache_enabled=False,
        settings=Settings(CACHE_ENABLED=True),
    ) as client:
        with pytest.raises(HTTPException) as exc_info:
            await call_riot(lambda: client.get_match_v5("/lol/match/v5/matches/OC1_1"))

    assert exc_info.value.status_code == 503
    assert exc_info.value.headers == {
        "X-League-API-Cache": "bypass",
        "X-League-API-Cache-Read": "unavailable",
        "X-League-API-Cache-Write": "unavailable",
    }
