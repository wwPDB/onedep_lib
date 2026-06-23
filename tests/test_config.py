import pytest

from onedep_lib.config import DepositConfig, _parse_bool
from onedep_lib.exceptions import ConfigError


def test_parse_bool_true_values():
    assert _parse_bool("true", "VAR") is True
    assert _parse_bool("True", "VAR") is True
    assert _parse_bool("TRUE", "VAR") is True
    assert _parse_bool("1", "VAR") is True


def test_parse_bool_false_values():
    assert _parse_bool("false", "VAR") is False
    assert _parse_bool("False", "VAR") is False
    assert _parse_bool("FALSE", "VAR") is False
    assert _parse_bool("0", "VAR") is False


def test_parse_bool_invalid_raises():
    with pytest.raises(ValueError, match="ONEDEP_SSL_VERIFY"):
        _parse_bool("yes", "ONEDEP_SSL_VERIFY")
    with pytest.raises(ValueError, match="ONEDEP_REDIRECT"):
        _parse_bool("on", "ONEDEP_REDIRECT")


def test_load_defaults_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = DepositConfig.load()
    assert config.hostname == "https://deposit.wwpdb.org/deposition"
    assert config.ssl_verify is True
    assert config.redirect is True
    assert config.access_token is None


def test_load_reads_toml_file(monkeypatch, tmp_path):
    config_dir = tmp_path / ".config" / "onedep"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[default]\nhostname = "https://example.com"\nssl_verify = false\nredirect = false\n'
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    config = DepositConfig.load()
    assert config.hostname == "https://example.com"
    assert config.ssl_verify is False
    assert config.redirect is False


def test_load_skips_missing_default_section(monkeypatch, tmp_path):
    config_dir = tmp_path / ".config" / "onedep"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[other]\nhostname = "https://other.example.com"\n')
    monkeypatch.setenv("HOME", str(tmp_path))
    config = DepositConfig.load()
    assert config.access_token is None  # [default] absent → skipped


def test_load_malformed_toml_raises(monkeypatch, tmp_path):
    config_dir = tmp_path / ".config" / "onedep"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("this is not : valid toml [[\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="config.toml"):
        DepositConfig.load()


def test_load_ignores_unknown_keys_in_file(monkeypatch, tmp_path):
    config_dir = tmp_path / ".config" / "onedep"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[default]\nunknown_key = "ignored"\nhostname = "https://example.com"\n')
    monkeypatch.setenv("HOME", str(tmp_path))
    config = DepositConfig.load()
    assert config.hostname == "https://example.com"  # did not raise


def test_load_empty_hostname_in_file_falls_back(monkeypatch, tmp_path):
    config_dir = tmp_path / ".config" / "onedep"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[default]\nhostname = ""\n')
    monkeypatch.setenv("HOME", str(tmp_path))
    config = DepositConfig.load()
    assert config.hostname == "https://deposit.wwpdb.org/deposition"


def test_env_var_access_token(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ONEDEP_ACCESS_TOKEN", "env-token")
    config = DepositConfig.load()
    assert config.access_token == "env-token"


def test_env_var_hostname(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ONEDEP_HOSTNAME", "https://env.example.com")
    config = DepositConfig.load()
    assert config.hostname == "https://env.example.com"


def test_env_var_empty_hostname_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ONEDEP_HOSTNAME", "")
    config = DepositConfig.load()
    assert config.hostname == "https://deposit.wwpdb.org/deposition"


def test_env_var_ssl_verify_false(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ONEDEP_SSL_VERIFY", "false")
    config = DepositConfig.load()
    assert config.ssl_verify is False


def test_env_var_ssl_verify_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ONEDEP_SSL_VERIFY", "FALSE")
    config = DepositConfig.load()
    assert config.ssl_verify is False


def test_env_var_redirect_false(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ONEDEP_REDIRECT", "0")
    config = DepositConfig.load()
    assert config.redirect is False


def test_env_var_invalid_bool_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ONEDEP_SSL_VERIFY", "yes")
    with pytest.raises(ValueError, match="ONEDEP_SSL_VERIFY"):
        DepositConfig.load()


def test_env_var_overrides_file(monkeypatch, tmp_path):
    config_dir = tmp_path / ".config" / "onedep"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[default]\nhostname = "https://file.example.com"\n')
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ONEDEP_HOSTNAME", "https://env.example.com")
    config = DepositConfig.load()
    assert config.hostname == "https://env.example.com"


def test_constructor_overrides_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ONEDEP_HOSTNAME", "https://env.example.com")
    config = DepositConfig.load(hostname="https://explicit.example.com")
    assert config.hostname == "https://explicit.example.com"


def test_defaults_include_schema_and_session_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = DepositConfig()
    assert cfg.access_token is None
    assert cfg.hostname == "https://deposit.wwpdb.org/deposition"
    assert cfg.ssl_verify is True
    assert cfg.redirect is True
    assert cfg.schema_base_url == "https://schemas.wwpdb.org/nextdep"
    assert "schemas" in str(cfg.schema_cache_dir)
    assert "sessions" in str(cfg.session_dir)


def test_constructor_overrides(monkeypatch):
    monkeypatch.delenv("ONEDEP_HOSTNAME", raising=False)
    cfg = DepositConfig.load(hostname="https://test.example.com", ssl_verify=False)
    assert cfg.hostname == "https://test.example.com"
    assert cfg.ssl_verify is False


def test_env_var_overrides(monkeypatch):
    monkeypatch.setenv("ONEDEP_HOSTNAME", "https://env.example.com")
    monkeypatch.setenv("ONEDEP_SSL_VERIFY", "false")
    cfg = DepositConfig.load()
    assert cfg.hostname == "https://env.example.com"
    assert cfg.ssl_verify is False


def test_constructor_beats_env_var(monkeypatch):
    monkeypatch.setenv("ONEDEP_HOSTNAME", "https://env.example.com")
    cfg = DepositConfig.load(hostname="https://override.example.com")
    assert cfg.hostname == "https://override.example.com"


def test_invalid_bool_env_var_raises_config_error(monkeypatch):
    monkeypatch.setenv("ONEDEP_SSL_VERIFY", "yes")
    with pytest.raises(ConfigError):
        DepositConfig.load()


def test_schema_base_url_env_override(monkeypatch):
    monkeypatch.setenv("ONEDEP_SCHEMA_URL", "http://localhost:8080/schemas")
    cfg = DepositConfig.load()
    assert cfg.schema_base_url == "http://localhost:8080/schemas"


def test_default_config_path_is_onedep_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = DepositConfig()
    expected = tmp_path / ".config" / "onedep" / "config.toml"
    assert cfg.config_path == expected


def test_load_config_path_override_reads_from_given_file(tmp_path):
    cfg_file = tmp_path / "custom.toml"
    cfg_file.write_text('[default]\nhostname = "https://custom.example.com"\n')
    cfg = DepositConfig.load(config_path=cfg_file)
    assert cfg.hostname == "https://custom.example.com"
    assert cfg.config_path == cfg_file


def test_read_auth_entry_returns_none_when_key_absent(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[default]\nhostname = "https://example.com"\n')
    cfg = DepositConfig(config_path=cfg_file)
    assert cfg.read_auth_entry("example_com") is None


def test_read_auth_entry_returns_dict_when_present(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[default]\n[auths.example_com]\naccess_token = "tok"\nrefresh_token = "ref"\n')
    cfg = DepositConfig(config_path=cfg_file)
    assert cfg.read_auth_entry("example_com") == {"access_token": "tok", "refresh_token": "ref"}


def test_write_auth_entry_creates_entry_and_preserves_default(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[default]\nhostname = "https://example.com"\n')
    cfg = DepositConfig(config_path=cfg_file)
    cfg.write_auth_entry("example_com", {"access_token": "a", "refresh_token": "r"})
    text = cfg_file.read_text()
    assert "[auths.example_com]" in text
    assert 'access_token = "a"' in text
    assert "[default]" in text


def test_write_auth_entry_updates_existing_entry(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[default]\n[auths.example_com]\naccess_token = "old"\nrefresh_token = "old_r"\n')
    cfg = DepositConfig(config_path=cfg_file)
    cfg.write_auth_entry("example_com", {"access_token": "new", "refresh_token": "new_r"})
    assert cfg.read_auth_entry("example_com") == {"access_token": "new", "refresh_token": "new_r"}


def test_delete_auth_entry_removes_entry_and_preserves_rest(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[default]\nhostname = "https://example.com"\n[auths.example_com]\naccess_token = "a"\nrefresh_token = "r"\n'
    )
    cfg = DepositConfig(config_path=cfg_file)
    cfg.delete_auth_entry("example_com")
    assert cfg.read_auth_entry("example_com") is None
    assert "[default]" in cfg_file.read_text()


def test_delete_auth_entry_is_noop_when_key_absent(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[default]\nhostname = "https://example.com"\n')
    original = cfg_file.read_text()
    cfg = DepositConfig(config_path=cfg_file)
    cfg.delete_auth_entry("nonexistent")
    assert cfg_file.read_text() == original


def test_write_auth_entry_creates_file_and_parent_dirs_if_missing(tmp_path):
    cfg_file = tmp_path / "subdir" / "config.toml"
    cfg = DepositConfig(config_path=cfg_file)
    cfg.write_auth_entry("example_com", {"access_token": "a", "refresh_token": "r"})
    assert cfg_file.exists()
    assert cfg.read_auth_entry("example_com") == {"access_token": "a", "refresh_token": "r"}


def test_load_populates_tokens_from_auths_section(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[default]\nhostname = "https://deposit.wwpdb.org/deposition"\n'
        '[auths.deposit_wwpdb_org]\naccess_token = "acc"\nrefresh_token = "ref"\n'
    )
    cfg = DepositConfig.load(config_path=cfg_file)
    assert cfg.access_token == "acc"
    assert cfg.refresh_token == "ref"


def test_load_leaves_tokens_none_when_auths_section_absent(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[default]\nhostname = "https://deposit.wwpdb.org/deposition"\n')
    cfg = DepositConfig.load(config_path=cfg_file)
    assert cfg.access_token is None
    assert cfg.refresh_token is None


def test_load_raises_config_error_for_non_string_token(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[default]\nhostname = "https://deposit.wwpdb.org/deposition"\n'
        '[auths.deposit_wwpdb_org]\naccess_token = 123\nrefresh_token = "ref"\n'
    )
    with pytest.raises(ConfigError, match="Malformed token"):
        DepositConfig.load(config_path=cfg_file)


def test_load_does_not_read_tokens_from_default_section(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[default]\nhostname = "https://deposit.wwpdb.org/deposition"\naccess_token = "should_be_ignored"\n'
    )
    cfg = DepositConfig.load(config_path=cfg_file)
    assert cfg.access_token is None


def test_load_tokens_can_be_injected_as_override(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[default]\nhostname = "https://deposit.wwpdb.org/deposition"\n')
    cfg = DepositConfig.load(config_path=cfg_file, access_token="injected", refresh_token="injected_r")
    assert cfg.access_token == "injected"
    assert cfg.refresh_token == "injected_r"


def test_deposit_config_constructor_accepts_token_fields():
    cfg = DepositConfig(access_token="tok", refresh_token="ref")
    assert cfg.access_token == "tok"
    assert cfg.refresh_token == "ref"


def test_load_raises_config_error_when_only_one_token_present(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[default]\nhostname = "https://deposit.wwpdb.org/deposition"\n'
        '[auths.deposit_wwpdb_org]\naccess_token = "acc"\n'
    )
    with pytest.raises(ConfigError, match="Malformed token"):
        DepositConfig.load(config_path=cfg_file)
