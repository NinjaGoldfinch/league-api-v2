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


class RiotRateLimitWouldWaitError(RiotRateLimitError):
    """Raised when a non-blocking Riot request would need to wait for capacity."""

    def __init__(self, message: str, *, wait_seconds: float) -> None:
        super().__init__(message)
        self.wait_seconds = wait_seconds


class RiotConfigurationError(RiotApiError):
    """Raised when Riot client configuration is incomplete."""
