from datetime import UTC, datetime
from typing import Any, cast
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
    LadderIngestionParams,
    LadderIngestionResult,
    ProfileFetchParams,
    ProfileFetchResult,
)


class PostgresJobStore:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def create_job(
        self,
        *,
        job_type: JobType,
        params: JobParams,
    ) -> JobRecord:
        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.sql import bindparam

        now = datetime.now(UTC)
        job_id = str(uuid4())
        query = text(
            """
            insert into jobs (
                job_id, job_type, status, created_at, params, progress
            )
            values (
                :job_id, :job_type, :status, :created_at, :params, :progress
            )
            """
        ).bindparams(
            bindparam("params", type_=JSONB),
            bindparam("progress", type_=JSONB),
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                query,
                {
                    "job_id": job_id,
                    "job_type": job_type.value,
                    "status": JobStatus.QUEUED.value,
                    "created_at": now,
                    "params": params.model_dump(mode="json"),
                    "progress": JobProgress().model_dump(mode="json"),
                },
            )
        job = await self.get_job(job_id)
        if job is None:
            msg = f"Created job was not found: {job_id}"
            raise RuntimeError(msg)
        return job

    async def get_job(self, job_id: str) -> JobRecord | None:
        from sqlalchemy import text

        query = text(
            """
            select job_id, job_type, status, created_at, started_at, finished_at, params,
                   progress, result, error, current_wait
            from jobs
            where job_id = :job_id
            """
        )
        events_query = text(
            """
            select event_payload
            from job_events
            where job_id = :job_id
            order by event_id asc
            """
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(query, {"job_id": job_id})).mappings().first()
            if row is None:
                return None
            event_rows = (await conn.execute(events_query, {"job_id": job_id})).mappings().all()
        return _row_to_job(row, [event_row["event_payload"] for event_row in event_rows])

    async def list_jobs(self, *, statuses: set[JobStatus] | None = None) -> list[JobRecord]:
        from sqlalchemy import String, text
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy.sql import bindparam

        if statuses is None:
            query = text("select job_id from jobs order by created_at desc")
            params: dict[str, Any] = {}
        else:
            query = text(
                """
                select job_id
                from jobs
                where status = any(:statuses)
                order by created_at desc
                """
            ).bindparams(bindparam("statuses", type_=ARRAY(String)))
            params = {"statuses": [status.value for status in statuses]}
        async with self._engine.begin() as conn:
            rows = (await conn.execute(query, params)).mappings().all()
        jobs: list[JobRecord] = []
        for row in rows:
            job = await self.get_job(cast(str, row["job_id"]))
            if job is not None:
                jobs.append(job)
        return jobs

    async def mark_running(self, job_id: str) -> JobRecord:
        from sqlalchemy import text

        query = text(
            """
            update jobs
            set status = :status, started_at = coalesce(started_at, :started_at), error = null
            where job_id = :job_id
            """
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                query,
                {
                    "job_id": job_id,
                    "status": JobStatus.RUNNING.value,
                    "started_at": datetime.now(UTC),
                },
            )
        return await self._require_job(job_id)

    async def update_progress(self, job_id: str, progress: JobProgress) -> JobRecord:
        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.sql import bindparam

        query = text("update jobs set progress = :progress where job_id = :job_id").bindparams(
            bindparam("progress", type_=JSONB)
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                query,
                {"job_id": job_id, "progress": progress.model_dump(mode="json")},
            )
        return await self._require_job(job_id)

    async def mark_succeeded(
        self,
        job_id: str,
        *,
        result: JobResult,
    ) -> JobRecord:
        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.sql import bindparam

        query = text(
            """
            update jobs
            set status = :status, finished_at = :finished_at, progress = :progress,
                result = :result, error = null, current_wait = null
            where job_id = :job_id
            """
        ).bindparams(
            bindparam("progress", type_=JSONB),
            bindparam("result", type_=JSONB),
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                query,
                {
                    "job_id": job_id,
                    "status": JobStatus.SUCCEEDED.value,
                    "finished_at": datetime.now(UTC),
                    "progress": result.summary.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                },
            )
        return await self._require_job(job_id)

    async def mark_failed(self, job_id: str, *, error: JobError) -> JobRecord:
        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.sql import bindparam

        query = text(
            """
            update jobs
            set status = :status, finished_at = :finished_at, error = :error, current_wait = null
            where job_id = :job_id
            """
        ).bindparams(bindparam("error", type_=JSONB))
        async with self._engine.begin() as conn:
            await conn.execute(
                query,
                {
                    "job_id": job_id,
                    "status": JobStatus.FAILED.value,
                    "finished_at": datetime.now(UTC),
                    "error": error.model_dump(mode="json"),
                },
            )
        return await self._require_job(job_id)

    async def record_event(
        self,
        job_id: str,
        event: JobEvent,
        *,
        current_wait: JobWait | None = None,
        clear_current_wait: bool = False,
        max_events: int = 100,
    ) -> JobRecord:
        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.sql import bindparam

        insert_event = text(
            """
            insert into job_events (job_id, event_payload)
            values (:job_id, :event_payload)
            """
        ).bindparams(bindparam("event_payload", type_=JSONB))
        update_wait = text(
            """
            update jobs
            set current_wait = :current_wait
            where job_id = :job_id
            """
        ).bindparams(bindparam("current_wait", type_=JSONB))
        trim_events = text(
            """
            delete from job_events
            where job_id = :job_id
              and event_id not in (
                select event_id
                from job_events
                where job_id = :job_id
                order by event_id desc
                limit :max_events
              )
            """
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                insert_event,
                {"job_id": job_id, "event_payload": event.model_dump(mode="json")},
            )
            if current_wait is not None or clear_current_wait:
                await conn.execute(
                    update_wait,
                    {
                        "job_id": job_id,
                        "current_wait": (
                            current_wait.model_dump(mode="json")
                            if current_wait is not None
                            else None
                        ),
                    },
                )
            await conn.execute(trim_events, {"job_id": job_id, "max_events": max_events})
        return await self._require_job(job_id)

    async def _require_job(self, job_id: str) -> JobRecord:
        job = await self.get_job(job_id)
        if job is None:
            msg = f"Unknown job id: {job_id}"
            raise KeyError(msg)
        return job


def _row_to_job(row: Any, events: list[dict[str, Any]]) -> JobRecord:
    job_type = JobType(cast(str, row["job_type"]))
    params = _params_for_type(job_type, cast(dict[str, Any], row["params"]))
    result_payload = row["result"]
    return JobRecord(
        job_id=cast(str, row["job_id"]),
        job_type=job_type,
        status=JobStatus(cast(str, row["status"])),
        created_at=_aware(cast(datetime, row["created_at"])),
        started_at=_optional_aware(row["started_at"]),
        finished_at=_optional_aware(row["finished_at"]),
        progress=JobProgress.model_validate(row["progress"] or {}),
        params=params,
        result=(
            _result_for_type(job_type, cast(dict[str, Any], result_payload))
            if result_payload is not None
            else None
        ),
        error=(JobError.model_validate(row["error"]) if row["error"] is not None else None),
        current_wait=(
            JobWait.model_validate(row["current_wait"]) if row["current_wait"] is not None else None
        ),
        events=[JobEvent.model_validate(event) for event in events],
    )


def _params_for_type(job_type: JobType, payload: dict[str, Any]) -> JobParams:
    if job_type is JobType.LADDER_INGESTION:
        return LadderIngestionParams.model_validate(payload)
    if job_type is JobType.PROFILE_FETCH:
        return ProfileFetchParams.model_validate(payload)
    msg = f"Unsupported job type: {job_type}"
    raise ValueError(msg)


def _result_for_type(job_type: JobType, payload: dict[str, Any]) -> JobResult:
    if job_type is JobType.LADDER_INGESTION:
        return LadderIngestionResult.model_validate(payload)
    if job_type is JobType.PROFILE_FETCH:
        return ProfileFetchResult.model_validate(payload)
    msg = f"Unsupported job type: {job_type}"
    raise ValueError(msg)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _optional_aware(value: Any) -> datetime | None:
    if value is None:
        return None
    return _aware(cast(datetime, value))
