from dataclasses import dataclass

from league_api.core.config import Settings, get_settings


@dataclass(slots=True)
class RiotClient:
    """Placeholder for future Riot API access."""

    api_key: str | None
    platform_route: str
    regional_route: str

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "RiotClient":
        resolved_settings = settings or get_settings()
        return cls(
            api_key=resolved_settings.riot_api_key,
            platform_route=resolved_settings.default_platform_route,
            regional_route=resolved_settings.default_regional_route,
        )

    # TODO: Add fetch_ladder_page for League-V4 ranked ladder pages.
    # TODO: Add fetch_match_ids_by_puuid for Match-V5 match history discovery.
    # TODO: Add fetch_match_details_by_match_id for Match-V5 match detail ingestion.
