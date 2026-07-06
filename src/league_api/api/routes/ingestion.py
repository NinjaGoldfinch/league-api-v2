from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from league_api.ingestion.ladder_page import LadderPageIngestionResult, LadderPageIngestionService
from league_api.riot.client import RiotClient
from league_api.riot.errors import RiotApiError, RiotConfigurationError, RiotRateLimitError
from league_api.riot.routing import DEFAULT_OCE_PLATFORM_ROUTE

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

QUERY_METHOD_DESCRIPTION = (
    "Ingest one Riot ranked ladder page. This operation accepts `GET` with URL query "
    "parameters and the HTTP `QUERY` method with either URL query parameters or a "
    "`LadderPageIngestionRequest` JSON body."
)


class LadderPageIngestionRequest(BaseModel):
    platform_route: str = DEFAULT_OCE_PLATFORM_ROUTE
    queue: str = "RANKED_SOLO_5x5"
    tier: str = "CHALLENGER"
    division: str | None = None
    page: int | None = Field(default=None, ge=1)


def get_riot_client() -> RiotClient:
    return RiotClient.from_settings()


async def run_ladder_page_ingestion(
    riot_client: RiotClient,
    request: LadderPageIngestionRequest,
) -> LadderPageIngestionResult:
    try:
        async with riot_client:
            service = LadderPageIngestionService(riot_client)
            return await service.ingest_ladder_page(
                queue=request.queue,
                tier=request.tier,
                division=request.division,
                page=request.page,
                platform_route=request.platform_route,
            )
    except RiotConfigurationError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    except RiotRateLimitError as exc:
        raise HTTPException(status_code=429, detail=exc.message) from exc
    except RiotApiError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc


@router.get(
    "/ladder-page",
    response_model=LadderPageIngestionResult,
    description=QUERY_METHOD_DESCRIPTION,
    openapi_extra={
        "x-http-method-aliases": ["QUERY"],
        "x-query-request-body": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/LadderPageIngestionRequest"}
                }
            }
        },
    },
)
async def ingest_ladder_page(
    riot_client: Annotated[RiotClient, Depends(get_riot_client)],
    platform_route: str = DEFAULT_OCE_PLATFORM_ROUTE,
    queue: str = "RANKED_SOLO_5x5",
    tier: str = "CHALLENGER",
    division: str | None = None,
    page: Annotated[int | None, Query(ge=1)] = None,
) -> LadderPageIngestionResult:
    return await run_ladder_page_ingestion(
        riot_client,
        LadderPageIngestionRequest(
            platform_route=platform_route,
            queue=queue,
            tier=tier,
            division=division,
            page=page,
        ),
    )


@router.api_route("/ladder-page", methods=["QUERY"], include_in_schema=False)
async def query_ladder_page(
    riot_client: Annotated[RiotClient, Depends(get_riot_client)],
    platform_route: str = DEFAULT_OCE_PLATFORM_ROUTE,
    queue: str = "RANKED_SOLO_5x5",
    tier: str = "CHALLENGER",
    division: str | None = None,
    page: Annotated[int | None, Query(ge=1)] = None,
    body: Annotated[LadderPageIngestionRequest | None, Body()] = None,
) -> LadderPageIngestionResult:
    request = body or LadderPageIngestionRequest(
        platform_route=platform_route,
        queue=queue,
        tier=tier,
        division=division,
        page=page,
    )
    return await run_ladder_page_ingestion(riot_client, request)
