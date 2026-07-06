from typing import cast

from fastapi.testclient import TestClient

from league_api.api.routes.ingestion import get_riot_client
from league_api.main import app
from league_api.riot.client import RiotClient
from league_api.riot.schemas import LeagueEntry


class RouteFakeRiotClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def __aenter__(self) -> "RouteFakeRiotClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None

    async def fetch_ladder_page(
        self,
        queue: str,
        tier: str,
        division: str | None = None,
        page: int | None = None,
        platform_route: str = "oc1",
    ) -> list[LeagueEntry]:
        self.requests.append(
            {
                "platform_route": platform_route,
                "queue": queue,
                "tier": tier,
                "division": division,
                "page": page,
            }
        )
        return [LeagueEntry(puuid="player-1"), LeagueEntry(puuid="player-2")]


def override_riot_client(fake_client: RouteFakeRiotClient) -> None:
    app.dependency_overrides[get_riot_client] = lambda: cast(
        RiotClient,
        fake_client,
    )


def test_ingestion_route_returns_summary_counts() -> None:
    fake_client = RouteFakeRiotClient()
    with TestClient(app) as test_client:
        override_riot_client(fake_client)
        try:
            response = test_client.get(
                "/ingestion/ladder-page",
                params={
                    "platform_route": "oc1",
                    "queue": "RANKED_SOLO_5x5",
                    "tier": "CHALLENGER",
                },
            )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake_client.requests == [
        {
            "platform_route": "oc1",
            "queue": "RANKED_SOLO_5x5",
            "tier": "CHALLENGER",
            "division": None,
            "page": None,
        }
    ]
    assert response.json() == {
        "platform_route": "oc1",
        "queue": "RANKED_SOLO_5x5",
        "tier": "CHALLENGER",
        "division": None,
        "page": None,
        "players_found": 2,
        "unique_players": 2,
        "entries": [
            {
                "puuid": "player-1",
                "summonerId": None,
                "leaguePoints": None,
                "wins": None,
                "losses": None,
                "veteran": None,
                "inactive": None,
                "freshBlood": None,
                "hotStreak": None,
            },
            {
                "puuid": "player-2",
                "summonerId": None,
                "leaguePoints": None,
                "wins": None,
                "losses": None,
                "veteran": None,
                "inactive": None,
                "freshBlood": None,
                "hotStreak": None,
            },
        ],
    }


def test_ingestion_route_supports_query_method_with_url_params() -> None:
    fake_client = RouteFakeRiotClient()
    with TestClient(app) as test_client:
        override_riot_client(fake_client)
        try:
            response = test_client.request(
                "QUERY",
                "/ingestion/ladder-page",
                params={
                    "platform_route": "oc1",
                    "queue": "RANKED_SOLO_5x5",
                    "tier": "DIAMOND",
                    "division": "I",
                    "page": "2",
                },
            )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake_client.requests == [
        {
            "platform_route": "oc1",
            "queue": "RANKED_SOLO_5x5",
            "tier": "DIAMOND",
            "division": "I",
            "page": 2,
        }
    ]
    assert response.json()["tier"] == "DIAMOND"
    assert response.json()["division"] == "I"
    assert response.json()["page"] == 2


def test_ingestion_route_supports_query_method_with_json_body() -> None:
    fake_client = RouteFakeRiotClient()
    with TestClient(app) as test_client:
        override_riot_client(fake_client)
        try:
            response = test_client.request(
                "QUERY",
                "/ingestion/ladder-page",
                json={
                    "platform_route": "oc1",
                    "queue": "RANKED_SOLO_5x5",
                    "tier": "EMERALD",
                    "division": "II",
                    "page": 3,
                },
            )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake_client.requests == [
        {
            "platform_route": "oc1",
            "queue": "RANKED_SOLO_5x5",
            "tier": "EMERALD",
            "division": "II",
            "page": 3,
        }
    ]
    assert response.json()["tier"] == "EMERALD"
    assert response.json()["division"] == "II"
    assert response.json()["page"] == 3


def test_ingestion_route_validates_query_method_json_body() -> None:
    fake_client = RouteFakeRiotClient()
    with TestClient(app) as test_client:
        override_riot_client(fake_client)
        try:
            response = test_client.request(
                "QUERY",
                "/ingestion/ladder-page",
                json={
                    "tier": "DIAMOND",
                    "division": "I",
                    "page": 0,
                },
            )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 422
    assert fake_client.requests == []


def test_openapi_documents_query_method_support() -> None:
    with TestClient(app) as test_client:
        schema = test_client.get("/openapi.json").json()

    path_schema = schema["paths"]["/ingestion/ladder-page"]
    operation = path_schema["get"]

    assert "query" not in path_schema
    assert operation["x-http-method-aliases"] == ["QUERY"]
    assert operation["x-query-request-body"] == {
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/LadderPageIngestionRequest"}
            }
        }
    }
    assert "HTTP `QUERY` method" in operation["description"]
    assert "LadderPageIngestionRequest" in operation["description"]
