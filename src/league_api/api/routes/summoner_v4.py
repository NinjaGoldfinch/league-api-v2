from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from league_api.api.routes.riot import RiotClientDependency, call_riot
from league_api.riot.client import RiotClient
from league_api.riot.routing import RiotPlatformRoute

router = APIRouter(prefix="/lol/summoner/v4", tags=["summoner-v4"])

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
    "/summoners/by-puuid/{encryptedPUUID}",
    summary="Get a summoner by PUUID",
    description=(
        "Mirrors Riot Summoner-V4 `GET /lol/summoner/v4/summoners/by-puuid/{encryptedPUUID}`."
    ),
)
async def get_summoner_by_puuid(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    encrypted_puuid: Annotated[
        str,
        Path(alias="encryptedPUUID", min_length=1, description="Encrypted player PUUID."),
    ],
    platform_route: PlatformRoute = RiotPlatformRoute.OC1,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_summoner_v4(
                f"/lol/summoner/v4/summoners/by-puuid/{encrypted_puuid}",
                platform_route=platform_route.value,
            )

    return await call_riot(operation)
