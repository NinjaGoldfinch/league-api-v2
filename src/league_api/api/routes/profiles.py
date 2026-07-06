from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from league_api.api.routes.jobs import get_job_queue, get_job_store
from league_api.api.routes.riot import RiotClientDependency
from league_api.jobs.models import JobStatus, JobType, ProfileFetchParams
from league_api.jobs.queue import (
    PROFILE_FETCH_PRIORITY,
    PROFILE_MATCH_DETAILS_PRIORITY,
    InMemoryJobQueue,
)
from league_api.jobs.store import InMemoryJobStore
from league_api.riot.client import RiotClient
from league_api.riot.errors import (
    RiotApiError,
    RiotConfigurationError,
    RiotRateLimitError,
    RiotRateLimitWouldWaitError,
)
from league_api.riot.rate_limiter import RiotRateLimitAudience
from league_api.riot.routing import RiotAccountRegionalRoute, RiotPlatformRoute, RiotRegionalRoute

router = APIRouter(prefix="/profiles", tags=["profiles"])


class ProfileFetchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: JobType
    status: JobStatus
    identity_status: str
    account: dict[str, Any] | None = None
    summoner: dict[str, Any] | None = None
    match_ids: list[str] | None = None


@router.post(
    "/fetch",
    response_model=ProfileFetchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Fetch a profile by Riot ID",
)
async def fetch_profile(
    store: Annotated[InMemoryJobStore, Depends(get_job_store)],
    job_queue: Annotated[InMemoryJobQueue, Depends(get_job_queue)],
    riot_client: Annotated[RiotClient, RiotClientDependency],
    riot_id: Annotated[
        str,
        Query(
            min_length=3,
            description="Riot ID in gameName#tagLine format.",
        ),
    ],
    account_regional_route: Annotated[
        RiotAccountRegionalRoute,
        Query(description="Riot regional route for Account-V1."),
    ] = RiotAccountRegionalRoute.ASIA,
    platform_route: Annotated[
        RiotPlatformRoute,
        Query(description="Riot platform route for Summoner-V4."),
    ] = RiotPlatformRoute.OC1,
    regional_route: Annotated[
        RiotRegionalRoute,
        Query(description="Riot regional route for Match-V5."),
    ] = RiotRegionalRoute.SEA,
) -> ProfileFetchResponse:
    game_name, tag_line = _parse_riot_id(riot_id)
    params = ProfileFetchParams(
        game_name=game_name,
        tag_line=tag_line,
        account_regional_route=account_regional_route,
        platform_route=platform_route,
        regional_route=regional_route,
    )

    account: dict[str, Any] | None = None
    summoner: dict[str, Any] | None = None
    match_ids: list[str] | None = None
    identity_status = "queued"

    try:
        async with riot_client:
            account = await _fetch_account_without_wait(riot_client, params)
            summoner = await _fetch_summoner_without_wait(riot_client, params, _puuid(account))
            match_ids = await _fetch_match_ids_without_wait(riot_client, params, _puuid(account))
            identity_status = "resolved"
    except RiotRateLimitWouldWaitError:
        pass
    except RiotConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message
        ) from exc
    except RiotRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=exc.message
        ) from exc
    except RiotApiError as exc:
        raise HTTPException(
            status_code=exc.status_code or status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    queued_params = params.model_copy(
        update={
            "account": account,
            "summoner": summoner,
            "match_ids": match_ids,
        }
    )
    job = await store.create_job(job_type=JobType.PROFILE_FETCH, params=queued_params)
    priority = PROFILE_MATCH_DETAILS_PRIORITY if match_ids is not None else PROFILE_FETCH_PRIORITY
    await job_queue.enqueue(job.job_id, priority=priority)

    return ProfileFetchResponse(
        job_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        identity_status=identity_status,
        account=account,
        summoner=summoner,
        match_ids=match_ids,
    )


async def _fetch_account_without_wait(
    riot_client: RiotClient,
    params: ProfileFetchParams,
) -> dict[str, Any]:
    account_payload = await riot_client.get_account_v1(
        "/riot/account/v1/accounts/by-riot-id/"
        f"{_path_segment(params.game_name)}/{_path_segment(params.tag_line)}",
        regional_route=params.account_regional_route,
        rate_limit_audience=RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit=False,
    )
    if not isinstance(account_payload, dict):
        msg = "Account-V1 response did not return an object."
        raise ValueError(msg)
    return account_payload


async def _fetch_summoner_without_wait(
    riot_client: RiotClient,
    params: ProfileFetchParams,
    puuid: str,
) -> dict[str, Any]:
    summoner_payload = await riot_client.get_summoner_v4(
        f"/lol/summoner/v4/summoners/by-puuid/{puuid}",
        platform_route=params.platform_route,
        rate_limit_audience=RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit=False,
    )
    if not isinstance(summoner_payload, dict):
        msg = "Summoner-V4 response did not return an object."
        raise ValueError(msg)
    return summoner_payload


async def _fetch_match_ids_without_wait(
    riot_client: RiotClient,
    params: ProfileFetchParams,
    puuid: str,
) -> list[str]:
    match_ids_payload = await riot_client.get_match_v5(
        f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
        regional_route=params.regional_route,
        params={"start": 0, "count": params.match_count},
        rate_limit_audience=RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit=False,
    )
    if not isinstance(match_ids_payload, list):
        msg = "Match ID response did not return a list."
        raise ValueError(msg)
    return [match_id for match_id in match_ids_payload if isinstance(match_id, str) and match_id]


def _parse_riot_id(riot_id: str) -> tuple[str, str]:
    if riot_id.count("#") != 1:
        raise HTTPException(
            status_code=422,
            detail="riot_id must use gameName#tagLine format.",
        )
    game_name, tag_line = (part.strip() for part in riot_id.split("#", maxsplit=1))
    if not game_name or not tag_line:
        raise HTTPException(
            status_code=422,
            detail="riot_id must include a non-empty gameName and tagLine.",
        )
    return game_name, tag_line


def _puuid(account: dict[str, Any]) -> str:
    puuid = account.get("puuid")
    if not isinstance(puuid, str) or not puuid:
        msg = "Account-V1 response did not include a PUUID."
        raise ValueError(msg)
    return puuid


def _path_segment(value: str) -> str:
    return quote(value, safe="")
