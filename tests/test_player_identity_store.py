from datetime import UTC, datetime, timedelta

import pytest

from league_api.players.store import (
    InMemoryPlayerIdentityStore,
    PostgresPlayerIdentityStore,
    hydrate_identities,
)


@pytest.mark.asyncio
async def test_match_identity_hydration_keeps_the_newest_observed_riot_id() -> None:
    store = InMemoryPlayerIdentityStore()

    await hydrate_identities(store, _match(2000, "Current", "OCE"))
    await hydrate_identities(store, _match(1000, "Historical", "OLD"))

    assert await store.get_by_riot_id("Current", "oce") is not None
    assert await store.get_by_riot_id("Historical", "OLD") is None


@pytest.mark.asyncio
async def test_identity_lookup_can_require_a_recent_observation() -> None:
    store = InMemoryPlayerIdentityStore()
    old_timestamp = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
    await hydrate_identities(store, _match(old_timestamp, "Player", "OCE"))

    assert await store.get_by_riot_id("Player", "OCE") is not None
    assert await store.get_by_riot_id("Player", "OCE", max_age=timedelta(hours=24)) is None


def _match(game_creation: int, game_name: str, tag_line: str) -> dict[str, object]:
    return {
        "info": {
            "gameCreation": game_creation,
            "participants": [
                {
                    "puuid": "puuid-1",
                    "riotIdGameName": game_name,
                    "riotIdTagline": tag_line,
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_postgres_recent_identity_query_does_not_use_ambiguous_null_parameter() -> None:
    engine = _RecordingEngine()
    store = PostgresPlayerIdentityStore(engine)

    assert await store.get_by_riot_id("Player", "OCE", max_age=timedelta(hours=24)) is None

    assert ":cutoff is null" not in engine.sql
    assert "observed_at >= :cutoff" in engine.sql
    assert isinstance(engine.params["cutoff"], datetime)


class _RecordingResult:
    def mappings(self) -> "_RecordingResult":
        return self

    def first(self) -> None:
        return None


class _RecordingConnection:
    def __init__(self, engine: "_RecordingEngine") -> None:
        self.engine = engine

    async def __aenter__(self) -> "_RecordingConnection":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, query: object, params: dict[str, object]) -> _RecordingResult:
        self.engine.sql = str(query)
        self.engine.params = params
        return _RecordingResult()


class _RecordingEngine:
    def __init__(self) -> None:
        self.sql = ""
        self.params: dict[str, object] = {}

    def begin(self) -> _RecordingConnection:
        return _RecordingConnection(self)
