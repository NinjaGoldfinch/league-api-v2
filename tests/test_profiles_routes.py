import asyncio
from typing import Any, cast

from fastapi.testclient import TestClient

from league_api.api.routes.jobs import get_job_queue, get_job_store
from league_api.api.routes.riot import get_riot_client
from league_api.jobs.models import ProfileFetchParams
from league_api.jobs.queue import (
    PROFILE_FETCH_PRIORITY,
    PROFILE_MATCH_DETAILS_PRIORITY,
    InMemoryJobQueue,
)
from league_api.jobs.store import InMemoryJobStore
from league_api.main import create_app
from league_api.riot.client import RiotClient
from league_api.riot.errors import RiotApiError, RiotRateLimitWouldWaitError
from league_api.riot.rate_limiter import RiotRateLimitAudience


class FakeJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, int]] = []

    async def enqueue(self, job_id: str, *, priority: int = 200) -> None:
        self.enqueued.append((job_id, priority))


class FakeRiotClient:
    def __init__(
        self,
        *,
        wait_on_account: bool = False,
        fail_account: bool = False,
    ) -> None:
        self.wait_on_account = wait_on_account
        self.fail_account = fail_account
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

    async def get_account_v1(
        self,
        path: str,
        *,
        regional_route: str = "asia",
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "get_account_v1",
                "path": path,
                "regional_route": regional_route,
                "rate_limit_audience": rate_limit_audience,
                "wait_for_rate_limit": wait_for_rate_limit,
                "params": params,
            }
        )
        if self.wait_on_account:
            raise RiotRateLimitWouldWaitError("Would wait.", wait_seconds=12.0)
        if self.fail_account:
            raise RiotApiError("Riot API request failed with status 404.", status_code=404)
        return {"puuid": "puuid-1", "gameName": "GameName", "tagLine": "OCE"}

    async def get_summoner_v4(
        self,
        path: str,
        *,
        platform_route: str = "oc1",
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "get_summoner_v4",
                "path": path,
                "platform_route": platform_route,
                "rate_limit_audience": rate_limit_audience,
                "wait_for_rate_limit": wait_for_rate_limit,
                "params": params,
            }
        )
        return {"puuid": "puuid-1", "summonerLevel": 100}

    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str = "sea",
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> list[str]:
        self.calls.append(
            {
                "method": "get_match_v5",
                "path": path,
                "regional_route": regional_route,
                "rate_limit_audience": rate_limit_audience,
                "wait_for_rate_limit": wait_for_rate_limit,
                "params": params,
            }
        )
        return ["OC1_1", "OC1_2"]


def test_fetch_profile_fast_path_returns_identity_and_enqueues_match_details() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            response = test_client.post("/profiles/fetch", params={"riot_id": "GameName#OCE"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    body = response.json()
    assert body["identity_status"] == "resolved"
    assert body["account"]["puuid"] == "puuid-1"
    assert body["summoner"]["summonerLevel"] == 100
    assert body["match_ids"] == ["OC1_1", "OC1_2"]
    assert fake_queue.enqueued == [(body["job_id"], PROFILE_MATCH_DETAILS_PRIORITY)]
    assert [call["method"] for call in fake_riot_client.calls] == [
        "get_account_v1",
        "get_summoner_v4",
        "get_match_v5",
    ]
    assert all(
        call["rate_limit_audience"] is RiotRateLimitAudience.MANUAL
        for call in fake_riot_client.calls
    )
    assert all(call["wait_for_rate_limit"] is False for call in fake_riot_client.calls)

    stored_job = asyncio.run(store.get_job(body["job_id"]))
    assert stored_job is not None
    assert isinstance(stored_job.params, ProfileFetchParams)
    assert stored_job.params.account is not None
    assert stored_job.params.summoner is not None
    assert stored_job.params.match_ids == ["OC1_1", "OC1_2"]


def test_fetch_profile_rate_limit_wait_queues_full_profile_job() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient(wait_on_account=True)
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            response = test_client.post("/profiles/fetch", params={"riot_id": "GameName#OCE"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    body = response.json()
    assert body["identity_status"] == "queued"
    assert body["account"] is None
    assert body["summoner"] is None
    assert body["match_ids"] is None
    assert fake_queue.enqueued == [(body["job_id"], PROFILE_FETCH_PRIORITY)]


def test_fetch_profile_rejects_invalid_riot_id() -> None:
    app = create_app()

    with TestClient(app) as test_client:
        no_tag_response = test_client.post("/profiles/fetch", params={"riot_id": "GameName"})
        blank_tag_response = test_client.post("/profiles/fetch", params={"riot_id": "GameName# "})

    assert no_tag_response.status_code == 422
    assert blank_tag_response.status_code == 422


def test_fetch_profile_surfaces_riot_errors_before_queuing() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient(fail_account=True)
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            response = test_client.post("/profiles/fetch", params={"riot_id": "GameName#OCE"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert fake_queue.enqueued == []
