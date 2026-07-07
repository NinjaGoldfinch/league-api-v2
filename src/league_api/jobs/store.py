import asyncio
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from league_api.jobs.models import (
    JobError,
    JobEvent,
    JobParams,
    JobProgress,
    JobRecord,
    JobResult,
    JobStatus,
    JobType,
    JobWait,
)


class JobStore(Protocol):
    async def create_job(
        self,
        *,
        job_type: JobType,
        params: JobParams,
    ) -> JobRecord: ...

    async def get_job(self, job_id: str) -> JobRecord | None: ...

    async def list_jobs(self, *, statuses: set[JobStatus] | None = None) -> list[JobRecord]: ...

    async def mark_running(self, job_id: str) -> JobRecord: ...

    async def update_progress(self, job_id: str, progress: JobProgress) -> JobRecord: ...

    async def mark_succeeded(
        self,
        job_id: str,
        *,
        result: JobResult,
    ) -> JobRecord: ...

    async def mark_failed(self, job_id: str, *, error: JobError) -> JobRecord: ...

    async def record_event(
        self,
        job_id: str,
        event: JobEvent,
        *,
        current_wait: JobWait | None = None,
        clear_current_wait: bool = False,
        max_events: int = 100,
    ) -> JobRecord: ...


class InMemoryJobStore:
    """Process-local job storage guarded by an async lock."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    async def create_job(
        self,
        *,
        job_type: JobType,
        params: JobParams,
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

    async def list_jobs(self, *, statuses: set[JobStatus] | None = None) -> list[JobRecord]:
        async with self._lock:
            jobs = list(self._jobs.values())
            if statuses is not None:
                jobs = [job for job in jobs if job.status in statuses]
            jobs.sort(key=lambda job: job.created_at, reverse=True)
            return [self._copy(job) for job in jobs]

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
        result: JobResult,
    ) -> JobRecord:
        async with self._lock:
            job = self._require_job(job_id)
            job.status = JobStatus.SUCCEEDED
            job.finished_at = datetime.now(UTC)
            job.progress = result.summary.model_copy(deep=True)
            job.result = result.model_copy(deep=True)
            job.error = None
            job.current_wait = None
            return self._copy(job)

    async def mark_failed(self, job_id: str, *, error: JobError) -> JobRecord:
        async with self._lock:
            job = self._require_job(job_id)
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now(UTC)
            job.error = error.model_copy(deep=True)
            job.current_wait = None
            return self._copy(job)

    async def record_event(
        self,
        job_id: str,
        event: JobEvent,
        *,
        current_wait: JobWait | None = None,
        clear_current_wait: bool = False,
        max_events: int = 100,
    ) -> JobRecord:
        async with self._lock:
            job = self._require_job(job_id)
            job.events.append(event.model_copy(deep=True))
            if len(job.events) > max_events:
                job.events = job.events[-max_events:]
            if current_wait is not None:
                job.current_wait = current_wait.model_copy(deep=True)
            elif clear_current_wait:
                job.current_wait = None
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
