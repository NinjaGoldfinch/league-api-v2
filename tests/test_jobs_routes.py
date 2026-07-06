import asyncio
from typing import cast

from fastapi.testclient import TestClient

from league_api.api.routes.jobs import get_job_queue, get_job_store
from league_api.jobs.models import (
    JobError,
    JobEvent,
    JobProgress,
    JobType,
    JobWait,
    LadderIngestionParams,
    LadderIngestionResult,
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
