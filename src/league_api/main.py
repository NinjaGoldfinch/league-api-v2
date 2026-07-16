from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from league_api.api.routes.account_v1 import router as account_v1_router
from league_api.api.routes.jobs import router as jobs_router
from league_api.api.routes.ladders import router as ladders_router
from league_api.api.routes.league_v4 import router as league_v4_router
from league_api.api.routes.manager import router as manager_router
from league_api.api.routes.match_v5 import router as match_v5_router
from league_api.api.routes.profiles import router as profiles_router
from league_api.api.routes.summoner_v4 import router as summoner_v4_router
from league_api.core.config import get_settings
from league_api.db import create_async_engine_from_url
from league_api.experimental_frontend import register_experimental_frontend
from league_api.jobs.ingestion import run_ladder_ingestion
from league_api.jobs.ladder_players import run_ladder_players_fetch
from league_api.jobs.postgres_store import PostgresJobStore
from league_api.jobs.profile import run_profile_fetch
from league_api.jobs.queue import InMemoryJobQueue
from league_api.jobs.store import InMemoryJobStore
from league_api.ladders.store import InMemoryLadderPlayerStore, PostgresLadderPlayerStore
from league_api.matches.references import (
    InMemoryMatchReferenceStore,
    PostgresMatchReferenceStore,
)
from league_api.matches.store import InMemoryMatchStore, PostgresMatchStore
from league_api.players.store import InMemoryPlayerIdentityStore, PostgresPlayerIdentityStore
from league_api.redis.coordinator import RedisJobLockCoordinator, create_redis_client
from league_api.riot.cache import InMemoryRiotCacheStore
from league_api.riot.client import RiotClient, RiotRequestEventHandler
from league_api.riot.postgres_cache import PostgresRiotCacheStore
from league_api.riot.rate_limiter import RiotRateLimit, get_riot_rate_limiter
from league_api.riot.redis_rate_limiter import RedisRiotRateLimitManager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create app lifetime job, cache, and coordination infrastructure."""
    settings = get_settings()
    use_external_services = settings.app_env != "test"
    db_engine = (
        create_async_engine_from_url(settings.database_url)
        if use_external_services and settings.database_url is not None
        else None
    )
    redis_client = (
        await create_redis_client(settings.redis_url)
        if use_external_services and settings.redis_url
        else None
    )

    job_store = PostgresJobStore(db_engine) if db_engine is not None else InMemoryJobStore()
    riot_cache_store = (
        PostgresRiotCacheStore(db_engine)
        if db_engine is not None and settings.cache_enabled
        else InMemoryRiotCacheStore()
        if settings.cache_enabled
        else None
    )
    lock_coordinator = RedisJobLockCoordinator(redis_client) if redis_client is not None else None
    match_store = PostgresMatchStore(db_engine) if db_engine is not None else InMemoryMatchStore()
    match_reference_store = (
        PostgresMatchReferenceStore(db_engine)
        if db_engine is not None
        else InMemoryMatchReferenceStore()
    )
    identity_store = (
        PostgresPlayerIdentityStore(db_engine)
        if db_engine is not None
        else InMemoryPlayerIdentityStore()
    )
    ladder_player_store = (
        PostgresLadderPlayerStore(db_engine)
        if db_engine is not None
        else InMemoryLadderPlayerStore()
    )
    riot_rate_limiter = (
        RedisRiotRateLimitManager(
            redis_client=redis_client,
            limits=[
                RiotRateLimit(
                    request_count=settings.riot_app_rate_limit_short_requests,
                    window_seconds=settings.riot_app_rate_limit_short_window_seconds,
                ),
                RiotRateLimit(
                    request_count=settings.riot_app_rate_limit_long_requests,
                    window_seconds=settings.riot_app_rate_limit_long_window_seconds,
                ),
            ],
            max_retries=settings.riot_rate_limit_max_retries,
            retry_after_buffer_seconds=settings.riot_rate_limit_retry_after_buffer_seconds,
            retry_after_fallback_seconds=settings.riot_rate_limit_retry_after_fallback_seconds,
            manual_reserve_fraction=settings.riot_manual_rate_limit_reserve_fraction,
            manual_reserve_unlock_seconds=settings.riot_manual_rate_limit_unlock_seconds,
        )
        if redis_client is not None
        else get_riot_rate_limiter(settings)
    )

    def riot_client_factory(
        *,
        request_event_handler: RiotRequestEventHandler | None = None,
    ) -> RiotClient:
        return RiotClient.from_settings(
            settings,
            request_event_handler=request_event_handler,
            cache_store=riot_cache_store,
            rate_limiter=riot_rate_limiter,
        )

    job_queue = InMemoryJobQueue(
        store=job_store,
        ladder_ingestion_handler=partial(
            run_ladder_ingestion,
            riot_client_factory=riot_client_factory,
            match_store=match_store,
            identity_store=identity_store,
        ),
        profile_fetch_handler=partial(
            run_profile_fetch,
            riot_client_factory=riot_client_factory,
            match_store=match_store,
            identity_store=identity_store,
        ),
        ladder_players_handler=partial(
            run_ladder_players_fetch,
            riot_client_factory=riot_client_factory,
            player_store=ladder_player_store,
            identity_store=identity_store,
            match_store=match_store,
            match_reference_store=match_reference_store,
        ),
        lock_coordinator=lock_coordinator,
    )
    app.state.job_store = job_store
    app.state.job_queue = job_queue
    app.state.riot_cache_store = riot_cache_store
    app.state.riot_rate_limiter = riot_rate_limiter
    app.state.match_store = match_store
    app.state.match_reference_store = match_reference_store
    app.state.player_identity_store = identity_store
    app.state.ladder_player_store = ladder_player_store
    job_queue.start()
    await job_queue.recover_queued_jobs()
    try:
        yield
    finally:
        await job_queue.stop()
        if redis_client is not None:
            await redis_client.aclose()
        if db_engine is not None:
            await db_engine.dispose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_methods=["GET", "POST", "QUERY", "OPTIONS"],
            allow_headers=["*"],
        )
    app.include_router(jobs_router)
    app.include_router(account_v1_router)
    app.include_router(match_v5_router)
    app.include_router(league_v4_router)
    app.include_router(summoner_v4_router)
    app.include_router(profiles_router)
    app.include_router(ladders_router)
    if settings.experimental_frontend_enabled:
        app.include_router(manager_router)
        register_experimental_frontend(app)
    return app


app = create_app()
