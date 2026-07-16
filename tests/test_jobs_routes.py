import asyncio
from typing import cast

import pytest
from fastapi.testclient import TestClient

from league_api.api.routes.jobs import get_job_queue, get_job_store
from league_api.core.config import get_settings
from league_api.jobs.models import (
    JobError,
    JobEvent,
    JobProgress,
    JobType,
    JobWait,
    LadderIngestionParams,
    LadderIngestionResult,
    ProfileFetchParams,
    ProfileFetchResult,
)
from league_api.jobs.queue import InMemoryJobQueue
from league_api.jobs.store import InMemoryJobStore
from league_api.main import create_app


class FakeJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, int]] = []

    async def enqueue(self, job_id: str, *, priority: int = 200) -> None:
        self.enqueued.append((job_id, priority))


def test_start_ladder_ingestion_job_returns_job_id_with_defaults() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)

    try:
        with TestClient(app) as test_client:
            response = test_client.post("/jobs/ingestion/ladder")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["job_type"] == "ladder_ingestion"
    assert body["status"] == "queued"
    assert body["details"] == {
        "source": "league_v4_apex_ladder",
        "platform_route": "oc1",
        "regional_route": "sea",
        "queue": "RANKED_SOLO_5x5",
        "queue_label": "Ranked Solo/Duo",
        "ladder": "challenger",
        "tier": "CHALLENGER",
        "division": None,
        "match_count_per_player": 20,
        "player_count": 0,
        "match_id_request_count": 0,
        "match_detail_request_count": 0,
    }
    assert body["estimate"]["stage"] == "queued"
    assert body["estimate"]["requests_total"] == 1
    assert body["estimate"]["estimated_completed_at"] is None
    assert body["progress"] == {
        "players_discovered": 0,
        "players_processed": 0,
        "match_id_pages_fetched": 0,
        "match_id_pages_with_results": 0,
        "match_ids_discovered": 0,
        "unique_match_ids": 0,
        "duplicate_match_ids_skipped": 0,
        "matches_fetched": 0,
        "errors": 0,
    }
    assert body["current_wait"] is None
    assert body["events"] == []
    assert fake_queue.enqueued == [(body["job_id"], 200)]

    stored_job = asyncio.run(store.get_job(body["job_id"]))
    assert stored_job is not None
    assert stored_job.params == LadderIngestionParams()


def test_start_ladder_ingestion_job_requires_configured_operator_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", "secret-token")
    get_settings.cache_clear()
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)

    try:
        with TestClient(app) as test_client:
            unauthorized = test_client.post("/jobs/ingestion/ladder")
            authorized = test_client.post(
                "/jobs/ingestion/ladder", headers={"X-Operator-Token": "secret-token"}
            )
    finally:
        app.dependency_overrides.clear()

    assert unauthorized.status_code == 401
    assert authorized.status_code == 202


def test_get_job_returns_status_and_result_returns_202_while_queued() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    job = asyncio.run(
        store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(),
        )
    )

    try:
        with TestClient(app) as test_client:
            status_response = test_client.get(f"/jobs/{job.job_id}")
            result_response = test_client.get(f"/jobs/{job.job_id}/result")
    finally:
        app.dependency_overrides.clear()

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "queued"
    assert status_body["estimate"]["stage"] == "queued"
    assert result_response.status_code == 202
    result_body = result_response.json()
    assert result_body["message"] == "Job is still queued or running."
    assert result_body["details"]["tier"] == "CHALLENGER"
    assert result_body["details"]["division"] is None
    assert result_body["estimate"]["requests_remaining"] == 1


def test_list_job_status_defaults_to_simple_running_jobs() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    queued_job = asyncio.run(
        store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(),
        )
    )
    running_job = asyncio.run(
        store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(match_count=5),
        )
    )
    completed_job = asyncio.run(
        store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(match_count=10),
        )
    )
    asyncio.run(store.mark_running(running_job.job_id))
    asyncio.run(
        store.update_progress(
            running_job.job_id,
            JobProgress(
                players_discovered=3,
                players_processed=1,
                unique_match_ids=5,
                matches_fetched=2,
            ),
        )
    )
    asyncio.run(
        store.mark_succeeded(
            completed_job.job_id,
            result=LadderIngestionResult(
                summary=JobProgress(players_discovered=1, players_processed=1),
                player_puuids=["puuid-1"],
                match_ids=[],
                matches={},
            ),
        )
    )

    try:
        with TestClient(app) as test_client:
            response = test_client.get("/jobs/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["running_only"] is True
    assert body["verbose"] is False
    assert [job["job_id"] for job in body["jobs"]] == [running_job.job_id, queued_job.job_id]

    running_status = body["jobs"][0]
    assert running_status["status"] == "running"
    assert running_status["details"] == {
        "source": "league_v4_apex_ladder",
        "platform_route": "oc1",
        "regional_route": "sea",
        "queue": "RANKED_SOLO_5x5",
        "queue_label": "Ranked Solo/Duo",
        "ladder": "challenger",
        "tier": "CHALLENGER",
        "division": None,
        "match_count_per_player": 5,
        "player_count": 3,
        "match_id_request_count": 3,
        "match_detail_request_count": 5,
    }
    assert running_status["estimate"]["stage"] == "match_ids"
    assert running_status["estimate"]["requests_completed"] == 4
    assert running_status["estimate"]["requests_total"] == 9
    assert running_status["estimate"]["requests_remaining"] == 5
    assert running_status["estimate"]["rate_limit_seconds_remaining"] == 6.0
    assert running_status["estimate"]["rate_limit_label"] == "20/1s-100/120s"
    assert running_status["estimate"]["estimated_seconds_remaining"] >= 6.0
    assert running_status["estimate"]["estimated_completed_at"] is not None
    assert "params" not in running_status
    assert "events" not in running_status
    assert "result" not in running_status


def test_list_job_status_verbose_flags_include_deeper_job_details() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    job = asyncio.run(
        store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(match_count=5),
        )
    )
    asyncio.run(
        store.record_event(
            job.job_id,
            JobEvent(
                event_type="request_started",
                message="Riot request started.",
                stage="match_ids",
                path="/lol/match/v5/matches/by-puuid/puuid-1/ids",
            ),
        )
    )

    try:
        with TestClient(app) as test_client:
            response = test_client.get(
                "/jobs/status?running_only=false&verbose=true&include_events=true&include_result=true"
            )
            query_response = test_client.request(
                "QUERY",
                "/jobs/status",
                json={
                    "running_only": False,
                    "verbose": True,
                    "include_events": True,
                    "include_result": True,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["running_only"] is False
    assert body["verbose"] is True
    assert body["include_events"] is True
    assert body["include_result"] is True
    assert body["jobs"][0]["job_id"] == job.job_id
    assert body["jobs"][0]["details"]["queue_label"] == "Ranked Solo/Duo"
    assert body["jobs"][0]["details"]["match_count_per_player"] == 5
    assert body["jobs"][0]["params"]["match_count"] == 5
    assert body["jobs"][0]["last_event"]["stage"] == "match_ids"
    assert body["jobs"][0]["events"][0]["path"] == "/lol/match/v5/matches/by-puuid/puuid-1/ids"
    assert body["jobs"][0]["result"] is None

    assert response.headers["accept-query"] == '"application/json"'
    assert query_response.status_code == 200
    assert query_response.headers["accept-query"] == '"application/json"'
    query_body = query_response.json()
    assert query_body.pop("generated_at")
    assert body.pop("generated_at")
    assert query_body == body


def test_list_job_status_supports_cursor_pagination() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    jobs = [
        asyncio.run(
            store.create_job(
                job_type=JobType.LADDER_INGESTION,
                params=LadderIngestionParams(match_count=match_count),
            )
        )
        for match_count in (5, 10, 15)
    ]

    try:
        with TestClient(app) as test_client:
            first_response = test_client.get("/jobs/status?limit=2")
            first_body = first_response.json()
            second_response = test_client.get(
                "/jobs/status",
                params={"limit": 2, "cursor": first_body["next_cursor"]},
            )
    finally:
        app.dependency_overrides.clear()

    assert first_response.status_code == 200
    assert first_body["limit"] == 2
    assert first_body["has_more"] is True
    assert first_body["next_cursor"]
    assert [job["job_id"] for job in first_body["jobs"]] == [
        jobs[2].job_id,
        jobs[1].job_id,
    ]

    assert second_response.status_code == 200
    second_body = second_response.json()
    assert second_body["has_more"] is False
    assert second_body["next_cursor"] is None
    assert [job["job_id"] for job in second_body["jobs"]] == [jobs[0].job_id]


def test_list_job_status_filters_by_status_type_and_riot_id() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    matching_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        )
    )
    other_profile_job = asyncio.run(
        store.create_job(
            job_type=JobType.PROFILE_FETCH,
            params=ProfileFetchParams(game_name="Other", tag_line="OCE"),
        )
    )
    ladder_job = asyncio.run(
        store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(),
        )
    )
    asyncio.run(
        store.mark_succeeded(
            matching_job.job_id,
            result=ProfileFetchResult(
                summary=JobProgress(),
                account={"puuid": "puuid-1", "gameName": "GameName", "tagLine": "OCE"},
                summoner={"puuid": "puuid-1", "summonerLevel": 100},
                match_ids=[],
                matches={},
            ),
        )
    )

    try:
        with TestClient(app) as test_client:
            get_response = test_client.get(
                "/jobs/status",
                params={
                    "running_only": False,
                    "status": "succeeded",
                    "job_type": "profile_fetch",
                    "riot_id": "gamename#oce",
                },
            )
            query_response = test_client.request(
                "QUERY",
                "/jobs/status",
                json={
                    "running_only": False,
                    "status": ["queued"],
                    "job_type": "profile_fetch",
                    "riot_id": "other#oce",
                    "limit": 10,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert get_response.status_code == 200
    get_body = get_response.json()
    assert [job["job_id"] for job in get_body["jobs"]] == [matching_job.job_id]
    assert get_body["jobs"][0]["status"] == "succeeded"
    assert get_body["jobs"][0]["job_type"] == "profile_fetch"

    assert query_response.status_code == 200
    query_body = query_response.json()
    assert [job["job_id"] for job in query_body["jobs"]] == [other_profile_job.job_id]
    assert ladder_job.job_id not in {job["job_id"] for job in query_body["jobs"]}


def test_query_job_status_validates_query_json_contract() -> None:
    app = create_app()

    with TestClient(app) as test_client:
        missing_content_type = test_client.request("QUERY", "/jobs/status", content=b"{}")
        unsupported_content_type = test_client.request(
            "QUERY",
            "/jobs/status",
            content=b"{}",
            headers={"Content-Type": "text/plain"},
        )
        malformed_json = test_client.request(
            "QUERY",
            "/jobs/status",
            content=b"{",
            headers={"Content-Type": "application/json"},
        )
        validation_error = test_client.request(
            "QUERY",
            "/jobs/status",
            json={"running_only": "not-a-bool"},
        )

    assert missing_content_type.status_code == 400
    assert missing_content_type.headers["accept-query"] == '"application/json"'
    assert unsupported_content_type.status_code == 415
    assert unsupported_content_type.headers["accept-query"] == '"application/json"'
    assert malformed_json.status_code == 422
    assert malformed_json.headers["accept-query"] == '"application/json"'
    assert validation_error.status_code == 422
    assert validation_error.headers["accept-query"] == '"application/json"'


def test_query_cors_preflight_allows_configured_frontend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", '["http://localhost:5173"]')
    get_settings.cache_clear()
    app = create_app()

    with TestClient(app) as test_client:
        response = test_client.options(
            "/jobs/status",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "QUERY",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "QUERY" in response.headers["access-control-allow-methods"]


def test_get_job_returns_rate_limit_wait_status_and_events() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    job = asyncio.run(
        store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(),
        )
    )
    resume_at = job.created_at
    asyncio.run(
        store.record_event(
            job.job_id,
            JobEvent(
                event_type="rate_limit_wait",
                message="Waiting for Riot 429.",
                stage="match_detail",
                path="/lol/match/v5/matches/OC1_1",
                wait_seconds=17.0,
                resume_at=resume_at,
                retry_after="17",
            ),
            current_wait=JobWait(
                reason="riot_429",
                message="Waiting for Riot 429.",
                resume_at=resume_at,
                wait_seconds=17.0,
                stage="match_detail",
                path="/lol/match/v5/matches/OC1_1",
            ),
        )
    )

    try:
        with TestClient(app) as test_client:
            status_response = test_client.get(f"/jobs/{job.job_id}")
            result_response = test_client.get(f"/jobs/{job.job_id}/result")
    finally:
        app.dependency_overrides.clear()

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["current_wait"]["reason"] == "riot_429"
    assert status_body["current_wait"]["resume_at"] == resume_at.isoformat().replace("+00:00", "Z")
    assert status_body["events"][0]["event_type"] == "rate_limit_wait"

    assert result_response.status_code == 202
    result_body = result_response.json()
    assert result_body["current_wait"]["reason"] == "riot_429"
    assert result_body["events"][0]["retry_after"] == "17"


def test_completed_job_result_contains_summary_and_payload() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    job = asyncio.run(
        store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(),
        )
    )
    result = LadderIngestionResult(
        summary=JobProgress(
            players_discovered=2,
            players_processed=2,
            match_ids_discovered=4,
            unique_match_ids=3,
            duplicate_match_ids_skipped=1,
            matches_fetched=3,
        ),
        player_puuids=["puuid-1", "puuid-2"],
        match_ids=["OC1_1", "OC1_2", "OC1_3"],
        matches={
            "OC1_1": {"metadata": {"matchId": "OC1_1"}, "info": {}},
            "OC1_2": {"metadata": {"matchId": "OC1_2"}, "info": {}},
            "OC1_3": {"metadata": {"matchId": "OC1_3"}, "info": {}},
        },
    )
    asyncio.run(store.mark_succeeded(job.job_id, result=result))

    try:
        with TestClient(app) as test_client:
            response = test_client.get(f"/jobs/{job.job_id}/result")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job.job_id
    assert body["status"] == "succeeded"
    assert body["details"]["tier"] == "CHALLENGER"
    assert body["summary"]["duplicate_match_ids_skipped"] == 1
    assert body["estimate"]["estimated_seconds_remaining"] == 0.0
    assert body["estimate"]["estimated_completed_at"] is not None
    assert body["player_puuids"] == ["puuid-1", "puuid-2"]
    assert body["match_ids"] == ["OC1_1", "OC1_2", "OC1_3"]
    assert body["matches"]["OC1_2"]["metadata"]["matchId"] == "OC1_2"


def test_failed_job_result_returns_failed_record_with_error_details() -> None:
    store = InMemoryJobStore()
    fake_queue = FakeJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: cast(InMemoryJobQueue, fake_queue)
    job = asyncio.run(
        store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(),
        )
    )
    asyncio.run(
        store.mark_failed(
            job.job_id,
            error=JobError(message="Riot API rate limit exceeded.", stage="match_ids"),
        )
    )

    try:
        with TestClient(app) as test_client:
            response = test_client.get(f"/jobs/{job.job_id}/result")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["details"]["source"] == "league_v4_apex_ladder"
    assert body["error"]["message"] == "Riot API rate limit exceeded."


def test_unknown_job_id_returns_404_and_openapi_includes_job_endpoints() -> None:
    app = create_app()

    with TestClient(app) as test_client:
        missing_status = test_client.get("/jobs/does-not-exist")
        missing_result = test_client.get("/jobs/does-not-exist/result")
        openapi = test_client.get("/openapi.json").json()

    assert missing_status.status_code == 404
    assert missing_result.status_code == 404
    assert "/jobs/ingestion/ladder" in openapi["paths"]
    assert "/jobs/status" in openapi["paths"]
    assert "/jobs/{job_id}" in openapi["paths"]
    assert "/jobs/{job_id}/result" in openapi["paths"]
    assert openapi["components"]["schemas"]["LeagueQueue"]["enum"] == [
        "RANKED_SOLO_5x5",
        "RANKED_FLEX_SR",
        "RANKED_FLEX_TT",
    ]
