import pytest

from league_api.jobs.models import (
    JobError,
    JobEvent,
    JobProgress,
    JobStatus,
    JobType,
    JobWait,
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


@pytest.mark.asyncio
async def test_store_lists_jobs_by_status_newest_first() -> None:
    store = InMemoryJobStore()
    first_job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(),
    )
    second_job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(match_count=5),
    )
    await store.mark_running(second_job.job_id)
    await store.mark_failed(
        first_job.job_id,
        error=JobError(message="Riot API rate limit exceeded.", stage="match_ids"),
    )

    running_jobs = await store.list_jobs(statuses={JobStatus.RUNNING})
    all_jobs = await store.list_jobs()

    assert [job.job_id for job in running_jobs] == [second_job.job_id]
    assert [job.job_id for job in all_jobs] == [second_job.job_id, first_job.job_id]


@pytest.mark.asyncio
async def test_store_records_job_events_and_current_wait() -> None:
    store = InMemoryJobStore()
    job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(),
    )
    resume_at = job.created_at
    event = JobEvent(
        event_type="rate_limit_wait",
        message="Waiting for Riot 429.",
        stage="match_detail",
        path="/lol/match/v5/matches/OC1_1",
        wait_seconds=17.0,
        resume_at=resume_at,
    )
    wait = JobWait(
        reason="riot_429",
        message="Waiting for Riot 429.",
        resume_at=resume_at,
        wait_seconds=17.0,
        stage="match_detail",
        path="/lol/match/v5/matches/OC1_1",
    )

    waiting_job = await store.record_event(job.job_id, event, current_wait=wait)

    assert waiting_job.current_wait == wait
    assert waiting_job.events == [event]

    resumed_job = await store.record_event(
        job.job_id,
        JobEvent(event_type="request_started", message="Riot request started."),
        clear_current_wait=True,
    )

    assert resumed_job.current_wait is None
    assert [stored_event.event_type for stored_event in resumed_job.events] == [
        "rate_limit_wait",
        "request_started",
    ]
