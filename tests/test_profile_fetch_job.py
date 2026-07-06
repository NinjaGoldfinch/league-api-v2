from types import TracebackType
from typing import Any

import pytest

from league_api.jobs.models import JobType, ProfileFetchParams
from league_api.jobs.profile import ProfileRiotClientFactory, run_profile_fetch
from league_api.jobs.store import InMemoryJobStore
from league_api.riot.client import RiotRequestEventHandler
from league_api.riot.rate_limiter import RiotRateLimitAudience


class FakeProfileRiotClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "FakeProfileRiotClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def get_account_v1(
        self,
        path: str,
        *,
        regional_route: str = "asia",
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "get_account_v1",
                "path": path,
                "regional_route": regional_route,
                "rate_limit_audience": rate_limit_audience,
                "wait_for_rate_limit": wait_for_rate_limit,
                "params": params,
            }
        )
        return {"puuid": "puuid-1", "gameName": "GameName", "tagLine": "OCE"}

    async def get_summoner_v4(
        self,
        path: str,
        *,
        platform_route: str = "oc1",
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "get_summoner_v4",
                "path": path,
                "platform_route": platform_route,
                "rate_limit_audience": rate_limit_audience,
                "wait_for_rate_limit": wait_for_rate_limit,
                "params": params,
            }
        )
        return {"puuid": "puuid-1", "summonerLevel": 100}

    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str = "sea",
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> list[str] | dict[str, Any]:
        self.calls.append(
            {
                "method": "get_match_v5",
                "path": path,
                "regional_route": regional_route,
                "rate_limit_audience": rate_limit_audience,
                "wait_for_rate_limit": wait_for_rate_limit,
                "params": params,
            }
        )
        if path.endswith("/ids"):
            return ["OC1_1", "OC1_2", "OC1_1"]
        match_id = path.rsplit("/", maxsplit=1)[-1]
        return {"metadata": {"matchId": match_id}, "info": {}}


def fake_profile_riot_client_factory(
    fake_client: FakeProfileRiotClient,
) -> ProfileRiotClientFactory:
    def factory(
        *,
        request_event_handler: RiotRequestEventHandler | None = None,
    ) -> FakeProfileRiotClient:
        return fake_client

    return factory


@pytest.mark.asyncio
async def test_profile_fetch_calls_identity_match_ids_and_details_in_order() -> None:
    store = InMemoryJobStore()
    job = await store.create_job(
        job_type=JobType.PROFILE_FETCH,
        params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
    )
    fake_client = FakeProfileRiotClient()

    result = await run_profile_fetch(
        ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        store,
        job.job_id,
        riot_client_factory=fake_profile_riot_client_factory(fake_client),
    )

    assert result.account["puuid"] == "puuid-1"
    assert result.summoner["summonerLevel"] == 100
    assert result.match_ids == ["OC1_1", "OC1_2"]
    assert sorted(result.matches) == ["OC1_1", "OC1_2"]
    assert result.summary.duplicate_match_ids_skipped == 1
    assert [call["method"] for call in fake_client.calls] == [
        "get_account_v1",
        "get_summoner_v4",
        "get_match_v5",
        "get_match_v5",
        "get_match_v5",
    ]
    assert [call["rate_limit_audience"] for call in fake_client.calls] == [
        RiotRateLimitAudience.MANUAL,
        RiotRateLimitAudience.MANUAL,
        RiotRateLimitAudience.MANUAL,
        RiotRateLimitAudience.AUTOMATIC,
        RiotRateLimitAudience.AUTOMATIC,
    ]


@pytest.mark.asyncio
async def test_profile_fetch_seeded_identity_and_match_ids_fetches_details_only() -> None:
    store = InMemoryJobStore()
    params = ProfileFetchParams(
        game_name="GameName",
        tag_line="OCE",
        account={"puuid": "puuid-1"},
        summoner={"puuid": "puuid-1", "summonerLevel": 100},
        match_ids=["OC1_1", "OC1_2", "OC1_1"],
    )
    job = await store.create_job(job_type=JobType.PROFILE_FETCH, params=params)
    fake_client = FakeProfileRiotClient()

    result = await run_profile_fetch(
        params,
        store,
        job.job_id,
        riot_client_factory=fake_profile_riot_client_factory(fake_client),
    )

    assert result.match_ids == ["OC1_1", "OC1_2"]
    assert [call["path"] for call in fake_client.calls] == [
        "/lol/match/v5/matches/OC1_1",
        "/lol/match/v5/matches/OC1_2",
    ]
    assert all(
        call["rate_limit_audience"] is RiotRateLimitAudience.AUTOMATIC for call in fake_client.calls
    )
