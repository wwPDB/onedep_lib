from __future__ import annotations

class OneDepError(Exception):
    """Base exception for all onedep_lib errors."""


class AuthError(OneDepError):
    """OIDC flow failure or token expired/invalid."""


class ApiError(OneDepError):
    """HTTP error from the OneDep API.

    ``status_code`` is the status the API responded with. It is None when no
    response was received at all -- see ApiUnreachableError.
    """

    def __init__(self, message: str, status_code: int | None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApiUnreachableError(ApiError):
    """The OneDep API could not be reached.

    Raised for transport-level failures -- DNS failure, connection refused, TLS
    error, timeout -- where no HTTP response was received, so there is no status
    code to report and ``status_code`` is None.

    Subclasses ApiError so that existing ``except ApiError`` handlers keep
    catching these. Catch it specifically to tell "the server was never reached"
    apart from a response the server actually sent, such as a genuine 401/403.
    Callers that need to decide whether the user's credentials are bad must make
    that distinction: an offline client is not an unauthorized one.
    """

    def __init__(self, message: str = "Failed to access the API") -> None:
        super().__init__(message, None)


class ConfigError(OneDepError, ValueError):
    """Missing or invalid configuration."""


class SchemaError(OneDepError):
    """Schema fetch failure, cache corruption, or validation engine error."""


DepositApiException = ApiError
