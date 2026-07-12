from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from league_api.core.config import Settings, get_settings
from league_api.experimental_frontend import build_profile_slug, parse_profile_slug
from league_api.main import create_app


@pytest.fixture
def frontend_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("EXPERIMENTAL_FRONTEND_ENABLED", "true")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_experimental_frontend_is_disabled_by_default() -> None:
    field = Settings.model_fields["experimental_frontend_enabled"]

    assert field.default is False


def test_frontend_routes_are_unavailable_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERIMENTAL_FRONTEND_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()

    with TestClient(app) as test_client:
        home_response = test_client.get("/")
        profile_response = test_client.get("/GameName-OCE")

    assert home_response.status_code == 404
    assert profile_response.status_code == 404


def test_homepage_renders_when_enabled(frontend_client: TestClient) -> None:
    response = frontend_client.get("/")

    assert response.status_code == 200
    assert "Search a Riot profile" in response.text
    assert "NinjaGoldfinch#OCENZ" in response.text
    assert "window.__LEAGUE_PROFILE_CONFIG__" in response.text
    assert '"page":"home"' in response.text
    assert "parseRiotId" in response.text
    assert "gameName#tagLine" in response.text


def test_profile_slug_renders_profile_shell_when_enabled(frontend_client: TestClient) -> None:
    response = frontend_client.get("/GameName-OCE")

    assert response.status_code == 200
    assert "Checking cached profile data first" in response.text
    assert '"page":"profile"' in response.text
    assert '"riotId":"GameName#OCE"' in response.text
    assert '"profileSlug":"GameName-OCE"' in response.text
    assert "/profiles/fetch?" in response.text
    assert "/profiles/by-riot-id" in response.text
    assert "/jobs/status?running_only=true&amp;verbose=true" not in response.text
    assert "/jobs/status?running_only=true&verbose=true" not in response.text
    assert "orderedMatchEntries(matches, matchIds)" in response.text
    assert "for (const [matchId, match] of entries)" in response.text
    assert "renderJob(diagnostics.active_job" in response.text
    assert "Populating profile" in response.text
    assert "Refreshing profile" in response.text
    assert 'id="refresh-profile"' in response.text
    assert "PROFILE_REFRESH_LOCKOUT_MS = 60_000" in response.text
    assert "league-profile-refresh:" in response.text
    assert "Refresh in progress" in response.text
    assert "await queueProfileRefresh(config.profile)" in response.text
    assert 'id="profile-diagnostics"' in response.text
    assert '"profileMatchLimit":15' in response.text
    assert "match_start: String(options.matchStart ?? 0)" in response.text
    assert (
        "match_limit: String(options.matchLimit ?? config.defaults.profileMatchLimit)"
        in response.text
    )
    assert 'id="load-more-matches"' in response.text
    assert "renderProfileView(view, { appendMatches: true })" in response.text
    assert "entries.slice(0, 10)" not in response.text
    assert "state.profilePuuid = job.details.puuid" in response.text
    assert '["populating", "refreshing"].includes(lifecycle.state)' in response.text
    assert 'lifecycle.state === "missing" || refreshDue' in response.text
    assert "dataSummary.refresh_after" in response.text
    assert "window.setTimeout(tick, 1800)" in response.text
    assert "window.setInterval(tick, 1800)" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/jobs",
        "/profiles",
        "/riot",
        "/lol",
        "/docs",
        "/openapi.json",
    ],
)
def test_frontend_catch_all_does_not_swallow_reserved_paths(
    frontend_client: TestClient,
    path: str,
) -> None:
    response = frontend_client.get(path)

    assert "Search a Riot profile" not in response.text
    assert "window.__LEAGUE_PROFILE_CONFIG__" not in response.text


def test_profile_slug_helpers_support_hash_input_shape_and_game_name_hyphens() -> None:
    slug = build_profile_slug("Name-With-Hyphens", "OCENZ")
    profile = parse_profile_slug(slug)

    assert slug == "Name-With-Hyphens-OCENZ"
    assert profile == {
        "gameName": "Name-With-Hyphens",
        "tagLine": "OCENZ",
        "riotId": "Name-With-Hyphens#OCENZ",
        "profileSlug": "Name-With-Hyphens-OCENZ",
    }
    assert parse_profile_slug("MissingSeparator") is None
    assert parse_profile_slug("GameName-") is None
    assert parse_profile_slug("profiles") is None
