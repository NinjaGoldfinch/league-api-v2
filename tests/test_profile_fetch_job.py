from types import TracebackType
from typing import Any

import pytest

from league_api.jobs.models import JobType, ProfileFetchParams
from league_api.jobs.profile import ProfileRiotClientFactory, run_profile_fetch
from league_api.jobs.store import InMemoryJobStore
from league_api.matches.store import InMemoryMatchStore
from league_api.riot.client import RiotRequestEventHandler
from league_api.riot.rate_limiter import RiotRateLimitAudience


class FakeProfileRiotClient:
    def __init__(self, *, match_ids: list[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.match_ids = match_ids or ["OC1_1", "OC1_2", "OC1_1"]

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
        bypass_cache: bool = False,
    ) -> list[str] | dict[str, Any]:
        self.calls.append(
            {
                "method": "get_match_v5",
                "path": path,
                "regional_route": regional_route,
                "rate_limit_audience": rate_limit_audience,
                "wait_for_rate_limit": wait_for_rate_limit,
                "bypass_cache": bypass_cache,
                "params": params,
            }
        )
        if path.endswith("/ids"):
            start = int(params["start"] or 0) if params is not None else 0
            count = int(params["count"] or 100) if params is not None else 100
            return self.match_ids[start : start + count]
        match_id = path.rsplit("/", maxsplit=1)[-1]
        game_creation = int(match_id.rsplit("_", maxsplit=1)[-1])
        return {
            "metadata": {"matchId": match_id},
            "info": {"gameCreation": game_creation},
        }


class FailingSecondMatchClient(FakeProfileRiotClient):
    async def get_match_v5(self, path: str, **kwargs: Any) -> list[str] | dict[str, Any]:
        if path.endswith("/OC1_2"):
            raise RuntimeError("second match failed")
        return await super().get_match_v5(path, **kwargs)


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
    assert result.summary.match_id_pages_fetched == 1
    assert result.summary.match_id_pages_with_results == 1
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
    assert fake_client.calls[2]["params"] == {"start": 0, "count": 100}
    assert fake_client.calls[2]["bypass_cache"] is True
    assert fake_client.calls[3]["bypass_cache"] is False


@pytest.mark.asyncio
async def test_profile_fetch_links_each_match_before_processing_the_next_detail() -> None:
    store = InMemoryJobStore()
    match_store = InMemoryMatchStore()
    job = await store.create_job(
        job_type=JobType.PROFILE_FETCH,
        params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
    )

    with pytest.raises(RuntimeError, match="second match failed"):
        await run_profile_fetch(
            ProfileFetchParams(game_name="GameName", tag_line="OCE"),
            store,
            job.job_id,
            riot_client_factory=fake_profile_riot_client_factory(FailingSecondMatchClient()),
            match_store=match_store,
        )

    assert await match_store.get_player_match_ids("puuid-1") == ["OC1_1"]


@pytest.mark.asyncio
async def test_profile_fetch_paginates_all_match_ids() -> None:
    match_ids = [f"OC1_{index}" for index in range(1, 103)]
    store = InMemoryJobStore()
    job = await store.create_job(
        job_type=JobType.PROFILE_FETCH,
        params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
    )
    fake_client = FakeProfileRiotClient(match_ids=match_ids)

    result = await run_profile_fetch(
        ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        store,
        job.job_id,
        riot_client_factory=fake_profile_riot_client_factory(fake_client),
    )

    assert result.match_ids == match_ids
    assert result.summary.match_id_pages_fetched == 2
    assert result.summary.match_id_pages_with_results == 2
    assert result.summary.unique_match_ids == 102
    assert result.summary.matches_fetched == 102
    id_page_calls = [call for call in fake_client.calls if call["path"].endswith("/ids")]
    assert [call["params"] for call in id_page_calls] == [
        {"start": 0, "count": 100},
        {"start": 100, "count": 100},
    ]
    first_page_call_index = fake_client.calls.index(id_page_calls[0])
    second_page_call_index = fake_client.calls.index(id_page_calls[1])
    first_page_detail_calls = fake_client.calls[first_page_call_index + 1 : second_page_call_index]
    assert len(first_page_detail_calls) == 100
    assert [call["path"] for call in first_page_detail_calls[:2]] == [
        "/lol/match/v5/matches/OC1_1",
        "/lol/match/v5/matches/OC1_2",
    ]
    assert first_page_detail_calls[-1]["path"] == "/lol/match/v5/matches/OC1_100"
    assert fake_client.calls[second_page_call_index + 1]["path"] == "/lol/match/v5/matches/OC1_101"
    stored_job = await store.get_job(job.job_id)
    assert stored_job is not None
    assert stored_job.result is not None
    assert stored_job.result.summary.match_id_pages_fetched == 2
    assert stored_job.result.summary.match_id_pages_with_results == 2
    assert stored_job.result.summary.matches_fetched == 102


@pytest.mark.asyncio
async def test_profile_fetch_counts_blank_match_id_page_probe() -> None:
    match_ids = [f"OC1_{index}" for index in range(1, 201)]
    store = InMemoryJobStore()
    job = await store.create_job(
        job_type=JobType.PROFILE_FETCH,
        params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
    )
    fake_client = FakeProfileRiotClient(match_ids=match_ids)

    result = await run_profile_fetch(
        ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        store,
        job.job_id,
        riot_client_factory=fake_profile_riot_client_factory(fake_client),
    )

    id_page_calls = [call for call in fake_client.calls if call["path"].endswith("/ids")]
    assert [call["params"] for call in id_page_calls] == [
        {"start": 0, "count": 100},
        {"start": 100, "count": 100},
        {"start": 200, "count": 100},
    ]
    assert result.summary.match_id_pages_fetched == 3
    assert result.summary.match_id_pages_with_results == 2
    assert result.summary.match_ids_discovered == 200
    assert result.summary.unique_match_ids == 200
    assert result.summary.matches_fetched == 200


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
    assert result.summary.match_id_pages_fetched == 0
    assert result.summary.match_id_pages_with_results == 0
    assert [call["path"] for call in fake_client.calls] == [
        "/lol/match/v5/matches/OC1_1",
        "/lol/match/v5/matches/OC1_2",
    ]
    assert all(
        call["rate_limit_audience"] is RiotRateLimitAudience.AUTOMATIC for call in fake_client.calls
    )


@pytest.mark.asyncio
async def test_profile_refresh_stops_at_known_match_and_reuses_durable_history() -> None:
    store = InMemoryJobStore()
    match_store = InMemoryMatchStore()
    first_job = await store.create_job(
        job_type=JobType.PROFILE_FETCH,
        params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
    )
    await run_profile_fetch(
        ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        store,
        first_job.job_id,
        riot_client_factory=fake_profile_riot_client_factory(
            FakeProfileRiotClient(match_ids=["OC1_3", "OC1_2", "OC1_1"])
        ),
        match_store=match_store,
    )

    second_job = await store.create_job(
        job_type=JobType.PROFILE_FETCH,
        params=ProfileFetchParams(game_name="GameName", tag_line="OCE"),
    )
    second_client = FakeProfileRiotClient(match_ids=["OC1_5", "OC1_4", "OC1_3", "OC1_2", "OC1_1"])
    result = await run_profile_fetch(
        ProfileFetchParams(game_name="GameName", tag_line="OCE"),
        store,
        second_job.job_id,
        riot_client_factory=fake_profile_riot_client_factory(second_client),
        match_store=match_store,
    )

    assert [
        call["path"] for call in second_client.calls if "/lol/match/v5/matches/OC1_" in call["path"]
    ] == [
        "/lol/match/v5/matches/OC1_5",
        "/lol/match/v5/matches/OC1_4",
    ]
    assert result.match_ids == ["OC1_5", "OC1_4", "OC1_3", "OC1_2", "OC1_1"]
    assert sorted(result.matches) == ["OC1_1", "OC1_2", "OC1_3", "OC1_4", "OC1_5"]
    assert result.summary.match_id_pages_fetched == 1
    assert result.summary.unique_match_ids == 2
    assert result.summary.matches_fetched == 2
