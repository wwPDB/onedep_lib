from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import tomli_w

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from onedep_lib.exceptions import ConfigError


def _parse_bool(value: str, var_name: str) -> bool:
    lowered = value.lower()
    if lowered in ("true", "1"):
        return True
    if lowered in ("false", "0"):
        return False
    raise ConfigError(f"{var_name}={value!r} is not a valid boolean. Use 'true', 'false', '1', or '0'.")


def _hostname_to_fqdn_key(hostname: str) -> str | None:
    parsed = urlparse(hostname)
    host = parsed.hostname
    if host is None:
        host = urlparse(f"https://{hostname}").hostname
    if not host:
        return None
    return host.replace(".", "_").replace("-", "_")


_ENV_MAP: dict[str, tuple[str, Callable[[str], object]]] = {
    "ONEDEP_ACCESS_TOKEN": ("access_token", str),
    "ONEDEP_REFRESH_TOKEN": ("refresh_token", str),
    "ONEDEP_HOSTNAME": ("hostname", str),
    "ONEDEP_SSL_VERIFY": ("ssl_verify", lambda v: _parse_bool(v, "ONEDEP_SSL_VERIFY")),
    "ONEDEP_REDIRECT": ("redirect", lambda v: _parse_bool(v, "ONEDEP_REDIRECT")),
    "ONEDEP_SCHEMA_URL": ("schema_base_url", str),
}


@dataclass
class DepositConfig:
    access_token: str | None = None
    refresh_token: str | None = None
    hostname: str = "https://deposit.wwpdb.org/deposition"
    ssl_verify: bool = True
    redirect: bool = True
    fetch_local_schema: bool = True
    local_schema_cache_dir: Path = field(default_factory=lambda: Path(__file__).parent / "schemas" / "json")
    schema_base_url: str = "https://schemas.wwpdb.org/nextdep"
    schema_cache_dir: Path = field(default_factory=lambda: Path.home() / ".onedep" / "schemas")
    session_dir: Path = field(default_factory=lambda: Path.home() / ".onedep" / "sessions")
    config_path: Path = field(default_factory=lambda: Path.home() / ".config" / "onedep" / "config.toml")

    @staticmethod
    def _load_toml_file(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            with path.open("rb") as fp:
                return tomllib.load(fp)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Failed to parse {path}: {exc}") from exc

    @classmethod
    def load(cls, **overrides: object) -> DepositConfig:
        valid_fields = {f.name for f in fields(cls)}
        merged: dict[str, object] = {}

        config_path_override = overrides.pop("config_path", None)
        config_file = (
            Path(config_path_override)  # type: ignore[arg-type]
            if config_path_override is not None
            else DepositConfig().config_path
        )
        merged["config_path"] = config_file

        raw = cls._load_toml_file(config_file)
        section = raw.get("default", {})
        for key, value in section.items():
            if key in valid_fields and key not in ("config_path", "access_token", "refresh_token"):
                if key == "hostname" and value == "":
                    continue
                merged[key] = value

        for env_var, (field_name, coerce) in _ENV_MAP.items():
            raw_val = os.environ.get(env_var)
            if raw_val is not None:
                value = coerce(raw_val)
                if field_name == "hostname" and value == "":
                    continue
                merged[field_name] = value

        for key, value in overrides.items():
            if key in valid_fields:
                merged[key] = value

        # Load tokens from [auths.<fqdn>] unless explicitly overridden via kwargs
        # If either token was explicitly overridden, skip file-based token loading entirely.
        if "access_token" not in merged and "refresh_token" not in merged:
            hostname_val = str(merged.get("hostname", next(f.default for f in fields(cls) if f.name == "hostname")))
            fqdn_key = _hostname_to_fqdn_key(hostname_val)
            if fqdn_key:
                entry = raw.get("auths", {}).get(fqdn_key)
                if isinstance(entry, dict):
                    acc = entry.get("access_token")
                    ref = entry.get("refresh_token")
                    if acc is not None or ref is not None:
                        if acc is not None and not isinstance(acc, str):
                            raise ConfigError(f"Malformed token data in [auths.{fqdn_key}]")
                        if not isinstance(ref, str):
                            raise ConfigError(f"Malformed token data in [auths.{fqdn_key}]")
                        merged["access_token"] = acc
                        merged["refresh_token"] = ref

        return cls(**merged)  # type: ignore[arg-type]

    def _read_toml(self) -> dict:
        return DepositConfig._load_toml_file(self.config_path)

    def _write_toml(self, data: dict) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".toml.tmp")
        try:
            tmp.write_text(tomli_w.dumps(data), encoding="utf-8")
            os.replace(tmp, self.config_path)
        except ConfigError:
            tmp.unlink(missing_ok=True)
            raise
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise ConfigError(f"Failed to write {self.config_path}: {exc}") from exc

    def read_auth_entry(self, key: str) -> dict | None:
        data = self._read_toml()
        auths = data.get("auths", {})
        if not isinstance(auths, dict):
            raise ConfigError("Malformed [auths] section in config.toml")
        entry = auths.get(key)
        if entry is None:
            return None
        if not isinstance(entry, dict):
            raise ConfigError(f"Malformed [auths.{key}] entry in config.toml")
        return entry

    def write_auth_entry(self, key: str, entry: dict) -> None:
        data = self._read_toml()
        auths = data.setdefault("auths", {})
        if not isinstance(auths, dict):
            raise ConfigError("Malformed [auths] section in config.toml")
        auths[key] = entry
        self._write_toml(data)

    def delete_auth_entry(self, key: str) -> None:
        data = self._read_toml()
        auths = data.get("auths")
        if not isinstance(auths, dict) or key not in auths:
            return
        del auths[key]
        self._write_toml(data)
