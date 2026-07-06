import asyncio

import pytest

from league_api.jobs.models import (
    JobProgress,
    JobStatus,
    JobType,
    LadderIngestionParams,
    LadderIngestionResult,
)
from league_api.jobs.queue import InMemoryJobQueue
from league_api.jobs.store import InMemoryJobStore


@pytest.mark.asyncio
async def test_queue_processes_job_asynchronously() -> None:
    store = InMemoryJobStore()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def handler(
        params: LadderIngestionParams,
        store_arg: InMemoryJobStore,
        job_id: str,
    ) -> LadderIngestionResult:
        assert params == LadderIngestionParams()
        assert store_arg is store
        assert job_id
        started.set()
        await finish.wait()
        return LadderIngestionResult(
            summary=JobProgress(players_discovered=1, players_processed=1),
            player_puuids=["puuid-1"],
            match_ids=[],
            matches={},
        )

    queue = InMemoryJobQueue(store=store, ladder_ingestion_handler=handler)
    queue.start()
    try:
        job = await store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(),
        )
        await queue.enqueue(job.job_id)

        await asyncio.wait_for(started.wait(), timeout=1)
        running_job = await store.get_job(job.job_id)
        assert running_job is not None
        assert running_job.status is JobStatus.RUNNING

        finish.set()
        await _wait_for_status(store, job.job_id, JobStatus.SUCCEEDED)
        succeeded_job = await store.get_job(job.job_id)
        assert succeeded_job is not None
        assert succeeded_job.result is not None
        assert succeeded_job.result.player_puuids == ["puuid-1"]
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_queue_marks_failed_job_and_continues_processing() -> None:
    store = InMemoryJobStore()
    calls = 0

    async def handler(
        params: LadderIngestionParams,
        store_arg: InMemoryJobStore,
        job_id: str,
    ) -> LadderIngestionResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "Riot API request failed with status 500."
            raise RuntimeError(msg)
        return LadderIngestionResult(
            summary=JobProgress(players_discovered=1, players_processed=1),
            player_puuids=["puuid-2"],
            match_ids=[],
            matches={},
        )

    queue = InMemoryJobQueue(store=store, ladder_ingestion_handler=handler)
    queue.start()
    try:
        failed_job = await store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(),
        )
        succeeded_job = await store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(),
        )

        await queue.enqueue(failed_job.job_id)
        await queue.enqueue(succeeded_job.job_id)

        await _wait_for_status(store, failed_job.job_id, JobStatus.FAILED)
        await _wait_for_status(store, succeeded_job.job_id, JobStatus.SUCCEEDED)

        failed_record = await store.get_job(failed_job.job_id)
        assert failed_record is not None
        assert failed_record.error is not None
        assert failed_record.error.message == "Riot API request failed with status 500."
    finally:
        await queue.stop()


async def _wait_for_status(
    store: InMemoryJobStore,
    job_id: str,
    expected_status: JobStatus,
) -> None:
    for _ in range(50):
        job = await store.get_job(job_id)
        if job is not None and job.status is expected_status:
            return
        await asyncio.sleep(0.01)
    msg = f"Job {job_id} did not reach {expected_status}."
    raise AssertionError(msg)
