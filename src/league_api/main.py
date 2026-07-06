from fastapi import FastAPI

from league_api.api.routes.league_v4 import router as league_v4_router
from league_api.api.routes.match_v5 import router as match_v5_router
from league_api.core.config import get_settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.include_router(match_v5_router)
    app.include_router(league_v4_router)
    return app


app = create_app()
