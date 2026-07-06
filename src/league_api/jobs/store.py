import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from league_api.jobs.models import (
    JobError,
    JobProgress,
    JobRecord,
    JobStatus,
    JobType,
    LadderIngestionParams,
    LadderIngestionResult,
)


class InMemoryJobStore:
    """Process-local job storage guarded by an async lock."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    async def create_job(
        self,
        *,
        job_type: JobType,
        params: LadderIngestionParams,
    ) -> JobRecord:
        now = datetime.now(UTC)
        job = JobRecord(
            job_id=str(uuid4()),
            job_type=job_type,
            status=JobStatus.QUEUED,
            created_at=now,
            params=params,
        )
        async with self._lock:
            self._jobs[job.job_id] = job
        return self._copy(job)

    async def get_job(self, job_id: str) -> JobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return self._copy(job)

    async def mark_running(self, job_id: str) -> JobRecord:
        async with self._lock:
            job = self._require_job(job_id)
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(UTC)
            job.error = None
            return self._copy(job)

    async def update_progress(self, job_id: str, progress: JobProgress) -> JobRecord:
        async with self._lock:
            job = self._require_job(job_id)
            job.progress = progress.model_copy(deep=True)
            return self._copy(job)

    async def mark_succeeded(
        self,
        job_id: str,
        *,
        result: LadderIngestionResult,
    ) -> JobRecord:
        async with self._lock:
            job = self._require_job(job_id)
            job.status = JobStatus.SUCCEEDED
            job.finished_at = datetime.now(UTC)
            job.progress = result.summary.model_copy(deep=True)
            job.result = result.model_copy(deep=True)
            job.error = None
            return self._copy(job)

    async def mark_failed(self, job_id: str, *, error: JobError) -> JobRecord:
        async with self._lock:
            job = self._require_job(job_id)
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now(UTC)
            job.error = error.model_copy(deep=True)
            return self._copy(job)

    def _require_job(self, job_id: str) -> JobRecord:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            msg = f"Unknown job id: {job_id}"
            raise KeyError(msg) from exc

    @staticmethod
    def _copy(job: JobRecord) -> JobRecord:
        return job.model_copy(deep=True)
