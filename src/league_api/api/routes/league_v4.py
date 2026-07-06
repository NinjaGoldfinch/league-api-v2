from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from league_api.api.routes.riot import RiotClientDependency, call_riot
from league_api.riot.client import RiotClient
from league_api.riot.routing import RiotPlatformRoute

router = APIRouter(prefix="/lol/league/v4", tags=["league-v4"])

PlatformRoute = Annotated[
    RiotPlatformRoute,
    Query(
        description=(
            "Riot platform routing value used as the upstream host prefix, for example "
            "BR1, EUN1, EUW1, JP1, KR, LA1, LA2, ME1, NA1, OC1, RU, SG2, TR1, TW2, or VN2."
        ),
    ),
]


@router.get(
    "/challengerleagues/by-queue/{queue}",
    summary="Get the challenger league for a queue",
    description="Mirrors Riot League-V4 `GET /lol/league/v4/challengerleagues/by-queue/{queue}`.",
)
async def get_challenger_league(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    queue: Annotated[str, Path(min_length=1, description="Ranked queue.")],
    platform_route: PlatformRoute = RiotPlatformRoute.OC1,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_league_v4(
                f"/lol/league/v4/challengerleagues/by-queue/{queue}",
                platform_route=platform_route.value,
            )

    return await call_riot(operation)


@router.get(
    "/entries/by-puuid/{encryptedPUUID}",
    summary="Get league entries in all queues for a PUUID",
    description="Mirrors Riot League-V4 `GET /lol/league/v4/entries/by-puuid/{encryptedPUUID}`.",
)
async def get_league_entries_by_puuid(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    encrypted_puuid: Annotated[
        str,
        Path(alias="encryptedPUUID", min_length=1, description="Encrypted player PUUID."),
    ],
    platform_route: PlatformRoute = RiotPlatformRoute.OC1,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_league_v4(
                f"/lol/league/v4/entries/by-puuid/{encrypted_puuid}",
                platform_route=platform_route.value,
            )

    return await call_riot(operation)


@router.get(
    "/entries/{queue}/{tier}/{division}",
    summary="Get league entries",
    description=(
        "Mirrors Riot League-V4 `GET /lol/league/v4/entries/{queue}/{tier}/{division}`. "
        "The optional `page` query parameter is passed through when provided."
    ),
)
async def get_league_entries(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    queue: Annotated[str, Path(min_length=1, description="Ranked queue.")],
    tier: Annotated[str, Path(min_length=1, description="Ranked tier.")],
    division: Annotated[str, Path(min_length=1, description="Ranked division.")],
    platform_route: PlatformRoute = RiotPlatformRoute.OC1,
    page: Annotated[int | None, Query(ge=1, description="Page index.")] = None,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_league_v4(
                f"/lol/league/v4/entries/{queue}/{tier}/{division}",
                platform_route=platform_route.value,
                params={"page": page},
            )

    return await call_riot(operation)


@router.get(
    "/grandmasterleagues/by-queue/{queue}",
    summary="Get the grandmaster league for a queue",
    description=(
        "Mirrors Riot League-V4 `GET /lol/league/v4/grandmasterleagues/by-queue/{queue}`."
    ),
)
async def get_grandmaster_league(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    queue: Annotated[str, Path(min_length=1, description="Ranked queue.")],
    platform_route: PlatformRoute = RiotPlatformRoute.OC1,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_league_v4(
                f"/lol/league/v4/grandmasterleagues/by-queue/{queue}",
                platform_route=platform_route.value,
            )

    return await call_riot(operation)


@router.get(
    "/masterleagues/by-queue/{queue}",
    summary="Get the master league for a queue",
    description="Mirrors Riot League-V4 `GET /lol/league/v4/masterleagues/by-queue/{queue}`.",
)
async def get_master_league(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    queue: Annotated[str, Path(min_length=1, description="Ranked queue.")],
    platform_route: PlatformRoute = RiotPlatformRoute.OC1,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_league_v4(
                f"/lol/league/v4/masterleagues/by-queue/{queue}",
                platform_route=platform_route.value,
            )

    return await call_riot(operation)
