import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from league_api.api.routes.ladders import (
    get_ladder_player_store,
    get_match_reference_store,
    get_match_store,
)
from league_api.ladders.store import InMemoryLadderPlayerStore, LadderPlayer
from league_api.main import create_app
from league_api.matches.references import InMemoryMatchReferenceStore
from league_api.matches.store import InMemoryMatchStore


@pytest.fixture
def ladders_client() -> Iterator[TestClient]:
    players = InMemoryLadderPlayerStore()
    references = InMemoryMatchReferenceStore()
    matches = InMemoryMatchStore()
    asyncio.run(
        players.replace_target(
            platform_route="oc1",
            queue="RANKED_SOLO_5x5",
            tier="CHALLENGER",
            division=None,
            page=None,
            players=[_player("one"), _player("two")],
        )
    )
    asyncio.run(references.upsert("one", ["OC1_1", "OC1_2"]))
    asyncio.run(references.upsert("two", ["OC1_1"]))
    asyncio.run(
        matches.save_match(
            "OC1_1",
            regional_route="sea",
            payload={
                "metadata": {"matchId": "OC1_1"},
                "info": {
                    "gameCreation": 123,
                    "gameDuration": 1800,
                    "gameMode": "CLASSIC",
                    "queueId": 420,
                },
            },
        )
    )
    app = create_app()
    app.dependency_overrides[get_ladder_player_store] = lambda: players
    app.dependency_overrides[get_match_reference_store] = lambda: references
    app.dependency_overrides[get_match_store] = lambda: matches
    with TestClient(app) as client:
        yield client


def test_lists_deduplicated_ladder_matches_without_fetching(ladders_client: TestClient) -> None:
    response = ladders_client.get("/ladders/matches", params={"tier": "CHALLENGER"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["matches"][0] == {
        "match_id": "OC1_1",
        "player_count": 2,
        "is_duplicate": True,
        "detail_status": "stored",
        "game_creation": 123,
        "game_duration": 1800,
        "game_mode": "CLASSIC",
        "queue_id": 420,
    }
    assert body["matches"][1]["detail_status"] == "missing"


def test_reads_only_stored_match_detail(ladders_client: TestClient) -> None:
    stored = ladders_client.get("/ladders/matches/OC1_1")
    missing = ladders_client.get("/ladders/matches/OC1_missing")

    assert stored.status_code == 200
    assert stored.json()["payload"]["metadata"]["matchId"] == "OC1_1"
    assert missing.status_code == 404


def _player(puuid: str) -> LadderPlayer:
    return LadderPlayer(
        platform_route="oc1",
        queue="RANKED_SOLO_5x5",
        tier="CHALLENGER",
        division=None,
        page=None,
        puuid=puuid,
        league_points=100,
        wins=1,
        losses=1,
        rank=None,
        hot_streak=False,
        veteran=False,
        inactive=False,
        fresh_blood=False,
        game_name=None,
        tag_line=None,
        fetched_at=datetime.now(UTC),
    )
