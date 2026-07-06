from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LeagueEntry(BaseModel):
    """Minimum League-V4 ranked entry fields used by first-stage ingestion."""

    puuid: str
    summoner_id: str | None = Field(default=None, alias="summonerId")
    league_points: int | None = Field(default=None, alias="leaguePoints")
    wins: int | None = None
    losses: int | None = None
    veteran: bool | None = None
    inactive: bool | None = None
    fresh_blood: bool | None = Field(default=None, alias="freshBlood")
    hot_streak: bool | None = Field(default=None, alias="hotStreak")

    model_config = ConfigDict(populate_by_name=True)


MatchDetail = dict[str, Any]
