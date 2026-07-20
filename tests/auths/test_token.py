from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from onedep_lib.auths.token import TokenStore
from onedep_lib.config import DepositConfig
from onedep_lib.exceptions import ApiError, ApiUnreachableError, AuthError, ConfigError


def _make_jwt(exp_offset: int = 3600) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    payload = json.dumps({"exp": int(time.time()) + exp_offset}).encode()
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return f"{header}.{body}."


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text('[default]\nhostname = "https://deposit.wwpdb.org/deposition"\nssl_verify = true\n')
    return path


@pytest.fixture
def config(config_file: Path) -> DepositConfig:
    return DepositConfig(
        hostname="https://deposit.wwpdb.org/deposition",
        ssl_verify=True,
        config_path=config_file,
    )


def test_store_tokens_writes_config_toml(config: DepositConfig, config_file: Path):
    store = TokenStore(config=config)
    store.store_tokens("access123", "refresh456")
    text = config_file.read_text()
    assert "[auths.deposit_wwpdb_org]" in text
    assert 'access_token = "access123"' in text
    assert 'refresh_token = "refresh456"' in text
    assert "[default]" in text
    assert 'hostname = "https://deposit.wwpdb.org/deposition"' in text


def test_fqdn_key_excludes_scheme_port_and_path(tmp_path: Path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[default]\n")
    config = DepositConfig(hostname="https://deposit.wwpdb.org:443/deposition", config_path=config_file)
    store = TokenStore(config=config)
    store.store_tokens("access", "refresh")
    text = config_file.read_text()
    assert "[auths.deposit_wwpdb_org]" in text
    assert "deposit_wwpdb_org_deposition" not in text


def test_multiple_fqdns_are_isolated(tmp_path: Path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[default]\n")
    first = TokenStore(DepositConfig(hostname="https://deposit.wwpdb.org/deposition", config_path=config_file))
    second = TokenStore(DepositConfig(hostname="https://sequence.wwpdb.org/api", config_path=config_file))
    first.store_tokens(_make_jwt(3600), "refresh-a")
    second.store_tokens(_make_jwt(3600), "refresh-b")
    # In-memory isolation
    assert first._read_entry()["refresh_token"] == "refresh-a"
    assert second._read_entry()["refresh_token"] == "refresh-b"
    # TOML-level isolation: each host has its own [auths.<fqdn>] section
    text = config_file.read_text()
    assert "[auths.deposit_wwpdb_org]" in text
    assert "[auths.sequence_wwpdb_org]" in text
    assert 'refresh_token = "refresh-a"' in text
    assert 'refresh_token = "refresh-b"' in text


def test_get_access_token_returns_unexpired_token_without_network(config: DepositConfig, config_file: Path):
    token = _make_jwt(3600)
    store = TokenStore(config=config)
    store.store_tokens(token, "refresh")
    assert store.get_access_token() == token


def test_get_access_token_refreshes_expired_token(tmp_path: Path, httpserver):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[default]\n")
    expired = _make_jwt(-60)
    fresh = _make_jwt(3600)
    store = TokenStore(
        config=DepositConfig(
            hostname=httpserver.url_for("/deposition").rstrip("/"),
            ssl_verify=False,
            config_path=config_file,
        ),
    )
    store.store_tokens(expired, "old-refresh")
    httpserver.expect_request(
        "/deposition/auth/tokens/refresh",
        method="POST",
        json={"refresh_token": "old-refresh"},
    ).respond_with_json({"access_token": fresh, "refresh_token": "new-refresh"})
    assert store.get_access_token() == fresh
    assert store._read_entry()["refresh_token"] == "new-refresh"


def test_get_access_token_refreshes_when_only_refresh_token_is_loaded(tmp_path: Path, httpserver):
    config_file = tmp_path / "config.toml"
    hostname = httpserver.url_for("/deposition").rstrip("/")
    config_file.write_text(
        "[default]\n"
        f"hostname = \"{hostname}\"\n"
        "ssl_verify = false\n"
        "\n"
        "[auths.localhost]\n"
        "refresh_token = \"bootstrap-refresh\"\n"
    )
    fresh = _make_jwt(3600)
    config = DepositConfig.load(config_path=config_file)
    store = TokenStore(config=config)
    httpserver.expect_request(
        "/deposition/auth/tokens/refresh",
        method="POST",
        json={"refresh_token": "bootstrap-refresh"},
    ).respond_with_json({"access_token": fresh, "refresh_token": "rotated-refresh"})

    assert store.get_access_token() == fresh
    assert store._read_entry() == {"access_token": fresh, "refresh_token": "rotated-refresh"}


def test_get_access_token_observes_shared_config_after_another_store_refreshes(monkeypatch, config: DepositConfig):
    stale_access = _make_jwt(-60)
    fresh_access = _make_jwt(3600)
    config.access_token = stale_access
    config.refresh_token = "stale-refresh"
    first_store = TokenStore(config)
    second_store = TokenStore(config)
    calls = []

    def fake_post(url, json, verify, timeout):
        calls.append({"url": url, "json": json, "verify": verify, "timeout": timeout})
        return _TokenResponse(fresh_access, "fresh-refresh")

    monkeypatch.setattr("onedep_lib.auths.token.requests.post", fake_post)

    assert second_store.get_access_token() == fresh_access
    assert first_store.get_access_token() == fresh_access
    assert calls == [
        {
            "url": "https://deposit.wwpdb.org/deposition/auth/tokens/refresh",
            "json": {"refresh_token": "stale-refresh"},
            "verify": True,
            "timeout": 30,
        }
    ]


def test_refresh_401_explains_manual_token_required(tmp_path: Path, httpserver):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[default]\n")
    store = TokenStore(
        config=DepositConfig(
            hostname=httpserver.url_for("/deposition").rstrip("/"),
            ssl_verify=False,
            config_path=config_file,
        ),
    )
    store.store_tokens(_make_jwt(-60), "bad-refresh")
    httpserver.expect_request("/deposition/auth/tokens/refresh", method="POST").respond_with_data(status=401)
    with pytest.raises(AuthError, match="generate and paste a new token pair"):
        store.refresh()


def _offline_store(tmp_path: Path) -> TokenStore:
    """A store whose endpoints are on a closed port: nothing listens, so requests
    fails at the transport layer and no HTTP response ever exists."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("[default]\n")
    store = TokenStore(
        config=DepositConfig(
            hostname="http://127.0.0.1:1/deposition",
            ssl_verify=False,
            config_path=config_file,
        ),
    )
    store.store_tokens(_make_jwt(-60), "refresh")
    return store


def _server_store(tmp_path: Path, httpserver) -> TokenStore:
    config_file = tmp_path / "config.toml"
    config_file.write_text("[default]\n")
    store = TokenStore(
        config=DepositConfig(
            hostname=httpserver.url_for("/deposition").rstrip("/"),
            ssl_verify=False,
            config_path=config_file,
        ),
    )
    store.store_tokens(_make_jwt(-60), "refresh")
    return store


def test_refresh_unreachable_is_not_an_auth_failure(tmp_path: Path):
    # Being offline says nothing about the token. Reporting it as AuthError sends
    # the depositor off to re-issue a refresh token that was never the problem.
    store = _offline_store(tmp_path)
    with pytest.raises(ApiUnreachableError) as excinfo:
        store.refresh()
    assert excinfo.value.status_code is None
    assert not isinstance(excinfo.value, AuthError)


def test_refresh_server_error_is_not_an_auth_failure(tmp_path: Path, httpserver):
    # A 502 during an outage is not a rejected token either.
    store = _server_store(tmp_path, httpserver)
    httpserver.expect_request("/deposition/auth/tokens/refresh", method="POST").respond_with_data(status=502)
    with pytest.raises(ApiError) as excinfo:
        store.refresh()
    assert excinfo.value.status_code == 502
    assert not isinstance(excinfo.value, AuthError)


def test_refresh_malformed_body_is_not_an_auth_failure(tmp_path: Path, httpserver):
    store = _server_store(tmp_path, httpserver)
    httpserver.expect_request("/deposition/auth/tokens/refresh", method="POST").respond_with_json({"nonsense": 1})
    with pytest.raises(ApiError) as excinfo:
        store.refresh()
    assert not isinstance(excinfo.value, AuthError)


def test_revoke_unreachable_is_not_an_auth_failure(tmp_path: Path):
    store = _offline_store(tmp_path)
    store.store_tokens(_make_jwt(3600), "refresh")  # fresh: revoke reaches its own POST
    with pytest.raises(ApiUnreachableError) as excinfo:
        store.revoke()
    assert excinfo.value.status_code is None


def test_revoke_server_error_reports_the_status(tmp_path: Path, httpserver):
    store = _server_store(tmp_path, httpserver)
    store.store_tokens(_make_jwt(3600), "refresh")
    httpserver.expect_request("/deposition/auth/tokens/revoke", method="POST").respond_with_data(status=500)
    with pytest.raises(ApiError) as excinfo:
        store.revoke()
    assert excinfo.value.status_code == 500
    assert not isinstance(excinfo.value, AuthError)


def test_revoke_rejected_is_an_auth_failure(tmp_path: Path, httpserver):
    store = _server_store(tmp_path, httpserver)
    store.store_tokens(_make_jwt(3600), "refresh")
    httpserver.expect_request("/deposition/auth/tokens/revoke", method="POST").respond_with_data(status=403)
    with pytest.raises(AuthError, match="rejected"):
        store.revoke()


def test_unwritable_config_is_not_an_auth_failure(tmp_path: Path, httpserver):
    # The server accepts the refresh token; only the local write fails. Reporting
    # that as AuthError tells the depositor to re-issue a token that was just
    # validated.
    store = _server_store(tmp_path, httpserver)
    httpserver.expect_request("/deposition/auth/tokens/refresh", method="POST").respond_with_json(
        {"access_token": _make_jwt(3600), "refresh_token": "rotated"}
    )
    read_only = tmp_path / "read_only"
    read_only.mkdir()
    read_only.chmod(0o500)
    store._config.config_path = read_only / "config.toml"
    try:
        with pytest.raises(ConfigError) as excinfo:
            store.refresh()
    finally:
        read_only.chmod(0o700)
    assert not isinstance(excinfo.value, AuthError)


def test_malformed_auths_entry_is_a_config_error(tmp_path: Path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('[default]\n\n[auths.example_org]\nrefresh_token = 42\n')
    with pytest.raises(ConfigError, match="Malformed token data"):
        TokenStore(config=DepositConfig(hostname="https://example.org", config_path=config_file))


def test_invalid_hostname_is_a_config_error(tmp_path: Path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[default]\n")
    with pytest.raises(ConfigError, match="Invalid hostname"):
        TokenStore(config=DepositConfig(hostname="", config_path=config_file))


def test_revoke_posts_refresh_token_and_clears_local_storage(tmp_path: Path, httpserver):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[default]\n")
    store = TokenStore(
        config=DepositConfig(
            hostname=httpserver.url_for("/deposition").rstrip("/"),
            ssl_verify=False,
            config_path=config_file,
        ),
    )
    access = _make_jwt(3600)
    store.store_tokens(access, "refresh")
    httpserver.expect_request(
        "/deposition/auth/tokens/revoke",
        method="POST",
        headers={"Authorization": f"Bearer {access}"},
        json={"refresh_token": "refresh"},
    ).respond_with_data(status=204)
    store.revoke()
    with pytest.raises(AuthError, match="No refresh token stored. Paste a refresh token first."):
        store.get_access_token()


def test_get_access_token_raises_auth_error_when_no_tokens_loaded(config: DepositConfig):
    store = TokenStore(config=config)
    # config was constructed directly (not via load()), so access_token is None
    with pytest.raises(AuthError, match="No refresh token stored. Paste a refresh token first."):
        store.get_access_token()


def _make_jwt_exp(exp_value) -> str:
    """A JWT carrying an arbitrary `exp` claim value (int, float, or omitted)."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    claim = {} if exp_value is None else {"exp": exp_value}
    body = base64.urlsafe_b64encode(json.dumps(claim).encode()).rstrip(b"=").decode()
    return f"{header}.{body}."


def test_is_expired_accepts_float_exp_in_future(config: DepositConfig):
    store = TokenStore(config)
    future_float = float(int(time.time()) + 3600) + 0.5
    assert store._is_expired(_make_jwt_exp(future_float)) is False


def test_is_expired_true_for_past_float_exp(config: DepositConfig):
    store = TokenStore(config)
    assert store._is_expired(_make_jwt_exp(float(int(time.time()) - 60))) is True


def test_is_expired_true_when_exp_missing(config: DepositConfig):
    store = TokenStore(config)
    assert store._is_expired(_make_jwt_exp(None)) is True


class _TokenResponse:
    status_code = 200

    def __init__(self, access_token: str, refresh_token: str) -> None:
        self._body = {"access_token": access_token, "refresh_token": refresh_token}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return self._body


def test_activate_site_refreshes_existing_site_token(monkeypatch, tmp_path: Path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[default]\nhostname = "https://deposit.wwpdb.org/deposition"\n'
        "[auths.deposit_wwpdb_org]\n"
        f'access_token = "{_make_jwt(3600)}"\n'
        'refresh_token = "default-refresh"\n'
        "[auths.deposit_pdbe_wwpdb_org]\n"
        f'access_token = "{_make_jwt(3600)}"\n'
        'refresh_token = "pdbe-refresh"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_post(url, json, verify, timeout):
        calls.append({"url": url, "json": json, "verify": verify, "timeout": timeout})
        return _TokenResponse("pdbe-access-new", "pdbe-refresh-new")

    monkeypatch.setattr("onedep_lib.auths.token.requests.post", fake_post)
    store = TokenStore(DepositConfig.load(config_path=config_file))

    assert store.activate_site("https://deposit-pdbe.wwpdb.org/deposition") == "pdbe-access-new"

    assert calls == [
        {
            "url": "https://deposit-pdbe.wwpdb.org/deposition/auth/tokens/refresh",
            "json": {"refresh_token": "pdbe-refresh"},
            "verify": True,
            "timeout": 30,
        }
    ]
    assert store._read_entry() == {"access_token": "pdbe-access-new", "refresh_token": "pdbe-refresh-new"}
    assert "pdbe-refresh-new" in config_file.read_text(encoding="utf-8")


def test_activate_site_exchanges_current_token_when_site_key_missing(monkeypatch, tmp_path: Path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[default]\nhostname = "https://deposit.wwpdb.org/deposition"\n'
        "[auths.deposit_wwpdb_org]\n"
        f'access_token = "{_make_jwt(3600)}"\n'
        'refresh_token = "default-refresh"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_post(url, json, verify, timeout):
        calls.append({"url": url, "json": json, "verify": verify, "timeout": timeout})
        return _TokenResponse("pdbe-access-new", "pdbe-refresh-new")

    monkeypatch.setattr("onedep_lib.auths.token.requests.post", fake_post)
    store = TokenStore(DepositConfig.load(config_path=config_file))

    assert store.activate_site("https://deposit-pdbe.wwpdb.org/deposition") == "pdbe-access-new"

    assert calls == [
        {
            "url": "https://deposit-pdbe.wwpdb.org/deposition/auth/tokens/exchange",
            "json": {"refresh_token": "default-refresh"},
            "verify": True,
            "timeout": 30,
        }
    ]
    text = config_file.read_text(encoding="utf-8")
    assert "[auths.deposit_pdbe_wwpdb_org]" in text
    assert 'refresh_token = "pdbe-refresh-new"' in text


def test_activate_site_uses_existing_site_token_without_default_token(monkeypatch, tmp_path: Path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[default]\nhostname = "https://deposit.wwpdb.org/deposition"\n'
        "[auths.deposit_pdbe_wwpdb_org]\n"
        f'access_token = "{_make_jwt(3600)}"\n'
        'refresh_token = "pdbe-refresh"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_post(url, json, verify, timeout):
        calls.append({"url": url, "json": json, "verify": verify, "timeout": timeout})
        return _TokenResponse("pdbe-access-new", "pdbe-refresh-new")

    monkeypatch.setattr("onedep_lib.auths.token.requests.post", fake_post)
    store = TokenStore(DepositConfig.load(config_path=config_file))

    assert store.activate_site("https://deposit-pdbe.wwpdb.org/deposition") == "pdbe-access-new"

    assert calls[0]["json"] == {"refresh_token": "pdbe-refresh"}
