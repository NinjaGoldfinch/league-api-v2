import pytest

from league_api.jobs.models import (
    JobError,
    JobProgress,
    JobStatus,
    JobType,
    LadderIngestionParams,
    LadderIngestionResult,
)
from league_api.jobs.store import InMemoryJobStore


@pytest.mark.asyncio
async def test_create_job_returns_queued_status() -> None:
    store = InMemoryJobStore()

    job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(),
    )

    assert job.status is JobStatus.QUEUED
    assert job.progress == JobProgress()
    assert job.started_at is None
    assert job.finished_at is None


@pytest.mark.asyncio
async def test_store_updates_running_progress_success_and_failure() -> None:
    store = InMemoryJobStore()
    job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(),
    )

    running_job = await store.mark_running(job.job_id)
    assert running_job.status is JobStatus.RUNNING
    assert running_job.started_at is not None

    progress = JobProgress(players_discovered=2, players_processed=1)
    progressed_job = await store.update_progress(job.job_id, progress)
    assert progressed_job.progress.players_discovered == 2
    assert progressed_job.progress.players_processed == 1

    result = LadderIngestionResult(
        summary=JobProgress(players_discovered=2, players_processed=2),
        player_puuids=["puuid-1", "puuid-2"],
        match_ids=["OC1_1"],
        matches={"OC1_1": {"metadata": {"matchId": "OC1_1"}, "info": {}}},
    )
    succeeded_job = await store.mark_succeeded(job.job_id, result=result)
    assert succeeded_job.status is JobStatus.SUCCEEDED
    assert succeeded_job.finished_at is not None
    assert succeeded_job.result == result

    failed_job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(),
    )
    error = JobError(message="Riot API rate limit exceeded.", stage="match_ids")
    failed_record = await store.mark_failed(failed_job.job_id, error=error)
    assert failed_record.status is JobStatus.FAILED
    assert failed_record.error == error
