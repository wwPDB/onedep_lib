from __future__ import annotations

from typing import Protocol


class SchemaProvider(Protocol):
    def get_schema(self, schema_name: str) -> dict: ...
