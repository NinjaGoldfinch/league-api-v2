from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from league_api.api.routes.riot import RiotClientDependency, call_riot
from league_api.riot.client import RiotClient
from league_api.riot.routing import RiotAccountRegionalRoute

router = APIRouter(prefix="/riot/account/v1", tags=["account-v1"])

RegionalRoute = Annotated[
    RiotAccountRegionalRoute,
    Query(
        description=(
            "Riot regional routing value used as the upstream host prefix. Account-V1 "
            "supports AMERICAS, ASIA, and EUROPE."
        ),
    ),
]


@router.get(
    "/accounts/by-puuid/{puuid}",
    summary="Get an account by PUUID",
    description="Mirrors Riot Account-V1 `GET /riot/account/v1/accounts/by-puuid/{puuid}`.",
)
async def get_account_by_puuid(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    puuid: Annotated[str, Path(min_length=1, description="Player PUUID.")],
    regional_route: RegionalRoute = RiotAccountRegionalRoute.ASIA,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_account_v1(
                f"/riot/account/v1/accounts/by-puuid/{puuid}",
                regional_route=regional_route.value,
            )

    return await call_riot(operation)


@router.get(
    "/accounts/by-riot-id/{gameName}/{tagLine}",
    summary="Get an account by Riot ID",
    description=(
        "Mirrors Riot Account-V1 `GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}`."
    ),
)
async def get_account_by_riot_id(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    game_name: Annotated[str, Path(alias="gameName", min_length=1, description="Game name.")],
    tag_line: Annotated[str, Path(alias="tagLine", min_length=1, description="Tag line.")],
    regional_route: RegionalRoute = RiotAccountRegionalRoute.ASIA,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_account_v1(
                f"/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}",
                regional_route=regional_route.value,
            )

    return await call_riot(operation)


@router.get(
    "/active-shards/by-game/{game}/by-puuid/{puuid}",
    summary="Get the active shard for a player",
    description=(
        "Mirrors Riot Account-V1 `GET "
        "/riot/account/v1/active-shards/by-game/{game}/by-puuid/{puuid}`."
    ),
)
async def get_active_shard(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    game: Annotated[str, Path(min_length=1, description="Game identifier.")],
    puuid: Annotated[str, Path(min_length=1, description="Player PUUID.")],
    regional_route: RegionalRoute = RiotAccountRegionalRoute.ASIA,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_account_v1(
                f"/riot/account/v1/active-shards/by-game/{game}/by-puuid/{puuid}",
                regional_route=regional_route.value,
            )

    return await call_riot(operation)
