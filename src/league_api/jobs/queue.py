import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from itertools import count

from league_api.jobs.models import (
    JobError,
    JobResult,
    JobType,
    LadderIngestionParams,
    LadderIngestionResult,
    ProfileFetchParams,
    ProfileFetchResult,
)
from league_api.jobs.store import InMemoryJobStore

logger = logging.getLogger(__name__)

PROFILE_FETCH_PRIORITY = 0
PROFILE_MATCH_DETAILS_PRIORITY = 50
LADDER_INGESTION_PRIORITY = 200
DEFAULT_JOB_PRIORITY = LADDER_INGESTION_PRIORITY

LadderIngestionHandler = Callable[
    [LadderIngestionParams, InMemoryJobStore, str],
    Awaitable[LadderIngestionResult],
]
ProfileFetchHandler = Callable[
    [ProfileFetchParams, InMemoryJobStore, str],
    Awaitable[ProfileFetchResult],
]
QueuedJobItem = tuple[int, int, str | None]


class InMemoryJobQueue:
    """Single-worker asyncio queue for process-local jobs."""

    def __init__(
        self,
        *,
        store: InMemoryJobStore,
        ladder_ingestion_handler: LadderIngestionHandler,
        profile_fetch_handler: ProfileFetchHandler,
    ) -> None:
        self._store = store
        self._ladder_ingestion_handler = ladder_ingestion_handler
        self._profile_fetch_handler = profile_fetch_handler
        self._queue: asyncio.PriorityQueue[QueuedJobItem] = asyncio.PriorityQueue()
        self._sequence = count()
        self._worker_task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._stopping = False
            self._worker_task = asyncio.create_task(self._run(), name="league-api-job-worker")

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

        await self._store.mark_running(job_id)
        try:
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

        await self._store.mark_succeeded(job_id, result=result)

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
