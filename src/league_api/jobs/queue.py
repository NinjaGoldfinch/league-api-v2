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
    LadderPlayersParams,
    LadderPlayersResult,
    ProfileFetchParams,
    ProfileFetchResult,
)
from league_api.jobs.store import JobStore
from league_api.redis.coordinator import InMemoryJobLockCoordinator, JobLockCoordinator

logger = logging.getLogger(__name__)

PROFILE_FETCH_PRIORITY = 0
PROFILE_MATCH_DETAILS_PRIORITY = 50
LADDER_INGESTION_PRIORITY = 200
LADDER_PLAYERS_PRIORITY = 100
DEFAULT_JOB_PRIORITY = LADDER_INGESTION_PRIORITY

LadderIngestionHandler = Callable[
    [LadderIngestionParams, Any, str],
    Awaitable[LadderIngestionResult],
]
ProfileFetchHandler = Callable[
    [ProfileFetchParams, Any, str],
    Awaitable[ProfileFetchResult],
]
LadderPlayersHandler = Callable[[LadderPlayersParams, Any, str], Awaitable[LadderPlayersResult]]
QueuedJobItem = tuple[int, int, str | None]


class InMemoryJobQueue:
    """Single-worker asyncio queue for process-local jobs."""

    def __init__(
        self,
        *,
        store: JobStore,
        ladder_ingestion_handler: LadderIngestionHandler,
        profile_fetch_handler: ProfileFetchHandler,
        ladder_players_handler: LadderPlayersHandler | None = None,
        lock_coordinator: JobLockCoordinator | None = None,
    ) -> None:
        self._store = store
        self._ladder_ingestion_handler = ladder_ingestion_handler
        self._profile_fetch_handler = profile_fetch_handler
        self._ladder_players_handler = ladder_players_handler
        self._lock_coordinator = lock_coordinator or InMemoryJobLockCoordinator()
        self._queue: asyncio.PriorityQueue[QueuedJobItem] = asyncio.PriorityQueue()
        self._manual_queue: asyncio.PriorityQueue[QueuedJobItem] = asyncio.PriorityQueue()
        self._sequence = count()
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._retry_tasks: set[asyncio.Task[None]] = set()
        self._enqueued_job_ids: set[str] = set()
        self._enqueue_lock = asyncio.Lock()
        self._stopping = False

    def start(self) -> None:
        if not self._worker_tasks or all(task.done() for task in self._worker_tasks):
            self._stopping = False
            self._worker_tasks = [
                asyncio.create_task(
                    self._run(self._manual_queue), name="league-api-manual-job-worker"
                ),
                asyncio.create_task(self._run(self._queue), name="league-api-automatic-job-worker"),
            ]

    async def recover_queued_jobs(self) -> int:
        """Restore durable queued jobs after the process-local queue is recreated."""
        jobs = await self._store.list_jobs(statuses={JobStatus.QUEUED, JobStatus.RUNNING})
        jobs.sort(key=lambda job: (job.created_at, job.job_id))
        for job in jobs:
            await self.enqueue(job.job_id, priority=_priority_for_job(job))
        if jobs:
            logger.info("Recovered %s queued job(s) from the job store.", len(jobs))
        return len(jobs)

    async def stop(self) -> None:
        worker_tasks = self._worker_tasks
        if not worker_tasks:
            return

        self._stopping = True
        for worker_task in worker_tasks:
            worker_task.cancel()
        for worker_task in worker_tasks:
            with suppress(asyncio.CancelledError):
                await worker_task
        retry_tasks = list(self._retry_tasks)
        for retry_task in retry_tasks:
            retry_task.cancel()
        for retry_task in retry_tasks:
            with suppress(asyncio.CancelledError):
                await retry_task
        self._retry_tasks.clear()
        await self._fail_queued_jobs(self._manual_queue)
        await self._fail_queued_jobs(self._queue)
        self._worker_tasks = []

    async def enqueue(self, job_id: str, *, priority: int = DEFAULT_JOB_PRIORITY) -> None:
        if self._stopping:
            msg = "Cannot enqueue jobs while the worker is stopping."
            raise RuntimeError(msg)
        async with self._enqueue_lock:
            if job_id in self._enqueued_job_ids:
                return
            self._enqueued_job_ids.add(job_id)
            queue = self._manual_queue if priority < LADDER_PLAYERS_PRIORITY else self._queue
            await queue.put((priority, next(self._sequence), job_id))

    async def _run(self, queue: asyncio.PriorityQueue[QueuedJobItem]) -> None:
        while True:
            priority, _, job_id = await queue.get()
            retry = False
            try:
                if job_id is None:
                    return
                retry = await self._process_job(job_id)
            except Exception:
                logger.exception("Unexpected in-memory job worker error.")
            finally:
                if job_id is not None:
                    async with self._enqueue_lock:
                        self._enqueued_job_ids.discard(job_id)
                queue.task_done()
            if retry and job_id is not None and not self._stopping:
                retry_task = asyncio.create_task(
                    self._retry_enqueue(job_id, priority),
                    name=f"league-api-job-lock-retry:{job_id}",
                )
                self._retry_tasks.add(retry_task)
                retry_task.add_done_callback(self._retry_tasks.discard)

    async def _retry_enqueue(self, job_id: str, priority: int) -> None:
        await asyncio.sleep(5)
        if not self._stopping:
            await self.enqueue(job_id, priority=priority)

    async def _process_job(self, job_id: str) -> bool:
        job = await self._store.get_job(job_id)
        if job is None:
            logger.warning("Skipping unknown job id %s.", job_id)
            return False
        if job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
            logger.info("Skipping job %s because its status is %s.", job_id, job.status)
            return False

        lock_token = await self._lock_coordinator.acquire_job_lock(job_id, ttl_seconds=86_400)
        if lock_token is None:
            logger.info("Retrying job %s later because another worker owns its lock.", job_id)
            return True

        try:
            if job.status is JobStatus.QUEUED:
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
            elif job.job_type == JobType.LADDER_PLAYERS:
                if (
                    not isinstance(job.params, LadderPlayersParams)
                    or self._ladder_players_handler is None
                ):
                    msg = "Ladder players job has invalid params or no handler."
                    raise ValueError(msg)
                result = await self._ladder_players_handler(job.params, self._store, job_id)
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
        finally:
            await self._lock_coordinator.release_job_lock(job_id, lock_token)
        return False

    async def _fail_queued_jobs(self, queue: asyncio.PriorityQueue[QueuedJobItem]) -> None:
        while True:
            try:
                _, _, job_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            try:
                if job_id is not None:
                    await self._mark_cancelled(job_id)
            finally:
                if job_id is not None:
                    async with self._enqueue_lock:
                        self._enqueued_job_ids.discard(job_id)
                queue.task_done()

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
    if job.job_type is JobType.LADDER_PLAYERS:
        return LADDER_PLAYERS_PRIORITY
    return LADDER_INGESTION_PRIORITY
