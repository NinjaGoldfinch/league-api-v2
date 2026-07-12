from collections.abc import Iterator
from functools import partial
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from league_api.api.routes.riot import get_riot_client
from league_api.core.config import get_settings
from league_api.main import create_app
from league_api.riot.cache import InMemoryRiotCacheStore, build_riot_cache_key


class FakeManagerRiotClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def __aenter__(self) -> "FakeManagerRiotClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: object,
        bypass_cache: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del regional_route, kwargs
        match_id = path.rsplit("/", maxsplit=1)[-1]
        self.calls.append((match_id, bypass_cache))
        if match_id == "OC1_BAD":
            raise RuntimeError("upstream failed")
        return {"metadata": {"matchId": match_id}, "info": {"gameCreation": 1}}


@pytest.fixture
def manager_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Any]]:
    monkeypatch.setenv("EXPERIMENTAL_FRONTEND_ENABLED", "true")
    monkeypatch.setenv("CACHE_ENABLED", "true")
    get_settings.cache_clear()
    app = create_app()
    fake_client = FakeManagerRiotClient()
    app.dependency_overrides[get_riot_client] = lambda: fake_client
    with TestClient(app) as client:
        yield client, fake_client


def test_manager_routes_are_feature_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERIMENTAL_FRONTEND_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.get("/manager").status_code == 404
        assert client.get("/manager/api/summary").status_code == 404


def test_manager_fetch_inspect_unlink_and_delete(
    manager_client: tuple[TestClient, FakeManagerRiotClient],
) -> None:
    client, fake_client = manager_client
    response = client.post(
        "/manager/api/matches/fetch",
        json={
            "match_ids": ["OC1_1", "OC1_BAD"],
            "regional_route": "sea",
            "puuid": "player-1",
            "force_upstream": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["succeeded"] == 1
    assert response.json()["failed"] == 1
    assert fake_client.calls == [("OC1_1", True), ("OC1_BAD", True)]

    summary = client.get("/manager/api/summary").json()
    assert summary["durable_matches"] == 1
    assert summary["player_match_links"] == 1
    page = client.get("/manager/api/matches", params={"puuid": "player-1"}).json()
    assert page["total"] == 1
    assert page["matches"][0]["match_id"] == "OC1_1"
    detail = client.get("/manager/api/matches/OC1_1").json()
    assert detail["payload"]["metadata"]["matchId"] == "OC1_1"
    assert detail["linked_puuids"] == ["player-1"]

    unlink = client.delete("/manager/api/players/player-1/matches/OC1_1").json()
    assert unlink["player_link_deleted"] is True
    assert (
        client.delete("/manager/api/players/player-1/matches/OC1_1").json()["player_link_deleted"]
        is False
    )
    assert client.delete("/manager/api/matches/OC1_1").json()["durable_match_deleted"] is True
    assert client.get("/manager/api/matches/OC1_1").status_code == 404


def test_manager_evicts_exact_match_cache_and_prunes(
    manager_client: tuple[TestClient, FakeManagerRiotClient],
) -> None:
    client, _ = manager_client
    app = cast(FastAPI, client.app)
    cache_store = app.state.riot_cache_store
    assert isinstance(cache_store, InMemoryRiotCacheStore)
    key = build_riot_cache_key(
        method="GET",
        base_url="https://sea.api.riotgames.com",
        path="/lol/match/v5/matches/OC1_2",
        params=None,
    )
    assert client.portal is not None
    client.portal.call(
        partial(
            cache_store.put,
            key=key,
            payload={"metadata": {"matchId": "OC1_2"}},
            status_code=200,
            headers={},
            ttl_seconds=60,
            stale_while_revalidate_seconds=60,
        )
    )

    result = client.delete("/manager/api/cache/matches/OC1_2").json()
    assert result["cache_deleted"] is True
    assert client.delete("/manager/api/cache/matches/OC1_2").json()["cache_deleted"] is False
    assert client.post("/manager/api/cache/prune-expired").json() == {"pruned": 0}


def test_manager_frontend_contains_home_and_profile_controls(
    manager_client: tuple[TestClient, FakeManagerRiotClient],
) -> None:
    client, _ = manager_client
    manager = client.get("/manager")
    profile = client.get("/GameName-OCE")

    assert manager.status_code == 200
    assert '"page":"manager"' in manager.text
    assert 'id="manager-match-list"' in manager.text
    assert "/manager/api/matches/fetch" in manager.text
    assert "Remove only this player's link" in profile.text
    assert "Delete both" in profile.text
