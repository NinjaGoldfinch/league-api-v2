import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from itertools import count
from typing import Any

from league_api.jobs.models import (
    JobError,
    JobRecord,
    JobResult,
    JobStatus,
    JobType,
    LadderIngestionParams,
    LadderIngestionResult,
    ProfileFetchParams,
    ProfileFetchResult,
)
from league_api.jobs.store import JobStore
from league_api.redis.coordinator import InMemoryJobLockCoordinator, JobLockCoordinator

logger = logging.getLogger(__name__)

PROFILE_FETCH_PRIORITY = 0
PROFILE_MATCH_DETAILS_PRIORITY = 50
LADDER_INGESTION_PRIORITY = 200
DEFAULT_JOB_PRIORITY = LADDER_INGESTION_PRIORITY

LadderIngestionHandler = Callable[
    [LadderIngestionParams, Any, str],
    Awaitable[LadderIngestionResult],
]
ProfileFetchHandler = Callable[
    [ProfileFetchParams, Any, str],
    Awaitable[ProfileFetchResult],
]
QueuedJobItem = tuple[int, int, str | None]


class InMemoryJobQueue:
    """Single-worker asyncio queue for process-local jobs."""

    def __init__(
        self,
        *,
        store: JobStore,
        ladder_ingestion_handler: LadderIngestionHandler,
        profile_fetch_handler: ProfileFetchHandler,
        lock_coordinator: JobLockCoordinator | None = None,
    ) -> None:
        self._store = store
        self._ladder_ingestion_handler = ladder_ingestion_handler
        self._profile_fetch_handler = profile_fetch_handler
        self._lock_coordinator = lock_coordinator or InMemoryJobLockCoordinator()
        self._queue: asyncio.PriorityQueue[QueuedJobItem] = asyncio.PriorityQueue()
        self._sequence = count()
        self._worker_task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._stopping = False
            self._worker_task = asyncio.create_task(self._run(), name="league-api-job-worker")

    async def recover_queued_jobs(self) -> int:
        """Restore durable queued jobs after the process-local queue is recreated."""
        jobs = await self._store.list_jobs(statuses={JobStatus.QUEUED})
        jobs.sort(key=lambda job: (job.created_at, job.job_id))
        for job in jobs:
            await self.enqueue(job.job_id, priority=_priority_for_job(job))
        if jobs:
            logger.info("Recovered %s queued job(s) from the job store.", len(jobs))
        return len(jobs)

    async def stop(self) -> None:
        worker_task = self._worker_task
        if worker_task is None:
            return

        self._stopping = True
        worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await worker_task
        await self._fail_queued_jobs()
        self._worker_task = None

    async def enqueue(self, job_id: str, *, priority: int = DEFAULT_JOB_PRIORITY) -> None:
        if self._stopping:
            msg = "Cannot enqueue jobs while the worker is stopping."
            raise RuntimeError(msg)
        await self._queue.put((priority, next(self._sequence), job_id))

    async def _run(self) -> None:
        while True:
            _, _, job_id = await self._queue.get()
            try:
                if job_id is None:
                    return
                await self._process_job(job_id)
            except Exception:
                logger.exception("Unexpected in-memory job worker error.")
            finally:
                self._queue.task_done()

    async def _process_job(self, job_id: str) -> None:
        job = await self._store.get_job(job_id)
        if job is None:
            logger.warning("Skipping unknown job id %s.", job_id)
            return
        if job.status is not JobStatus.QUEUED:
            logger.info("Skipping job %s because its status is %s.", job_id, job.status)
            return

        lock_token = await self._lock_coordinator.acquire_job_lock(job_id)
        if lock_token is None:
            logger.info("Skipping job %s because another worker owns its lock.", job_id)
            return

        try:
            await self._store.mark_running(job_id)
            result: JobResult
            if job.job_type == JobType.LADDER_INGESTION:
                if not isinstance(job.params, LadderIngestionParams):
                    msg = "Ladder ingestion job has invalid params."
                    raise ValueError(msg)
                result = await self._ladder_ingestion_handler(job.params, self._store, job_id)
            elif job.job_type == JobType.PROFILE_FETCH:
                if not isinstance(job.params, ProfileFetchParams):
                    msg = "Profile fetch job has invalid params."
                    raise ValueError(msg)
                result = await self._profile_fetch_handler(job.params, self._store, job_id)
            else:
                msg = f"Unsupported job type: {job.job_type}"
                raise ValueError(msg)
            await self._store.mark_succeeded(job_id, result=result)
        except asyncio.CancelledError:
            await self._mark_cancelled(job_id)
            raise
        except Exception as exc:
            await self._store.mark_failed(
                job_id,
                error=JobError(
                    message=str(exc),
                    stage="job",
                    error_type=exc.__class__.__name__,
                ),
            )
            return
        finally:
            await self._lock_coordinator.release_job_lock(job_id, lock_token)

    async def _fail_queued_jobs(self) -> None:
        while True:
            try:
                _, _, job_id = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            try:
                if job_id is not None:
                    await self._mark_cancelled(job_id)
            finally:
                self._queue.task_done()

    async def _mark_cancelled(self, job_id: str) -> None:
        await self._store.mark_failed(
            job_id,
            error=JobError(
                message="Job cancelled because the application is shutting down.",
                stage="job",
                error_type="CancelledError",
            ),
        )


def _priority_for_job(job: JobRecord) -> int:
    if job.job_type is JobType.PROFILE_FETCH:
        params = job.params
        if isinstance(params, ProfileFetchParams) and params.match_ids is not None:
            return PROFILE_MATCH_DETAILS_PRIORITY
        return PROFILE_FETCH_PRIORITY
    return LADDER_INGESTION_PRIORITY
