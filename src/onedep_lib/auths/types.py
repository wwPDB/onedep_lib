from __future__ import annotations

from typing import Protocol


class AuthProvider(Protocol):
    def store_tokens(self, access_token: str, refresh_token: str) -> None:
        """Persist a manually supplied token pair."""
        ...

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing and persisting rotation when needed."""
        ...

    def refresh(self) -> str:
        """Refresh tokens explicitly and return the new access token."""
        ...

    def revoke(self) -> None:
        """Revoke the current refresh token and clear local storage on success."""
        ...

    def clear_tokens(self) -> None:
        """Clear local token storage without contacting the server."""
        ...
