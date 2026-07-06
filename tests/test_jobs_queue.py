import asyncio

import pytest

from league_api.jobs.models import (
    JobProgress,
    JobStatus,
    JobType,
    LadderIngestionParams,
    LadderIngestionResult,
    ProfileFetchParams,
    ProfileFetchResult,
)
from league_api.jobs.queue import (
    LADDER_INGESTION_PRIORITY,
    PROFILE_FETCH_PRIORITY,
    InMemoryJobQueue,
)
from league_api.jobs.store import InMemoryJobStore
from league_api.riot.rate_limiter import RiotRateLimit, RiotRateLimitManager


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

    queue = InMemoryJobQueue(
        store=store,
        ladder_ingestion_handler=handler,
        profile_fetch_handler=_unexpected_profile_handler,
    )
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
        assert isinstance(succeeded_job.result, LadderIngestionResult)
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

    queue = InMemoryJobQueue(
        store=store,
        ladder_ingestion_handler=handler,
        profile_fetch_handler=_unexpected_profile_handler,
    )
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


@pytest.mark.asyncio
async def test_queue_stop_cancels_job_waiting_for_rate_limit() -> None:
    store = InMemoryJobStore()
    sleep_started = asyncio.Event()
    sleep_cancelled = asyncio.Event()

    async def rate_limit_sleep(delay: float) -> None:
        sleep_started.set()
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            sleep_cancelled.set()
            raise

    limiter = RiotRateLimitManager(
        limits=[RiotRateLimit(request_count=100, window_seconds=120.0)],
        max_retries=1,
        retry_after_buffer_seconds=0.0,
        retry_after_fallback_seconds=120.0,
        sleep=rate_limit_sleep,
    )

    async def handler(
        params: LadderIngestionParams,
        store_arg: InMemoryJobStore,
        job_id: str,
    ) -> LadderIngestionResult:
        await limiter.pause_for_retry_after("60")
        return LadderIngestionResult(
            summary=JobProgress(players_discovered=1, players_processed=1),
            player_puuids=["puuid-1"],
            match_ids=[],
            matches={},
        )

    queue = InMemoryJobQueue(
        store=store,
        ladder_ingestion_handler=handler,
        profile_fetch_handler=_unexpected_profile_handler,
    )
    queue.start()
    job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(),
    )
    await queue.enqueue(job.job_id)

    await asyncio.wait_for(sleep_started.wait(), timeout=1)
    await asyncio.wait_for(queue.stop(), timeout=1)

    assert sleep_cancelled.is_set()
    failed_job = await store.get_job(job.job_id)
    assert failed_job is not None
    assert failed_job.status is JobStatus.FAILED
    assert failed_job.error is not None
    assert failed_job.error.error_type == "CancelledError"
    assert failed_job.error.message == "Job cancelled because the application is shutting down."


@pytest.mark.asyncio
async def test_queue_stop_marks_pending_jobs_failed() -> None:
    store = InMemoryJobStore()
    started = asyncio.Event()

    async def handler(
        params: LadderIngestionParams,
        store_arg: InMemoryJobStore,
        job_id: str,
    ) -> LadderIngestionResult:
        started.set()
        await asyncio.sleep(60)
        return LadderIngestionResult(
            summary=JobProgress(players_discovered=1, players_processed=1),
            player_puuids=["puuid-1"],
            match_ids=[],
            matches={},
        )

    queue = InMemoryJobQueue(
        store=store,
        ladder_ingestion_handler=handler,
        profile_fetch_handler=_unexpected_profile_handler,
    )
    queue.start()
    running_job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(),
    )
    pending_job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(),
    )
    await queue.enqueue(running_job.job_id)
    await queue.enqueue(pending_job.job_id)

    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.wait_for(queue.stop(), timeout=1)

    failed_running_job = await store.get_job(running_job.job_id)
    failed_pending_job = await store.get_job(pending_job.job_id)
    assert failed_running_job is not None
    assert failed_running_job.status is JobStatus.FAILED
    assert failed_pending_job is not None
    assert failed_pending_job.status is JobStatus.FAILED
    assert failed_pending_job.error is not None
    assert failed_pending_job.error.error_type == "CancelledError"


@pytest.mark.asyncio
async def test_priority_queue_runs_profile_jobs_before_ladder_jobs() -> None:
    store = InMemoryJobStore()
    processed: list[str] = []

    async def ladder_handler(
        params: LadderIngestionParams,
        store_arg: InMemoryJobStore,
        job_id: str,
    ) -> LadderIngestionResult:
        processed.append(f"ladder:{job_id}")
        return LadderIngestionResult(
            summary=JobProgress(players_discovered=1, players_processed=1),
            player_puuids=["puuid-1"],
            match_ids=[],
            matches={},
        )

    async def profile_handler(
        params: ProfileFetchParams,
        store_arg: InMemoryJobStore,
        job_id: str,
    ) -> ProfileFetchResult:
        processed.append(f"profile:{job_id}")
        return ProfileFetchResult(
            summary=JobProgress(players_discovered=1, players_processed=1),
            account={"puuid": "puuid-1"},
            summoner={"puuid": "puuid-1"},
            match_ids=[],
            matches={},
        )

    queue = InMemoryJobQueue(
        store=store,
        ladder_ingestion_handler=ladder_handler,
        profile_fetch_handler=profile_handler,
    )
    ladder_job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(),
    )
    profile_job = await store.create_job(
        job_type=JobType.PROFILE_FETCH,
        params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
    )
    await queue.enqueue(ladder_job.job_id, priority=LADDER_INGESTION_PRIORITY)
    await queue.enqueue(profile_job.job_id, priority=PROFILE_FETCH_PRIORITY)

    queue.start()
    try:
        await _wait_for_status(store, ladder_job.job_id, JobStatus.SUCCEEDED)
        await _wait_for_status(store, profile_job.job_id, JobStatus.SUCCEEDED)
    finally:
        await queue.stop()

    assert processed == [f"profile:{profile_job.job_id}", f"ladder:{ladder_job.job_id}"]


@pytest.mark.asyncio
async def test_priority_queue_preserves_fifo_order_inside_same_priority() -> None:
    store = InMemoryJobStore()
    processed: list[str] = []

    async def profile_handler(
        params: ProfileFetchParams,
        store_arg: InMemoryJobStore,
        job_id: str,
    ) -> ProfileFetchResult:
        processed.append(job_id)
        return ProfileFetchResult(
            summary=JobProgress(players_discovered=1, players_processed=1),
            account={"puuid": "puuid-1"},
            summoner={"puuid": "puuid-1"},
            match_ids=[],
            matches={},
        )

    queue = InMemoryJobQueue(
        store=store,
        ladder_ingestion_handler=_unexpected_ladder_handler,
        profile_fetch_handler=profile_handler,
    )
    first_job = await store.create_job(
        job_type=JobType.PROFILE_FETCH,
        params=ProfileFetchParams(game_name="First", tag_line="OCE"),
    )
    second_job = await store.create_job(
        job_type=JobType.PROFILE_FETCH,
        params=ProfileFetchParams(game_name="Second", tag_line="OCE"),
    )
    await queue.enqueue(first_job.job_id, priority=PROFILE_FETCH_PRIORITY)
    await queue.enqueue(second_job.job_id, priority=PROFILE_FETCH_PRIORITY)

    queue.start()
    try:
        await _wait_for_status(store, first_job.job_id, JobStatus.SUCCEEDED)
        await _wait_for_status(store, second_job.job_id, JobStatus.SUCCEEDED)
    finally:
        await queue.stop()

    assert processed == [first_job.job_id, second_job.job_id]


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


async def _unexpected_ladder_handler(
    params: LadderIngestionParams,
    store_arg: InMemoryJobStore,
    job_id: str,
) -> LadderIngestionResult:
    raise AssertionError("Ladder handler should not be called in this test.")


async def _unexpected_profile_handler(
    params: ProfileFetchParams,
    store_arg: InMemoryJobStore,
    job_id: str,
) -> ProfileFetchResult:
    raise AssertionError("Profile handler should not be called in this test.")
