import asyncio
from typing import cast

from fastapi.testclient import TestClient

from league_api.api.routes.jobs import get_job_queue, get_job_store
from league_api.jobs.models import (
    JobError,
    JobProgress,
    JobType,
    LadderIngestionParams,
    LadderIngestionResult,
)
from league_api.jobs.queue import InMemoryJobQueue
from league_api.jobs.store import InMemoryJobStore
from league_api.main import create_app


class FakeJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


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
    assert body["progress"] == {
        "players_discovered": 0,
        "players_processed": 0,
        "match_ids_discovered": 0,
        "unique_match_ids": 0,
        "duplicate_match_ids_skipped": 0,
        "matches_fetched": 0,
        "errors": 0,
    }
    assert fake_queue.enqueued == [body["job_id"]]

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
    assert status_response.json()["status"] == "queued"
    assert result_response.status_code == 202
    assert result_response.json()["message"] == "Job is still queued or running."


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
    assert body["summary"]["duplicate_match_ids_skipped"] == 1
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
    assert "/jobs/{job_id}" in openapi["paths"]
    assert "/jobs/{job_id}/result" in openapi["paths"]
