import json
from pathlib import Path

import pytest

from onedep_lib.checks.report import CheckSeverity
from onedep_lib.checks.runner import CheckRunner
from onedep_lib.enums import EMSubType, ExperimentType, FileType
from onedep_lib.exceptions import SchemaError
from onedep_lib.session.models import LocalFile
from onedep_lib.config import DepositConfig


class StubSchemaProvider:
    def __init__(self, schemas: dict[str, dict]) -> None:
        self._schemas = schemas

    def get_schema(self, schema_name: str) -> dict:
        if schema_name not in self._schemas:
            raise SchemaError(f"Schema '{schema_name}' not available")
        return self._schemas[schema_name]


def _load_files_schema() -> dict:
    schema_path = DepositConfig().local_schema_cache_dir / "required_files.json"
    with schema_path.open() as f:
        return json.load(f)


def _load_subschema(name: str) -> dict:
    schema_path = DepositConfig().local_schema_cache_dir / f"{name}.json"
    with schema_path.open() as f:
        return json.load(f)


@pytest.fixture
def runner_with_files_schema() -> CheckRunner:
    provider = StubSchemaProvider({
        "required_files": _load_files_schema(),
        **{name: _load_subschema(name) for name in CheckRunner.subschemas},
    })
    return CheckRunner(schema_provider=provider)


@pytest.fixture
def runner_no_schema() -> CheckRunner:
    provider = StubSchemaProvider({})
    return CheckRunner(schema_provider=provider)


def _make_file(file_type: FileType) -> LocalFile:
    return LocalFile(
        file_id="f1",
        session_id="s1",
        file_path="/tmp/file",
        file_type=file_type,
    )


def test_returns_warning_when_experiment_type_unset(runner_with_files_schema: CheckRunner):
    report = runner_with_files_schema.check_required_files([], None)
    assert report.ok is True
    assert any(i.code == "EXPERIMENT_TYPE_UNSET" for i in report.warnings())


def test_returns_warning_when_schema_unavailable(runner_no_schema: CheckRunner):
    report = runner_no_schema.check_required_files([], ExperimentType.XRAY)
    assert report.ok is True
    assert any(i.code == "SCHEMA_UNAVAILABLE" for i in report.warnings())


def test_xray_passes_with_correct_files(runner_with_files_schema: CheckRunner):
    files = [
        LocalFile("f1", "s1", "/tmp/model.cif", FileType.MMCIF_COORD),
        LocalFile("f2", "s1", "/tmp/data.cif", FileType.CRYSTAL_STRUC_FACTORS),
    ]
    report = runner_with_files_schema.check_required_files(files, ExperimentType.XRAY)
    assert report.ok is True


def test_xray_fails_without_coordinate_file(runner_with_files_schema: CheckRunner):
    files = [LocalFile("f1", "s1", "/tmp/data.cif", FileType.CRYSTAL_STRUC_FACTORS)]
    report = runner_with_files_schema.check_required_files(files, ExperimentType.XRAY)
    print(report)
    assert report.ok is False
    # assert any("coordinate" in i.message.lower() for i in report.errors())


def test_xray_fails_without_structure_factors(runner_with_files_schema: CheckRunner):
    files = [LocalFile("f1", "s1", "/tmp/model.cif", FileType.MMCIF_COORD)]
    report = runner_with_files_schema.check_required_files(files, ExperimentType.XRAY)
    print(report)
    assert report.ok is False
    # assert any("structure factor" in i.message.lower() for i in report.errors())


def test_em_spa_passes_with_correct_files(runner_with_files_schema: CheckRunner):
    files = [
        LocalFile("f1", "s1", "/tmp/model.cif", FileType.MMCIF_COORD),
        LocalFile("f2", "s1", "/tmp/map.map", FileType.EM_MAP),
        LocalFile("f3", "s1", "/tmp/img.png", FileType.ENTRY_IMAGE),
        LocalFile("f4", "s1", "/tmp/h1.map", FileType.EM_HALF_MAP),
        LocalFile("f5", "s1", "/tmp/h2.map", FileType.EM_HALF_MAP),
    ]
    report = runner_with_files_schema.check_required_files(files, ExperimentType.EM, em_subtype=EMSubType.SPA)
    assert report.ok is True


def test_em_fails_without_subtype(runner_with_files_schema: CheckRunner):
    files = [LocalFile("f1", "s1", "/tmp/map.map", FileType.EM_MAP)]
    report = runner_with_files_schema.check_required_files(files, ExperimentType.EM)
    assert report.ok is False


def test_check_mmcif_file_returns_info_when_no_schema(runner_no_schema: CheckRunner):
    file = LocalFile("f1", "s1", "/tmp/model.cif", FileType.MMCIF_COORD)
    report = runner_no_schema.check_mmcif_file(file)
    assert report.ok is True
    assert any(i.severity == CheckSeverity.INFO for i in report.issues)


def test_check_mmcif_category_returns_info_when_no_schema(runner_no_schema: CheckRunner):
    file = LocalFile("f1", "s1", "/tmp/model.cif", FileType.MMCIF_COORD)
    report = runner_no_schema.check_mmcif_category(file, "_atom_site")
    assert report.ok is True


def test_check_mmcif_field_returns_info_when_no_schema(runner_no_schema: CheckRunner):
    file = LocalFile("f1", "s1", "/tmp/model.cif", FileType.MMCIF_COORD)
    report = runner_no_schema.check_mmcif_field(file, "_atom_site", "Cartn_x")
    assert report.ok is True


def test_check_file_type_returns_info_when_no_schema(runner_no_schema: CheckRunner):
    file = LocalFile("f1", "s1", "/tmp/model.cif", FileType.MMCIF_COORD)
    report = runner_no_schema.check_file_type(file, FileType.MMCIF_COORD)
    assert report.ok is True
