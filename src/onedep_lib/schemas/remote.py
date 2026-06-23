from __future__ import annotations

import json
from pathlib import Path

import requests

from onedep_lib.exceptions import SchemaError


class RemoteSchemaProvider:
    def __init__(self, base_url: str, cache_dir: Path) -> None:
        self._base_url = base_url.rstrip("/")
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get_schema(self, schema_name: str) -> dict:
        cache_path = self._cache_dir / f"{schema_name}.json"
        if cache_path.exists():
            with cache_path.open() as f:
                return json.load(f)
        return self._fetch_and_cache(schema_name, cache_path)

    def _fetch_and_cache(self, schema_name: str, cache_path: Path) -> dict:
        url = f"{self._base_url}/{schema_name}.json"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SchemaError(f"Failed to fetch schema '{schema_name}' from {url}: {exc}") from exc
        try:
            schema = response.json()
        except ValueError as exc:
            raise SchemaError(f"Invalid JSON in schema '{schema_name}' from {url}") from exc
        with cache_path.open("w") as f:
            json.dump(schema, f)
        return schema
