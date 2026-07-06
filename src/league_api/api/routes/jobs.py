from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse

from league_api.jobs.models import (
    JobProgress,
    JobRecord,
    JobStatus,
    JobType,
    LadderIngestionParams,
    LadderIngestionResult,
    LadderType,
)
from league_api.jobs.queue import InMemoryJobQueue
from league_api.jobs.store import InMemoryJobStore
from league_api.riot.routing import RiotPlatformRoute, RiotRegionalRoute

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: JobType
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: JobProgress


class JobSucceededResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus
    summary: JobProgress
    player_puuids: list[str]
    match_ids: list[str]
    matches: dict[str, dict[str, Any]]
    errors: list[Any]


def get_job_store(request: Request) -> InMemoryJobStore:
    return cast(InMemoryJobStore, request.app.state.job_store)


def get_job_queue(request: Request) -> InMemoryJobQueue:
    return cast(InMemoryJobQueue, request.app.state.job_queue)


@router.post(
    "/ingestion/ladder",
    response_model=JobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a ladder ingestion job",
)
async def start_ladder_ingestion_job(
    store: Annotated[InMemoryJobStore, Depends(get_job_store)],
    job_queue: Annotated[InMemoryJobQueue, Depends(get_job_queue)],
    platform_route: Annotated[
        RiotPlatformRoute,
        Query(description="Riot platform route for League-V4 ladder data."),
    ] = RiotPlatformRoute.OC1,
    regional_route: Annotated[
        RiotRegionalRoute,
        Query(description="Riot regional route for Match-V5 match data."),
    ] = RiotRegionalRoute.SEA,
    queue: Annotated[str, Query(min_length=1, description="Ranked queue.")] = "RANKED_SOLO_5x5",
    ladder: Annotated[
        LadderType,
        Query(description="Ladder source to ingest. Only challenger is supported in this stage."),
    ] = LadderType.CHALLENGER,
    match_count: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JobStatusResponse:
    params = LadderIngestionParams(
        platform_route=platform_route,
        regional_route=regional_route,
        queue=queue,
        ladder=ladder,
        match_count=match_count,
    )
    job = await store.create_job(job_type=JobType.LADDER_INGESTION, params=params)
    await job_queue.enqueue(job.job_id)
    return _status_response(job)


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status",
)
async def get_job(
    job_id: str,
    store: Annotated[InMemoryJobStore, Depends(get_job_store)],
) -> JobStatusResponse:
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return _status_response(job)


@router.get(
    "/{job_id}/result",
    response_model=None,
    summary="Get job result",
)
async def get_job_result(
    job_id: str,
    store: Annotated[InMemoryJobStore, Depends(get_job_store)],
) -> dict[str, Any] | JSONResponse:
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": job.job_id,
                "status": job.status,
                "message": "Job is still queued or running.",
                "progress": job.progress.model_dump(mode="json"),
            },
        )

    if job.status is JobStatus.FAILED:
        return job.model_dump(mode="json")

    if job.result is None:
        msg = "Completed job is missing a result."
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)

    return _success_result(job.job_id, job.result).model_dump(mode="json")


def _status_response(job: JobRecord) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        progress=job.progress,
    )


def _success_result(job_id: str, result: LadderIngestionResult) -> JobSucceededResultResponse:
    return JobSucceededResultResponse(
        job_id=job_id,
        status=JobStatus.SUCCEEDED,
        summary=result.summary,
        player_puuids=result.player_puuids,
        match_ids=result.match_ids,
        matches=result.matches,
        errors=result.errors,
    )
