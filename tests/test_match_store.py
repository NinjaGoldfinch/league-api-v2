from datetime import UTC, datetime, timedelta

import pytest

from league_api.matches.store import InMemoryMatchStore
from league_api.riot.cache import InMemoryRiotCacheStore, RiotCacheEntry, build_riot_cache_key


@pytest.mark.asyncio
async def test_in_memory_match_store_lists_unlinks_and_deletes() -> None:
    store = InMemoryMatchStore()
    await store.save_match(
        "OC1_2",
        regional_route="sea",
        payload={"info": {"gameCreation": 2}},
    )
    await store.save_match(
        "OC1_1",
        regional_route="sea",
        payload={"info": {"gameCreation": 1}},
    )
    await store.link_player_matches("player-1", ["OC1_1", "OC1_2"])

    page = await store.list_matches(search="OC1", puuid="player-1", offset=0, limit=1)
    assert page.total == 2
    assert [match.match_id for match in page.matches] == ["OC1_2"]
    assert page.matches[0].linked_puuids == ["player-1"]
    assert await store.count_matches() == 2
    assert await store.count_player_links() == 2

    assert await store.unlink_player_match("player-1", "OC1_1") is True
    assert await store.unlink_player_match("player-1", "OC1_1") is False
    assert await store.delete_match("OC1_2") is True
    assert await store.delete_match("OC1_2") is False
    assert await store.count_matches() == 1
    assert await store.count_player_links() == 0


@pytest.mark.asyncio
async def test_in_memory_cache_delete_count_and_prune_are_idempotent() -> None:
    store = InMemoryRiotCacheStore()
    key = build_riot_cache_key(
        method="GET",
        base_url="https://sea.api.riotgames.com",
        path="/lol/match/v5/matches/OC1_1",
        params=None,
    )
    now = datetime.now(UTC)
    store._entries[key.cache_key] = RiotCacheEntry(
        cache_key=key.cache_key,
        payload={},
        status_code=200,
        headers={},
        fetched_at=now - timedelta(minutes=3),
        expires_at=now - timedelta(minutes=2),
        stale_until=now - timedelta(minutes=1),
    )

    assert await store.count() == 1
    assert await store.prune_expired(now=now) == 1
    assert await store.prune_expired(now=now) == 0
    assert await store.delete(key.cache_key) is False
