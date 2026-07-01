from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from onedep_lib.auths.token import TokenStore
from onedep_lib.config import DepositConfig
from onedep_lib.exceptions import AuthError


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
