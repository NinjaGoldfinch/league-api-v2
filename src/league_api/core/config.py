from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = Field(default="League API", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    riot_api_key: str | None = Field(default=None, alias="RIOT_API_KEY")
    default_platform_route: str = Field(default="oc1", alias="DEFAULT_PLATFORM_ROUTE")
    default_regional_route: str = Field(default="sea", alias="DEFAULT_REGIONAL_ROUTE")
    riot_app_rate_limit_short_requests: int = Field(
        default=20,
        ge=1,
        alias="RIOT_APP_RATE_LIMIT_SHORT_REQUESTS",
    )
    riot_app_rate_limit_short_window_seconds: float = Field(
        default=1.0,
        gt=0.0,
        alias="RIOT_APP_RATE_LIMIT_SHORT_WINDOW_SECONDS",
    )
    riot_app_rate_limit_long_requests: int = Field(
        default=100,
        ge=1,
        alias="RIOT_APP_RATE_LIMIT_LONG_REQUESTS",
    )
    riot_app_rate_limit_long_window_seconds: float = Field(
        default=120.0,
        gt=0.0,
        alias="RIOT_APP_RATE_LIMIT_LONG_WINDOW_SECONDS",
    )
    riot_rate_limit_max_retries: int = Field(
        default=3,
        ge=0,
        alias="RIOT_RATE_LIMIT_MAX_RETRIES",
    )
    riot_rate_limit_retry_after_buffer_seconds: float = Field(
        default=1.0,
        ge=0.0,
        alias="RIOT_RATE_LIMIT_RETRY_AFTER_BUFFER_SECONDS",
    )
    riot_rate_limit_retry_after_fallback_seconds: float = Field(
        default=120.0,
        ge=0.0,
        alias="RIOT_RATE_LIMIT_RETRY_AFTER_FALLBACK_SECONDS",
    )
    riot_manual_rate_limit_reserve_fraction: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        alias="RIOT_MANUAL_RATE_LIMIT_RESERVE_FRACTION",
    )
    riot_manual_rate_limit_unlock_seconds: float = Field(
        default=10.0,
        ge=0.0,
        alias="RIOT_MANUAL_RATE_LIMIT_UNLOCK_SECONDS",
    )
    riot_request_logs_enabled: bool = Field(
        default=True,
        alias="RIOT_REQUEST_LOGS_ENABLED",
    )
    cors_allowed_origins: list[str] = Field(default_factory=list, alias="CORS_ALLOWED_ORIGINS")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    cache_enabled: bool = Field(default=False, alias="CACHE_ENABLED")
    cache_default_ttl_seconds: int = Field(
        default=300,
        ge=0,
        alias="CACHE_DEFAULT_TTL_SECONDS",
    )
    cache_stale_while_revalidate_seconds: int = Field(
        default=300,
        ge=0,
        alias="CACHE_STALE_WHILE_REVALIDATE_SECONDS",
    )
    cache_match_detail_ttl_seconds: int = Field(
        default=86400,
        ge=0,
        alias="CACHE_MATCH_DETAIL_TTL_SECONDS",
    )
    cache_match_ids_ttl_seconds: int = Field(
        default=300,
        ge=0,
        alias="CACHE_MATCH_IDS_TTL_SECONDS",
    )
    cache_account_ttl_seconds: int = Field(
        default=3600,
        ge=0,
        alias="CACHE_ACCOUNT_TTL_SECONDS",
    )
    cache_summoner_ttl_seconds: int = Field(
        default=3600,
        ge=0,
        alias="CACHE_SUMMONER_TTL_SECONDS",
    )
    cache_league_entries_ttl_seconds: int = Field(
        default=300,
        ge=0,
        alias="CACHE_LEAGUE_ENTRIES_TTL_SECONDS",
    )
    cache_ladder_ttl_seconds: int = Field(
        default=300,
        ge=0,
        alias="CACHE_LADDER_TTL_SECONDS",
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
