import asyncio
import logging
from collections.abc import Awaitable, Callable

from league_api.jobs.models import JobError, JobType, LadderIngestionParams, LadderIngestionResult
from league_api.jobs.store import InMemoryJobStore

logger = logging.getLogger(__name__)

LadderIngestionHandler = Callable[
    [LadderIngestionParams, InMemoryJobStore, str],
    Awaitable[LadderIngestionResult],
]


class InMemoryJobQueue:
    """Single-worker asyncio queue for process-local jobs."""

    def __init__(
        self,
        *,
        store: InMemoryJobStore,
        ladder_ingestion_handler: LadderIngestionHandler,
    ) -> None:
        self._store = store
        self._ladder_ingestion_handler = ladder_ingestion_handler
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._run(), name="league-api-job-worker")

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        await self._queue.put(None)
        await self._worker_task
        self._worker_task = None

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def _run(self) -> None:
        while True:
            job_id = await self._queue.get()
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
            if job.job_type == JobType.LADDER_INGESTION:
                result = await self._ladder_ingestion_handler(job.params, self._store, job_id)
            else:
                msg = f"Unsupported job type: {job.job_type}"
                raise ValueError(msg)
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
