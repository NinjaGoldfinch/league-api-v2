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
from league_api.jobs.store import JobListCursor, JobListPage


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
        page = await self.list_jobs_page(
            statuses=statuses,
            limit=100_000,
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
        from sqlalchemy import String, text
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy.sql import bindparam

        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit + 1}
        bindparams: list[Any] = []
        if statuses is not None:
            clauses.append("status = any(:statuses)")
            params["statuses"] = [status.value for status in statuses]
            bindparams.append(bindparam("statuses", type_=ARRAY(String)))
        if job_type is not None:
            clauses.append("job_type = :job_type")
            params["job_type"] = job_type.value
        if riot_id is not None:
            game_name, _, tag_line = riot_id.partition("#")
            clauses.extend(
                [
                    "job_type = :profile_job_type",
                    "lower(params ->> 'game_name') = :game_name",
                    "lower(params ->> 'tag_line') = :tag_line",
                ]
            )
            params["profile_job_type"] = JobType.PROFILE_FETCH.value
            params["game_name"] = game_name.strip().casefold()
            params["tag_line"] = tag_line.strip().casefold()
        if cursor is not None:
            clauses.append(
                "(created_at < :cursor_created_at "
                "or (created_at = :cursor_created_at and job_id < :cursor_job_id))"
            )
            params["cursor_created_at"] = cursor.created_at
            params["cursor_job_id"] = cursor.job_id

        where_sql = f"where {' and '.join(clauses)}" if clauses else ""
        result_sql = "result" if include_result else "null as result"
        query = text(
            f"""
            select job_id, job_type, status, created_at, started_at, finished_at, params,
                   progress, {result_sql}, error, current_wait
            from jobs
            {where_sql}
            order by created_at desc, job_id desc
            limit :limit
            """
        ).bindparams(*bindparams)

        async with self._engine.begin() as conn:
            rows = (await conn.execute(query, params)).mappings().all()
            limited_rows = rows[:limit]
            events_by_job_id: dict[str, list[dict[str, Any]]] = {}
            if include_events and limited_rows:
                event_query = text(
                    """
                    select job_id, event_payload
                    from job_events
                    where job_id = any(:job_ids)
                    order by job_id asc, event_id asc
                    """
                ).bindparams(bindparam("job_ids", type_=ARRAY(String)))
                event_rows = (
                    (
                        await conn.execute(
                            event_query,
                            {"job_ids": [cast(str, row["job_id"]) for row in limited_rows]},
                        )
                    )
                    .mappings()
                    .all()
                )
                for event_row in event_rows:
                    events_by_job_id.setdefault(cast(str, event_row["job_id"]), []).append(
                        cast(dict[str, Any], event_row["event_payload"])
                    )

        has_more = len(rows) > limit
        jobs = [
            _row_to_job(row, events_by_job_id.get(cast(str, row["job_id"]), []))
            for row in limited_rows
        ]
        next_cursor = (
            JobListCursor(
                created_at=_aware(cast(datetime, limited_rows[-1]["created_at"])),
                job_id=cast(str, limited_rows[-1]["job_id"]),
            )
            if has_more and limited_rows
            else None
        )
        return JobListPage(jobs=jobs, next_cursor=next_cursor, has_more=has_more)

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

    async def update_result(
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
            set progress = :progress, result = :result
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
                    "progress": result.summary.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                },
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
