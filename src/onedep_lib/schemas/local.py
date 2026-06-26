from __future__ import annotations

import json
from pathlib import Path

from onedep_lib.exceptions import SchemaError


class LocalSchemaProvider:
    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir

    def get_schema(self, schema_name: str) -> dict:
        cache_path = self._cache_dir / f"{schema_name}.json"
        if cache_path.exists():
            try:
                with cache_path.open() as f:
                    return json.load(f)
            except (ValueError, OSError) as exc:
                raise SchemaError(
                    f"Schema '{schema_name}' is corrupted or unreadable: {exc}"
                ) from exc
        raise SchemaError(f"Schema '{schema_name}' not available")
