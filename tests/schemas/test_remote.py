import json
from pathlib import Path

import pytest

from onedep_lib.exceptions import SchemaError
from onedep_lib.schemas.remote import RemoteSchemaProvider

SAMPLE_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}


def test_fetches_schema_from_server(httpserver, tmp_path: Path):
    httpserver.expect_request("/required_files.json").respond_with_json(SAMPLE_SCHEMA)
    provider = RemoteSchemaProvider(httpserver.url_for("/"), cache_dir=tmp_path)
    schema = provider.get_schema("required_files")
    assert schema == SAMPLE_SCHEMA


def test_caches_schema_to_disk(httpserver, tmp_path: Path):
    httpserver.expect_request("/required_files.json").respond_with_json(SAMPLE_SCHEMA)
    provider = RemoteSchemaProvider(httpserver.url_for("/"), cache_dir=tmp_path)
    provider.get_schema("required_files")
    cache_file = tmp_path / "required_files.json"
    assert cache_file.exists()
    assert json.loads(cache_file.read_text()) == SAMPLE_SCHEMA


def test_serves_from_cache_without_network(tmp_path: Path):
    cache_file = tmp_path / "required_files.json"
    cache_file.write_text(json.dumps(SAMPLE_SCHEMA))
    provider = RemoteSchemaProvider("http://unreachable.invalid/", cache_dir=tmp_path)
    schema = provider.get_schema("required_files")
    assert schema == SAMPLE_SCHEMA


def test_raises_schema_error_on_network_failure(tmp_path: Path):
    provider = RemoteSchemaProvider("http://unreachable.invalid/", cache_dir=tmp_path)
    with pytest.raises(SchemaError, match="required_files"):
        provider.get_schema("required_files")


def test_raises_schema_error_on_404(httpserver, tmp_path: Path):
    httpserver.expect_request("/missing.json").respond_with_data("Not Found", status=404)
    provider = RemoteSchemaProvider(httpserver.url_for("/"), cache_dir=tmp_path)
    with pytest.raises(SchemaError):
        provider.get_schema("missing")
