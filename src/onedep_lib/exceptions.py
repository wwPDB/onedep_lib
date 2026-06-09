class OneDepError(Exception):
    """Base exception for all onedep_lib errors."""


class AuthError(OneDepError):
    """OIDC flow failure or token expired/invalid."""


class ApiError(OneDepError):
    """HTTP error from the OneDep API."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class ConfigError(OneDepError, ValueError):
    """Missing or invalid configuration."""


class SchemaError(OneDepError):
    """Schema fetch failure, cache corruption, or validation engine error."""


DepositApiException = ApiError
