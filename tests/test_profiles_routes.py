import asyncio
from typing import Any, cast

from fastapi.testclient import TestClient

from league_api.api.routes.jobs import get_job_queue, get_job_store
from league_api.api.routes.riot import get_riot_client
from league_api.jobs.models import (
    JobError,
    JobProgress,
    JobStatus,
    JobType,
    ProfileFetchParams,
    ProfileFetchResult,
)
from league_api.jobs.queue import (
    PROFILE_FETCH_PRIORITY,
    PROFILE_MATCH_DETAILS_PRIORITY,
    InMemoryJobQueue,
)
from league_api.jobs.store import InMemoryJobStore
from league_api.main import create_app
from league_api.riot.cache import InMemoryRiotCacheStore, build_riot_cache_key
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


def test_fetch_profile_fast_path_returns_identity_and_enqueues_profile_job() -> None:
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
    assert body["match_ids"] is None
    assert fake_queue.enqueued == [(body["job_id"], PROFILE_FETCH_PRIORITY)]
    assert [call["method"] for call in fake_riot_client.calls] == [
        "get_account_v1",
        "get_summoner_v4",
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
    assert stored_job.params.match_ids is None


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


def test_fetch_profile_reuses_active_matching_profile_job() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    active_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(
                game_name="GameName",
                tag_line="OCE",
                account={"puuid": "puuid-1"},
                summoner={"puuid": "puuid-1", "summonerLevel": 100},
                match_ids=["OC1_1"],
            ),
        )
    )
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            response = test_client.post("/profiles/fetch", params={"riot_id": " gamename#oce "})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == active_job.job_id
    assert body["identity_status"] == "already_running"
    assert body["account"] == {"puuid": "puuid-1"}
    assert body["summoner"] == {"puuid": "puuid-1", "summonerLevel": 100}
    assert body["match_ids"] == ["OC1_1"]
    assert fake_queue.enqueued == [(active_job.job_id, PROFILE_MATCH_DETAILS_PRIORITY)]
    assert fake_riot_client.calls == []


def test_fetch_profile_completed_job_does_not_block_new_refresh() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    completed_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    asyncio.run(store.mark_succeeded(completed_job.job_id, result=_profile_result()))
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
    assert body["job_id"] != completed_job.job_id
    assert body["identity_status"] == "resolved"
    assert fake_queue.enqueued == [(body["job_id"], PROFILE_FETCH_PRIORITY)]
    assert [call["method"] for call in fake_riot_client.calls] == [
        "get_account_v1",
        "get_summoner_v4",
    ]


def test_fetch_profile_route_difference_creates_separate_job() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    active_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/profiles/fetch",
                params={"riot_id": "GameName#OCE", "platform_route": "na1"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] != active_job.job_id
    assert body["identity_status"] == "resolved"
    assert fake_queue.enqueued == [(body["job_id"], PROFILE_FETCH_PRIORITY)]
    assert fake_riot_client.calls[1]["platform_route"] == "na1"


def test_get_profile_view_missing_profile_has_no_side_effects() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = InMemoryRiotCacheStore()
            response = test_client.get("/profiles/by-riot-id/GameName/OCE")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["profile"] == {
        "riot_id": "GameName#OCE",
        "game_name": "GameName",
        "tag_line": "OCE",
        "puuid": None,
    }
    assert body["status"] == {
        "state": "missing",
        "operation": None,
        "message": "No profile data has been populated yet.",
        "stage": None,
        "stage_description": None,
    }
    assert body["data_summary"] == {
        "account_available": False,
        "summoner_available": False,
        "match_ids_available": False,
        "match_details_available": False,
        "unique_match_ids": 0,
        "matches_available": 0,
        "matches_pending": 0,
        "last_updated_at": None,
        "refresh_after": None,
    }
    assert body["progress"] is None
    assert body["account"] is None
    assert body["matches"] == []
    assert body["matches_pagination"] == {
        "start": 0,
        "limit": 15,
        "returned": 0,
        "total": 0,
        "has_more": False,
        "next_start": None,
    }
    assert body["diagnostics"] == {
        "active_job": None,
        "latest_job": None,
        "cache": {"account": None, "summoner": None, "match_ids": None},
    }
    assert fake_queue.enqueued == []
    assert fake_riot_client.calls == []


def test_get_profile_view_returns_cached_partial_profile() -> None:
    cache_store = InMemoryRiotCacheStore()
    asyncio.run(
        _put_cache_entry(
            cache_store,
            base_url="https://asia.api.riotgames.com",
            path="/riot/account/v1/accounts/by-riot-id/GameName/OCE",
            payload={"puuid": "puuid-1", "gameName": "GameName", "tagLine": "OCE"},
        )
    )
    asyncio.run(
        _put_cache_entry(
            cache_store,
            base_url="https://oc1.api.riotgames.com",
            path="/lol/summoner/v4/summoners/by-puuid/puuid-1",
            payload={"puuid": "puuid-1", "summonerLevel": 100},
        )
    )
    asyncio.run(
        _put_cache_entry(
            cache_store,
            base_url="https://sea.api.riotgames.com",
            path="/lol/match/v5/matches/by-puuid/puuid-1/ids",
            params={"start": 0, "count": 20},
            payload=["OC1_1", "OC1_2"],
        )
    )
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = cache_store
            response = test_client.get("/profiles/by-riot-id/GameName/OCE")
            query_response = test_client.request(
                "QUERY",
                "/profiles/by-riot-id",
                json={
                    "riot_id": "GameName#OCE",
                    "account_regional_route": "asia",
                    "platform_route": "oc1",
                    "regional_route": "sea",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["state"] == "ready"
    assert body["status"]["operation"] is None
    assert body["data_summary"]["last_updated_at"] is not None
    assert body["data_summary"]["refresh_after"] is not None
    assert body["data_summary"]["unique_match_ids"] == 2
    assert body["data_summary"]["matches_available"] == 0
    assert body["data_summary"]["matches_pending"] == 2
    assert body["profile"]["puuid"] == "puuid-1"
    assert body["account"]["gameName"] == "GameName"
    assert body["summoner"]["summonerLevel"] == 100
    assert body["match_ids"] == ["OC1_1", "OC1_2"]
    assert body["matches"] == []
    assert body["diagnostics"]["cache"] == {
        "account": "hit",
        "summoner": "hit",
        "match_ids": "hit",
    }
    assert response.headers["accept-query"] == '"application/json"'
    assert query_response.status_code == 200
    assert query_response.headers["accept-query"] == '"application/json"'
    assert query_response.json() == body
    assert fake_queue.enqueued == []
    assert fake_riot_client.calls == []


def test_get_profile_view_includes_active_job() -> None:
    store = InMemoryJobStore()
    active_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = InMemoryRiotCacheStore()
            response = test_client.get("/profiles/by-riot-id/GameName/OCE")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["state"] == "populating"
    assert body["status"]["operation"] == "initial_population"
    assert body["status"]["stage"] == "queued"
    assert body["progress"]["job_id"] == active_job.job_id
    assert body["diagnostics"]["active_job"]["job_id"] == active_job.job_id
    assert body["diagnostics"]["active_job"]["status"] == JobStatus.QUEUED
    assert body["diagnostics"]["active_job"]["events"] == []
    assert body["diagnostics"]["latest_job"] is None
    assert fake_queue.enqueued == []
    assert fake_riot_client.calls == []


def test_get_profile_view_distinguishes_refresh_from_initial_population() -> None:
    store = InMemoryJobStore()
    completed_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    asyncio.run(store.mark_succeeded(completed_job.job_id, result=_profile_result()))
    active_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = InMemoryRiotCacheStore()
            response = test_client.get("/profiles/by-riot-id/GameName/OCE")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["state"] == "refreshing"
    assert body["status"]["operation"] == "refresh"
    assert body["progress"]["job_id"] == active_job.job_id
    assert body["data_summary"]["account_available"] is True
    assert body["diagnostics"]["latest_job"]["job_id"] == completed_job.job_id


def test_get_profile_view_reports_failed_initial_population() -> None:
    store = InMemoryJobStore()
    failed_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    asyncio.run(
        store.mark_failed(
            failed_job.job_id,
            error=JobError(message="Account lookup failed.", stage="account"),
        )
    )
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = InMemoryRiotCacheStore()
            response = test_client.get("/profiles/by-riot-id/GameName/OCE")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["state"] == "failed"
    assert body["status"]["operation"] is None
    assert body["status"]["stage"] == "account"
    assert body["progress"] is None
    assert body["diagnostics"]["latest_job"]["error"]["message"] == "Account lookup failed."


def test_get_profile_view_keeps_prior_data_ready_after_refresh_failure() -> None:
    store = InMemoryJobStore()
    completed_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    asyncio.run(store.mark_succeeded(completed_job.job_id, result=_profile_result()))
    failed_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    asyncio.run(
        store.mark_failed(
            failed_job.job_id,
            error=JobError(message="Refresh failed.", stage="match_ids"),
        )
    )
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = InMemoryRiotCacheStore()
            response = test_client.get("/profiles/by-riot-id/GameName/OCE")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["state"] == "ready"
    assert body["profile"]["puuid"] == "puuid-1"
    assert body["diagnostics"]["latest_job"]["job_id"] == failed_job.job_id
    assert body["diagnostics"]["latest_job"]["error"]["message"] == "Refresh failed."


def test_get_profile_view_includes_active_job_partial_match_summaries() -> None:
    store = InMemoryJobStore()
    active_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    asyncio.run(store.mark_running(active_job.job_id))
    asyncio.run(
        store.update_result(
            active_job.job_id,
            result=_profile_result(
                summary=JobProgress(
                    players_discovered=1,
                    players_processed=1,
                    match_ids_discovered=2,
                    unique_match_ids=2,
                    matches_fetched=1,
                ),
                match_ids=["OC1_1", "OC1_2"],
            ),
        )
    )
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = InMemoryRiotCacheStore()
            response = test_client.get("/profiles/by-riot-id/GameName/OCE")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["state"] == "populating"
    assert body["diagnostics"]["active_job"]["job_id"] == active_job.job_id
    assert body["progress"]["counters"]["unique_match_ids"] == 2
    assert body["data_summary"]["matches_available"] == 1
    assert body["data_summary"]["matches_pending"] == 1
    assert body["match_ids"] == ["OC1_1", "OC1_2"]
    assert body["matches"][0]["match_id"] == "OC1_1"
    assert body["matches"][0]["champion_name"] == "Ahri"
    assert len(body["matches"]) == 1
    assert fake_queue.enqueued == []
    assert fake_riot_client.calls == []


def test_get_profile_view_uses_completed_job_match_summaries() -> None:
    store = InMemoryJobStore()
    completed_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    asyncio.run(store.mark_succeeded(completed_job.job_id, result=_profile_result()))
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = InMemoryRiotCacheStore()
            response = test_client.get("/profiles/by-riot-id/GameName/OCE")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["state"] == "ready"
    assert body["status"]["operation"] is None
    assert body["profile"]["puuid"] == "puuid-1"
    assert body["diagnostics"]["latest_job"]["job_id"] == completed_job.job_id
    assert body["diagnostics"]["active_job"] is None
    assert body["matches"] == [
        {
            "match_id": "OC1_1",
            "game_creation": 1234567890,
            "game_duration": 1800,
            "game_mode": "CLASSIC",
            "queue_id": 420,
            "champion_id": 103,
            "champion_name": "Ahri",
            "win": True,
            "kills": 7,
            "deaths": 2,
            "assists": 9,
            "lane": "MIDDLE",
            "team_position": "MIDDLE",
        }
    ]
    assert fake_queue.enqueued == []
    assert fake_riot_client.calls == []


def test_get_profile_view_paginates_compact_match_summaries() -> None:
    store = InMemoryJobStore()
    completed_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    asyncio.run(store.mark_succeeded(completed_job.job_id, result=_profile_result_with_matches(20)))
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = InMemoryRiotCacheStore()
            first_response = test_client.get("/profiles/by-riot-id/GameName/OCE")
            second_response = test_client.get(
                "/profiles/by-riot-id/GameName/OCE",
                params={"match_start": 15, "match_limit": 10},
            )
            query_response = test_client.request(
                "QUERY",
                "/profiles/by-riot-id",
                json={
                    "riot_id": "GameName#OCE",
                    "match_start": 15,
                    "match_limit": 10,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert first_response.status_code == 200
    first_body = first_response.json()
    assert first_body["status"]["state"] == "ready"
    assert len(first_body["matches"]) == 15
    assert first_body["matches"][0]["match_id"] == "OC1_1"
    assert first_body["matches"][-1]["match_id"] == "OC1_15"
    assert first_body["matches_pagination"] == {
        "start": 0,
        "limit": 15,
        "returned": 15,
        "total": 20,
        "has_more": True,
        "next_start": 15,
    }

    assert second_response.status_code == 200
    second_body = second_response.json()
    assert [match["match_id"] for match in second_body["matches"]] == [
        "OC1_16",
        "OC1_17",
        "OC1_18",
        "OC1_19",
        "OC1_20",
    ]
    assert second_body["matches_pagination"] == {
        "start": 15,
        "limit": 10,
        "returned": 5,
        "total": 20,
        "has_more": False,
        "next_start": None,
    }
    assert query_response.status_code == 200
    assert query_response.headers["accept-query"] == '"application/json"'
    assert query_response.json()["matches"] == second_body["matches"]
    assert fake_queue.enqueued == []
    assert fake_riot_client.calls == []


def test_get_fetch_profile_returns_cached_profile_without_side_effects() -> None:
    cache_store = InMemoryRiotCacheStore()
    asyncio.run(
        _put_cache_entry(
            cache_store,
            base_url="https://asia.api.riotgames.com",
            path="/riot/account/v1/accounts/by-riot-id/GameName/OCE",
            payload={"puuid": "puuid-1", "gameName": "GameName", "tagLine": "OCE"},
        )
    )
    asyncio.run(
        _put_cache_entry(
            cache_store,
            base_url="https://oc1.api.riotgames.com",
            path="/lol/summoner/v4/summoners/by-puuid/puuid-1",
            payload={"puuid": "puuid-1", "summonerLevel": 100},
        )
    )
    asyncio.run(
        _put_cache_entry(
            cache_store,
            base_url="https://sea.api.riotgames.com",
            path="/lol/match/v5/matches/by-puuid/puuid-1/ids",
            params={"start": 0, "count": 20},
            payload=["OC1_1", "OC1_2"],
        )
    )
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    app = create_app()
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = cache_store
            response = test_client.get("/profiles/fetch", params={"riot_id": "GameName#OCE"})
            query_response = test_client.request(
                "QUERY",
                "/profiles/fetch",
                json={
                    "riot_id": "GameName#OCE",
                    "account_regional_route": "asia",
                    "platform_route": "oc1",
                    "regional_route": "sea",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "identity_status": "cached",
        "account": {"puuid": "puuid-1", "gameName": "GameName", "tagLine": "OCE"},
        "summoner": {"puuid": "puuid-1", "summonerLevel": 100},
        "match_ids": ["OC1_1", "OC1_2"],
        "account_cache_status": "hit",
        "summoner_cache_status": "hit",
        "match_ids_cache_status": "hit",
    }
    assert response.headers["accept-query"] == '"application/json"'
    assert query_response.status_code == 200
    assert query_response.headers["accept-query"] == '"application/json"'
    assert query_response.json() == body
    assert fake_queue.enqueued == []
    assert fake_riot_client.calls == []
    assert len(cache_store._entries) == 3


def test_get_fetch_profile_404s_when_profile_is_not_cached() -> None:
    cache_store = InMemoryRiotCacheStore()
    fake_queue = FakeJobQueue()
    fake_riot_client = FakeRiotClient()
    app = create_app()
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    app.dependency_overrides[get_riot_client] = lambda: cast(RiotClient, fake_riot_client)

    try:
        with TestClient(app) as test_client:
            app.state.riot_cache_store = cache_store
            response = test_client.get("/profiles/fetch", params={"riot_id": "GameName#OCE"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile is not cached."
    assert fake_queue.enqueued == []
    assert fake_riot_client.calls == []
    assert cache_store._entries == {}


def _profile_result(
    *,
    summary: JobProgress | None = None,
    match_ids: list[str] | None = None,
) -> ProfileFetchResult:
    return ProfileFetchResult(
        summary=summary
        or JobProgress(
            players_discovered=1,
            players_processed=1,
            match_ids_discovered=1,
            unique_match_ids=1,
            matches_fetched=1,
        ),
        account={"puuid": "puuid-1", "gameName": "GameName", "tagLine": "OCE"},
        summoner={"puuid": "puuid-1", "summonerLevel": 100},
        match_ids=match_ids or ["OC1_1"],
        matches={
            "OC1_1": {
                "metadata": {"matchId": "OC1_1"},
                "info": {
                    "gameCreation": 1234567890,
                    "gameDuration": 1800,
                    "gameMode": "CLASSIC",
                    "queueId": 420,
                    "participants": [
                        {
                            "puuid": "puuid-1",
                            "championId": 103,
                            "championName": "Ahri",
                            "win": True,
                            "kills": 7,
                            "deaths": 2,
                            "assists": 9,
                            "lane": "MIDDLE",
                            "teamPosition": "MIDDLE",
                        }
                    ],
                },
            }
        },
    )


def _profile_result_with_matches(match_count: int) -> ProfileFetchResult:
    match_ids = [f"OC1_{index}" for index in range(1, match_count + 1)]
    matches: dict[str, dict[str, Any]] = {}
    for index, match_id in enumerate(match_ids, start=1):
        matches[match_id] = {
            "metadata": {"matchId": match_id},
            "info": {
                "gameCreation": 1234567890 + index,
                "gameDuration": 1800 + index,
                "gameMode": "CLASSIC",
                "queueId": 420,
                "participants": [
                    {
                        "puuid": "puuid-1",
                        "championId": 103,
                        "championName": "Ahri",
                        "win": index % 2 == 0,
                        "kills": index,
                        "deaths": 2,
                        "assists": 9,
                        "lane": "MIDDLE",
                        "teamPosition": "MIDDLE",
                    }
                ],
            },
        }
    return ProfileFetchResult(
        summary=JobProgress(
            players_discovered=1,
            players_processed=1,
            match_ids_discovered=match_count,
            unique_match_ids=match_count,
            matches_fetched=match_count,
        ),
        account={"puuid": "puuid-1", "gameName": "GameName", "tagLine": "OCE"},
        summoner={"puuid": "puuid-1", "summonerLevel": 100},
        match_ids=match_ids,
        matches=matches,
    )


async def _put_cache_entry(
    cache_store: InMemoryRiotCacheStore,
    *,
    base_url: str,
    path: str,
    payload: Any,
    params: dict[str, int | str | None] | None = None,
    ttl_seconds: int = 3600,
    stale_while_revalidate_seconds: int = 300,
) -> None:
    cache_key = build_riot_cache_key(
        method="GET",
        base_url=base_url,
        path=path,
        params=params,
    )
    await cache_store.put(
        key=cache_key,
        payload=payload,
        status_code=200,
        headers={},
        ttl_seconds=ttl_seconds,
        stale_while_revalidate_seconds=stale_while_revalidate_seconds,
    )
