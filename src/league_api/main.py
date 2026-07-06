from fastapi import FastAPI

from league_api.api.routes.health import router as health_router
from league_api.api.routes.ingestion import router as ingestion_router
from league_api.core.config import get_settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.include_router(health_router)
    app.include_router(ingestion_router)
    return app


app = create_app()
