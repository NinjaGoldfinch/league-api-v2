from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from league_api.api.routes.jobs import router as jobs_router
from league_api.api.routes.league_v4 import router as league_v4_router
from league_api.api.routes.match_v5 import router as match_v5_router
from league_api.core.config import get_settings
from league_api.jobs.ingestion import run_ladder_ingestion
from league_api.jobs.queue import InMemoryJobQueue
from league_api.jobs.store import InMemoryJobStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create process-local job infrastructure for the app lifetime."""
    job_store = InMemoryJobStore()
    job_queue = InMemoryJobQueue(
        store=job_store,
        ladder_ingestion_handler=run_ladder_ingestion,
    )
    app.state.job_store = job_store
    app.state.job_queue = job_queue
    job_queue.start()
    try:
        yield
    finally:
        await job_queue.stop()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(jobs_router)
    app.include_router(match_v5_router)
    app.include_router(league_v4_router)
    return app


app = create_app()
