from typing import TypedDict

from fastapi import APIRouter


class HealthResponse(TypedDict):
    status: str
    service: str


router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> HealthResponse:
    return {"status": "ok", "service": "league-api"}
