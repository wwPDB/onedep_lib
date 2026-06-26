import json
from pathlib import Path

import pytest

from onedep_lib.exceptions import SchemaError
from onedep_lib.schemas.local import LocalSchemaProvider

SAMPLE_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}


def test_reads_schema_from_cache(tmp_path: Path):
    (tmp_path / "required_files.json").write_text(json.dumps(SAMPLE_SCHEMA))
    assert LocalSchemaProvider(tmp_path).get_schema("required_files") == SAMPLE_SCHEMA


def test_missing_schema_raises_schema_error(tmp_path: Path):
    with pytest.raises(SchemaError):
        LocalSchemaProvider(tmp_path).get_schema("absent")


def test_corrupted_schema_raises_schema_error(tmp_path: Path):
    # A corrupted cache file must surface as SchemaError (the provider contract),
    # not a raw json.JSONDecodeError - matching RemoteSchemaProvider.
    (tmp_path / "required_files.json").write_text("{ not valid json")
    with pytest.raises(SchemaError):
        LocalSchemaProvider(tmp_path).get_schema("required_files")
