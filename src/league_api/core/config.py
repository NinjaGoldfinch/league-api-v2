from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = Field(default="League API", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    riot_api_key: str | None = Field(default=None, alias="RIOT_API_KEY")
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/league_api",
        alias="DATABASE_URL",
    )
    default_platform_route: str = Field(default="oc1", alias="DEFAULT_PLATFORM_ROUTE")
    default_regional_route: str = Field(default="sea", alias="DEFAULT_REGIONAL_ROUTE")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
