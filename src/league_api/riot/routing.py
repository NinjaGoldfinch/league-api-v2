OC1 = "oc1"
SEA = "sea"

DEFAULT_OCE_PLATFORM_ROUTE = OC1
DEFAULT_OCE_REGIONAL_ROUTE = SEA


def get_platform_base_url(platform_route: str) -> str:
    """Return the Riot base URL for platform-scoped APIs."""
    return f"https://{platform_route.lower()}.api.riotgames.com"


def get_regional_base_url(regional_route: str) -> str:
    """Return the Riot base URL for regional APIs."""
    return f"https://{regional_route.lower()}.api.riotgames.com"
