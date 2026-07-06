from enum import StrEnum

from league_api.riot.errors import RiotApiError

OC1 = "oc1"
SEA = "sea"
ASIA = "asia"

DEFAULT_ACCOUNT_REGIONAL_ROUTE = ASIA
DEFAULT_OCE_PLATFORM_ROUTE = OC1
DEFAULT_OCE_REGIONAL_ROUTE = SEA


class CaseInsensitiveRoute(StrEnum):
    @classmethod
    def _missing_(cls, value: object) -> "CaseInsensitiveRoute | None":
        if not isinstance(value, str):
            return None
        normalized_value = value.lower()
        for member in cls:
            if member.value == normalized_value:
                return member
        return None


class RiotRegionalRoute(CaseInsensitiveRoute):
    AMERICAS = "americas"
    ASIA = ASIA
    EUROPE = "europe"
    SEA = SEA


class RiotAccountRegionalRoute(CaseInsensitiveRoute):
    AMERICAS = "americas"
    ASIA = ASIA
    EUROPE = "europe"


class RiotPlatformRoute(CaseInsensitiveRoute):
    BR1 = "br1"
    EUN1 = "eun1"
    EUW1 = "euw1"
    JP1 = "jp1"
    KR = "kr"
    LA1 = "la1"
    LA2 = "la2"
    ME1 = "me1"
    NA1 = "na1"
    OC1 = OC1
    RU = "ru"
    SG2 = "sg2"
    TR1 = "tr1"
    TW2 = "tw2"
    VN2 = "vn2"


def normalize_regional_route(regional_route: str | RiotRegionalRoute) -> str:
    return _normalize_route(regional_route, RiotRegionalRoute, "regional")


def normalize_account_regional_route(
    regional_route: str | RiotAccountRegionalRoute,
) -> str:
    return _normalize_route(regional_route, RiotAccountRegionalRoute, "account regional")


def normalize_platform_route(platform_route: str | RiotPlatformRoute) -> str:
    return _normalize_route(platform_route, RiotPlatformRoute, "platform")


def get_platform_base_url(platform_route: str | RiotPlatformRoute) -> str:
    """Return the Riot base URL for platform-scoped APIs."""
    return f"https://{normalize_platform_route(platform_route)}.api.riotgames.com"


def get_regional_base_url(regional_route: str | RiotRegionalRoute) -> str:
    """Return the Riot base URL for regional APIs."""
    return f"https://{normalize_regional_route(regional_route)}.api.riotgames.com"


def get_account_regional_base_url(regional_route: str | RiotAccountRegionalRoute) -> str:
    """Return the Riot base URL for Account-V1 regional APIs."""
    return f"https://{normalize_account_regional_route(regional_route)}.api.riotgames.com"


def _normalize_route(
    route: str | CaseInsensitiveRoute,
    route_type: type[CaseInsensitiveRoute],
    route_kind: str,
) -> str:
    try:
        return route_type(route).value
    except ValueError as exc:
        allowed_routes = ", ".join(route.value for route in route_type)
        msg = f"Unsupported Riot {route_kind} route '{route}'. Allowed values: {allowed_routes}."
        raise RiotApiError(msg, status_code=400) from exc
