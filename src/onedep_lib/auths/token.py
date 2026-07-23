from __future__ import annotations

import time
from urllib.parse import urljoin

import jwt as pyjwt
import requests

from onedep_lib.config import DepositConfig, _hostname_to_fqdn_key
from onedep_lib.exceptions import ApiError, ApiUnreachableError, AuthError, ConfigError

_REFRESH_PATH = "auth/tokens/refresh"
_EXCHANGE_PATH = "auth/tokens/exchange"
_REVOKE_PATH = "auth/tokens/revoke"


class TokenStore:
    """Manages the OneDep access/refresh token lifecycle for a DepositConfig.

    Tokens are persisted in the ``[auths.<fqdn>]`` section of the config
    file, keyed by the FQDN derived from the active hostname. Access tokens
    are short-lived JWTs (30-minute TTL); refresh tokens are long-lived
    opaque strings (30-day TTL) that rotate on every use.
    """

    def __init__(self, config: DepositConfig) -> None:
        self._config = config
        self._entries = self._load_auth_entries()
        active_key = self._fqdn_key()
        if self._config.refresh_token is not None:
            entry = {"refresh_token": self._config.refresh_token}
            if self._config.access_token is not None:
                entry["access_token"] = self._config.access_token
            self._entries[active_key] = entry

    def store_tokens(self, access_token: str, refresh_token: str) -> None:
        """Persist a token pair for the config's current hostname.

        Writes both tokens to the [auths.<fqdn>] section of the config file
        and updates the in-memory config fields.

        Args:
            access_token: The access token to store.
            refresh_token: The refresh token to store.
        """
        self._store_tokens_for_key(self._fqdn_key(), access_token, refresh_token)

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing it first if necessary.

        Returns:
            A non-expired access token for the config's current hostname.

        Raises:
            AuthError: If no refresh token is stored, or refresh fails
                because the refresh token is expired, revoked, or invalid.
            ApiUnreachableError: If a refresh is needed and the request cannot
                reach the server.
            ApiError: If a refresh is needed and the server returns an
                unexpected error response.
        """
        entry = self._read_entry()
        token = entry.get("access_token")
        if token is None or self._is_expired(token):
            return self.refresh()
        return token

    def refresh(self) -> str:
        """Exchange the stored refresh token for a new token pair.

        The rotated refresh token replaces the old one; refresh token
        rotation is mandatory on every call.

        Returns:
            The new access token.

        Raises:
            AuthError: If no refresh token is stored, or the server rejects
                the refresh token as expired, revoked, or invalid.
            ApiUnreachableError: If the refresh request cannot reach the server.
            ApiError: If the server returns an unexpected error response.
        """
        entry = self._read_entry()
        access_token, refresh_token = self._request_refresh(self._config.hostname, entry["refresh_token"])
        self.store_tokens(access_token, refresh_token)
        return access_token

    def activate_site(self, site_base_url: str) -> str:
        """Switch the active hostname to a redirected deposition site.

        If credentials already exist for site_base_url, refreshes them
        against that site. Otherwise exchanges the current refresh token for
        a token pair scoped to that site via its /auth/tokens/exchange
        endpoint. Updates config.hostname to site_base_url on success.

        Args:
            site_base_url: The deposition site root URL to activate.

        Returns:
            A valid access token for site_base_url.

        Raises:
            ConfigError: If site_base_url cannot be converted to a valid FQDN key.
            AuthError: If the exchange/refresh request is rejected.
            ApiUnreachableError: If the request cannot reach the server.
            ApiError: If the server returns an unexpected error response.
        """
        key = _hostname_to_fqdn_key(site_base_url)
        if not key:
            raise ConfigError(f"Invalid hostname for token storage: {site_base_url!r}")
        self._entries = self._load_auth_entries() | self._entries
        entry = self._entries.get(key)
        if entry is None:
            entry = self._read_entry()
            access_token, refresh_token = self._request_exchange(site_base_url, entry["refresh_token"])
        else:
            access_token, refresh_token = self._request_refresh(site_base_url, entry["refresh_token"])
        self._config.hostname = site_base_url
        self._store_tokens_for_key(key, access_token, refresh_token)
        return access_token

    def revoke(self) -> None:
        """Revoke the current refresh token on the server and clear it locally.

        Posts the current refresh token to the server's revoke endpoint.
        On success (204 No Content), removes the [auths.<fqdn>] entry from
        the config file and clears the in-memory token fields via
        clear_tokens(). After revocation, get_access_token() raises
        AuthError until new tokens are stored.

        Raises:
            AuthError: If the server rejects the revoke request (401/403).
            ApiUnreachableError: If the request cannot reach the server.
            ApiError: If the server returns an unexpected error response.
        """
        entry = self._read_entry()
        access_token = self.get_access_token()
        try:
            response = requests.post(
                self._url(_REVOKE_PATH),
                headers={"Authorization": f"Bearer {access_token}"},
                json={"refresh_token": entry["refresh_token"]},
                verify=self._config.ssl_verify,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ApiUnreachableError(f"Token revoke failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise AuthError("Token revoke was rejected; credentials are expired, revoked, or invalid.")
        if response.status_code != 204:
            raise ApiError(f"Token revoke failed with status {response.status_code}", response.status_code)
        self.clear_tokens()

    def clear_tokens(self) -> None:
        """Clear stored tokens locally without contacting the server.

        Removes the in-memory access/refresh tokens and deletes the
        [auths.<fqdn>] entry for the config's current hostname from the
        config file.
        """
        self._config.access_token = None
        self._config.refresh_token = None
        key = self._fqdn_key()
        self._config.delete_auth_entry(key)
        self._entries.pop(key, None)

    def _load_auth_entries(self) -> dict[str, dict[str, str]]:
        raw_entries = self._config.read_auth_entries()
        entries: dict[str, dict[str, str]] = {}
        for key, entry in raw_entries.items():
            access_token = entry.get("access_token")
            refresh_token = entry.get("refresh_token")
            if access_token is not None and not isinstance(access_token, str):
                raise ConfigError(f"Malformed token data in [auths.{key}]")
            if refresh_token is not None and not isinstance(refresh_token, str):
                raise ConfigError(f"Malformed token data in [auths.{key}]")
            if refresh_token is None:
                continue
            values = {"refresh_token": refresh_token}
            if access_token is not None:
                values["access_token"] = access_token
            entries[key] = values
        return entries

    def _store_tokens_for_key(self, key: str, access_token: str, refresh_token: str) -> None:
        self._config.write_auth_entry(
            key,
            {"access_token": access_token, "refresh_token": refresh_token},
        )
        self._entries[key] = {"access_token": access_token, "refresh_token": refresh_token}
        self._config.access_token = access_token
        self._config.refresh_token = refresh_token

    def _request_refresh(self, hostname: str, refresh_token: str) -> tuple[str, str]:
        return self._request_token_pair(hostname, _REFRESH_PATH, refresh_token, "refresh")

    def _request_exchange(self, hostname: str, refresh_token: str) -> tuple[str, str]:
        return self._request_token_pair(hostname, _EXCHANGE_PATH, refresh_token, "exchange")

    def _request_token_pair(
        self,
        hostname: str,
        path: str,
        refresh_token: str,
        operation: str,
    ) -> tuple[str, str]:
        try:
            response = requests.post(
                self._url_for(hostname, path),
                json={"refresh_token": refresh_token},
                verify=self._config.ssl_verify,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ApiUnreachableError(f"Token {operation} failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise AuthError("Refresh token is expired, revoked, or invalid; generate and paste a new token pair.")

        try:
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise ApiError(f"Token {operation} failed: {exc}", response.status_code) from exc

        access_token = body.get("access_token")
        refresh_token_out = body.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(refresh_token_out, str):
            raise ApiError(
                f"Token {operation} response missing access_token or refresh_token",
                response.status_code,
            )
        return access_token, refresh_token_out

    def _read_entry(self) -> dict[str, str]:
        key = self._fqdn_key()
        entry = self._entries.get(key)
        config_entry = self._entry_from_config()
        if config_entry is not None and (
            entry is None or config_entry.get("refresh_token") != entry.get("refresh_token")
        ):
            entry = config_entry
            self._entries[key] = entry
        if entry is None:
            access_token = self._config.access_token
            refresh_token = self._config.refresh_token
            if refresh_token is None:
                raise AuthError("No refresh token stored. Paste a refresh token first.")
            entry = {"refresh_token": refresh_token}
            if access_token is not None:
                entry["access_token"] = access_token
            self._entries[key] = entry
        if entry.get("refresh_token") is None:
            raise AuthError("No refresh token stored. Paste a refresh token first.")
        return dict(entry)

    def _entry_from_config(self) -> dict[str, str] | None:
        refresh_token = self._config.refresh_token
        if refresh_token is None:
            return None
        entry = {"refresh_token": refresh_token}
        if self._config.access_token is not None:
            entry["access_token"] = self._config.access_token
        return entry

    def _fqdn_key(self) -> str:
        key = _hostname_to_fqdn_key(self._config.hostname)
        if not key:
            raise ConfigError(f"Invalid hostname for token storage: {self._config.hostname!r}")
        return key

    def _url(self, path: str) -> str:
        return self._url_for(self._config.hostname, path)

    def _url_for(self, hostname: str, path: str) -> str:
        base = hostname.rstrip("/") + "/"
        return urljoin(base, path)

    def _is_expired(self, token: str) -> bool:
        try:
            payload = pyjwt.decode(
                token,
                options={"verify_signature": False},
                algorithms=["HS256", "RS256", "none"],
            )
            exp = payload.get("exp")
            return exp is None or not isinstance(exp, (int, float)) or exp < time.time() + 60
        except Exception:
            return True
