class RiotApiError(Exception):
    """Base exception for expected Riot API client failures."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RiotRateLimitError(RiotApiError):
    """Raised when Riot returns HTTP 429."""

    def __init__(self, message: str, *, retry_after: str | None = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class RiotConfigurationError(RiotApiError):
    """Raised when Riot client configuration is incomplete."""
