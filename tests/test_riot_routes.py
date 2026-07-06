from typing import Any, cast

from fastapi.testclient import TestClient

from league_api.api.routes.riot import get_riot_client
from league_api.main import app
from league_api.riot.client import RiotClient


class FakeRiotClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "FakeRiotClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None

    async def fetch_match_ids_by_puuid(
        self,
        puuid: str,
        start: int = 0,
        count: int = 20,
        regional_route: str = "sea",
        start_time: int | None = None,
        end_time: int | None = None,
        queue: int | None = None,
        match_type: str | None = None,
    ) -> list[str]:
        self.calls.append(
            {
                "method": "fetch_match_ids_by_puuid",
                "puuid": puuid,
                "regional_route": regional_route,
                "startTime": start_time,
                "endTime": end_time,
                "queue": queue,
                "type": match_type,
                "start": start,
                "count": count,
            }
        )
        return ["OC1_1"]

    async def fetch_match_by_id(
        self,
        match_id: str,
        regional_route: str = "sea",
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "fetch_match_by_id",
                "match_id": match_id,
                "regional_route": regional_route,
            }
        )
        return {"metadata": {"matchId": match_id}}

    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str = "sea",
        params: dict[str, int | str | None] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "get_match_v5",
                "path": path,
                "regional_route": regional_route,
                "params": params,
            }
        )
        return {"path": path}

    async def get_league_v4(
        self,
        path: str,
        *,
        platform_route: str = "oc1",
        params: dict[str, int | str | None] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "get_league_v4",
                "path": path,
                "platform_route": platform_route,
                "params": params,
            }
        )
        return {"path": path}


def test_match_ids_route_forwards_all_query_flags() -> None:
    fake_client = FakeRiotClient()
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_client)
    try:
        with TestClient(app) as test_client:
            response = test_client.get(
                "/lol/match/v5/matches/by-puuid/player-1/ids",
                params={
                    "regional_route": "SEA",
                    "startTime": "1710000000",
                    "endTime": "1710003600",
                    "queue": "420",
                    "type": "ranked",
                    "start": "5",
                    "count": "100",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == ["OC1_1"]
    assert fake_client.calls == [
        {
            "method": "fetch_match_ids_by_puuid",
            "puuid": "player-1",
            "regional_route": "SEA",
            "startTime": 1710000000,
            "endTime": 1710003600,
            "queue": 420,
            "type": "ranked",
            "start": 5,
            "count": 100,
        }
    ]


def test_match_detail_and_timeline_routes_mirror_match_v5_paths() -> None:
    fake_client = FakeRiotClient()
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_client)
    try:
        with TestClient(app) as test_client:
            detail_response = test_client.get(
                "/lol/match/v5/matches/OC1_1",
                params={"regional_route": "SEA"},
            )
            timeline_response = test_client.get(
                "/lol/match/v5/matches/OC1_1/timeline",
                params={"regional_route": "SEA"},
            )
            replay_response = test_client.get(
                "/lol/match/v5/matches/by-puuid/player-1/replays",
                params={"regional_route": "SEA"},
            )
    finally:
        app.dependency_overrides.clear()

    assert detail_response.status_code == 200
    assert timeline_response.status_code == 200
    assert replay_response.status_code == 200
    assert fake_client.calls == [
        {
            "method": "fetch_match_by_id",
            "match_id": "OC1_1",
            "regional_route": "SEA",
        },
        {
            "method": "get_match_v5",
            "path": "/lol/match/v5/matches/OC1_1/timeline",
            "regional_route": "SEA",
            "params": None,
        },
        {
            "method": "get_match_v5",
            "path": "/lol/match/v5/matches/by-puuid/player-1/replays",
            "regional_route": "SEA",
            "params": None,
        },
    ]


def test_league_v4_routes_mirror_paths_and_page_flag() -> None:
    fake_client = FakeRiotClient()
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_client)
    try:
        with TestClient(app) as test_client:
            responses = [
                test_client.get(
                    "/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5",
                    params={"platform_route": "OC1"},
                ),
                test_client.get(
                    "/lol/league/v4/entries/by-puuid/player-1",
                    params={"platform_route": "OC1"},
                ),
                test_client.get(
                    "/lol/league/v4/entries/RANKED_SOLO_5x5/DIAMOND/I",
                    params={"platform_route": "OC1", "page": "2"},
                ),
                test_client.get(
                    "/lol/league/v4/grandmasterleagues/by-queue/RANKED_SOLO_5x5",
                    params={"platform_route": "OC1"},
                ),
                test_client.get(
                    "/lol/league/v4/masterleagues/by-queue/RANKED_SOLO_5x5",
                    params={"platform_route": "OC1"},
                ),
            ]
    finally:
        app.dependency_overrides.clear()

    assert [response.status_code for response in responses] == [200, 200, 200, 200, 200]
    assert fake_client.calls == [
        {
            "method": "get_league_v4",
            "path": "/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5",
            "platform_route": "OC1",
            "params": None,
        },
        {
            "method": "get_league_v4",
            "path": "/lol/league/v4/entries/by-puuid/player-1",
            "platform_route": "OC1",
            "params": None,
        },
        {
            "method": "get_league_v4",
            "path": "/lol/league/v4/entries/RANKED_SOLO_5x5/DIAMOND/I",
            "platform_route": "OC1",
            "params": {"page": 2},
        },
        {
            "method": "get_league_v4",
            "path": "/lol/league/v4/grandmasterleagues/by-queue/RANKED_SOLO_5x5",
            "platform_route": "OC1",
            "params": None,
        },
        {
            "method": "get_league_v4",
            "path": "/lol/league/v4/masterleagues/by-queue/RANKED_SOLO_5x5",
            "platform_route": "OC1",
            "params": None,
        },
    ]


def test_mirror_endpoints_are_get_only_and_documented() -> None:
    with TestClient(app) as test_client:
        post_response = test_client.post("/lol/match/v5/matches/OC1_1")
        openapi = test_client.get("/openapi.json").json()

    assert post_response.status_code == 405
    assert "/health" not in openapi["paths"]
    assert not any(path.startswith("/ingestion") for path in openapi["paths"])
    assert set(openapi["paths"]["/lol/match/v5/matches/{matchId}"]) == {"get"}
    assert set(openapi["paths"]["/lol/league/v4/entries/{queue}/{tier}/{division}"]) == {"get"}
    assert "startTime" in {
        parameter["name"]
        for parameter in openapi["paths"]["/lol/match/v5/matches/by-puuid/{puuid}/ids"]["get"][
            "parameters"
        ]
    }
