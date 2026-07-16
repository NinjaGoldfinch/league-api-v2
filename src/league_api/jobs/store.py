import asyncio
from dataclasses import dataclass
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
    ProfileFetchParams,
)


@dataclass(frozen=True, slots=True)
class JobListCursor:
    created_at: datetime
    job_id: str


@dataclass(frozen=True, slots=True)
class JobListPage:
    jobs: list[JobRecord]
    next_cursor: JobListCursor | None
    has_more: bool


class JobStore(Protocol):
    async def create_job(
        self,
        *,
        job_type: JobType,
        params: JobParams,
    ) -> JobRecord: ...

    async def create_or_get_active_profile_job(
        self, *, params: ProfileFetchParams
    ) -> tuple[JobRecord, bool]: ...

    async def get_job(self, job_id: str) -> JobRecord | None: ...

    async def list_jobs(self, *, statuses: set[JobStatus] | None = None) -> list[JobRecord]: ...

    async def list_jobs_page(
        self,
        *,
        statuses: set[JobStatus] | None = None,
        job_type: JobType | None = None,
        riot_id: str | None = None,
        limit: int = 50,
        cursor: JobListCursor | None = None,
        include_events: bool = False,
        include_result: bool = False,
    ) -> JobListPage: ...

    async def mark_running(self, job_id: str) -> JobRecord: ...

    async def update_progress(self, job_id: str, progress: JobProgress) -> JobRecord: ...

    async def update_result(
        self,
        job_id: str,
        *,
        result: JobResult,
    ) -> JobRecord: ...

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

    async def create_or_get_active_profile_job(
        self, *, params: ProfileFetchParams
    ) -> tuple[JobRecord, bool]:
        async with self._lock:
            for existing in self._jobs.values():
                if (
                    existing.status in {JobStatus.QUEUED, JobStatus.RUNNING}
                    and isinstance(existing.params, ProfileFetchParams)
                    and _profile_work_key(existing.params) == _profile_work_key(params)
                ):
                    return self._copy(existing), False
            now = datetime.now(UTC)
            job = JobRecord(
                job_id=str(uuid4()),
                job_type=JobType.PROFILE_FETCH,
                status=JobStatus.QUEUED,
                created_at=now,
                params=params,
            )
            self._jobs[job.job_id] = job
            return self._copy(job), True

    async def get_job(self, job_id: str) -> JobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return self._copy(job)

    async def list_jobs(self, *, statuses: set[JobStatus] | None = None) -> list[JobRecord]:
        page = await self.list_jobs_page(
            statuses=statuses,
            limit=max(len(self._jobs), 1),
            include_events=True,
            include_result=True,
        )
        return page.jobs

    async def list_jobs_page(
        self,
        *,
        statuses: set[JobStatus] | None = None,
        job_type: JobType | None = None,
        riot_id: str | None = None,
        limit: int = 50,
        cursor: JobListCursor | None = None,
        include_events: bool = False,
        include_result: bool = False,
    ) -> JobListPage:
        async with self._lock:
            jobs = list(self._jobs.values())
            if statuses is not None:
                jobs = [job for job in jobs if job.status in statuses]
            if job_type is not None:
                jobs = [job for job in jobs if job.job_type is job_type]
            if riot_id is not None:
                normalized_riot_id = _normalize_riot_id(riot_id)
                jobs = [
                    job
                    for job in jobs
                    if _job_riot_id(job) is not None
                    and _normalize_riot_id(_job_riot_id(job) or "") == normalized_riot_id
                ]
            jobs.sort(key=lambda job: (job.created_at, job.job_id), reverse=True)
            if cursor is not None:
                jobs = [
                    job
                    for job in jobs
                    if (job.created_at, job.job_id) < (cursor.created_at, cursor.job_id)
                ]
            limited_jobs = jobs[: limit + 1]
            has_more = len(limited_jobs) > limit
            page_jobs = limited_jobs[:limit]
            next_cursor = (
                JobListCursor(created_at=page_jobs[-1].created_at, job_id=page_jobs[-1].job_id)
                if has_more and page_jobs
                else None
            )
            copied_jobs = [self._copy(job) for job in page_jobs]
            if not include_events or not include_result:
                copied_jobs = [
                    _trim_job(job, include_events=include_events, include_result=include_result)
                    for job in copied_jobs
                ]
            return JobListPage(jobs=copied_jobs, next_cursor=next_cursor, has_more=has_more)

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

    async def update_result(
        self,
        job_id: str,
        *,
        result: JobResult,
    ) -> JobRecord:
        async with self._lock:
            job = self._require_job(job_id)
            job.progress = result.summary.model_copy(deep=True)
            job.result = result.model_copy(deep=True)
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


def _trim_job(
    job: JobRecord,
    *,
    include_events: bool,
    include_result: bool,
) -> JobRecord:
    trimmed = job.model_copy(deep=True)
    if not include_events:
        trimmed.events = []
    if not include_result:
        trimmed.result = None
    return trimmed


def _job_riot_id(job: JobRecord) -> str | None:
    if not isinstance(job.params, ProfileFetchParams):
        return None
    return f"{job.params.game_name}#{job.params.tag_line}"


def _normalize_riot_id(riot_id: str) -> str:
    game_name, separator, tag_line = riot_id.partition("#")
    if not separator:
        return riot_id.strip().casefold()
    return f"{game_name.strip().casefold()}#{tag_line.strip().casefold()}"


def _profile_work_key(params: ProfileFetchParams) -> str:
    return "|".join(
        (
            params.game_name.strip().casefold(),
            params.tag_line.strip().casefold(),
            params.account_regional_route.value,
            params.platform_route.value,
            params.regional_route.value,
            str(params.match_count),
        )
    )
