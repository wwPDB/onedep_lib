"""Integration test: real JsonSessionStore + real RemoteSchemaProvider."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from onedep_lib.checks.runner import CheckRunner
from onedep_lib.config import DepositConfig
from onedep_lib.enums import Country, ExperimentType, FileType
from onedep_lib.schemas.remote import RemoteSchemaProvider
from onedep_lib.session.json_store import JsonSessionStore
from onedep_lib.session.models import LocalFile, LocalSession


@pytest.fixture
def served_schemas() -> dict[str, dict]:
    """Every required-files schema the runner will ask the remote provider for."""
    config = DepositConfig()
    schema_dir = config.local_schema_cache_dir / config.required_files_subfolder
    names = [config.required_files_schema, *config.required_files_subschemas]
    return {name: json.loads((schema_dir / f"{name}.json").read_text()) for name in names}


def test_full_session_create_add_check(tmp_path: Path, httpserver, served_schemas: dict[str, dict]):
    for name, schema in served_schemas.items():
        httpserver.expect_request(f"/{name}.json").respond_with_json(schema)

    store = JsonSessionStore("integ-session", base_dir=tmp_path / "sessions")
    session = LocalSession(
        session_id="integ-session",
        email="user@lab.org",
        users=["0000-0002-5109-8728"],
        country=Country.USA,
        experiment_type=ExperimentType.XRAY,
        created_at=datetime.now(),
    )
    store.create_session(session)

    f1 = LocalFile(
        file_id="f1",
        session_id="integ-session",
        file_path="/tmp/model.cif",
        file_type=FileType.MMCIF_COORD,
        file_mtime=datetime.now(tz=timezone.utc),
    )
    f2 = LocalFile(
        file_id="f2",
        session_id="integ-session",
        file_path="/tmp/data.cif",
        file_type=FileType.CRYSTAL_STRUC_FACTORS,
        file_mtime=datetime.now(tz=timezone.utc),
    )
    store.add_file(f1)
    store.add_file(f2)

    provider = RemoteSchemaProvider(httpserver.url_for("/"), cache_dir=tmp_path / "schemas")
    runner = CheckRunner(schema_provider=provider)

    files = store.get_all_files()
    loaded = store.get_session()
    report = runner.check_required_files(files, loaded.experiment_type)
    assert report.issues == []
    assert report.ok is True
