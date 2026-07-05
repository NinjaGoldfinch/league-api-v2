from enum import StrEnum


class PlatformRoute(StrEnum):
    """Riot platform routes for platform-scoped APIs."""

    OC1 = "oc1"


class RegionalRoute(StrEnum):
    """Riot regional routes for region-scoped APIs."""

    SEA = "sea"


# Platform routes are used for League-V4 ladder data.
OC1 = PlatformRoute.OC1

# Regional routes are used for Match-V5 data.
SEA = RegionalRoute.SEA

# OCE uses oc1 for platform routing and sea for Match-V5 regional routing.
DEFAULT_OCE_PLATFORM_ROUTE = OC1
DEFAULT_OCE_REGIONAL_ROUTE = SEA
