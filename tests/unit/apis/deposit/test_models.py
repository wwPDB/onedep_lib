from datetime import datetime
import pytest
from onedep_lib.apis.deposit.models import (
    WwPDBDeposition, DepositError, DepositedFile, DepositStatus, Experiment, PixelSpacing,
)
from onedep_lib.apis.deposit.enums import Status
from onedep_lib.enums import ExperimentType, FileType


def _deposit(**overrides) -> WwPDBDeposition:
    defaults = dict(
        dep_id="D_1", email="a@b.com",
        pdb_id="?", emdb_id="?", bmrb_id="?",
        title="T", hold_exp_date=None,
        created="2024-01-01T00:00:00",
        last_login="2024-01-01T00:00:00",
        site="pdbe", status="DEP",
    )
    return WwPDBDeposition(**{**defaults, **overrides})


def test_deposit_normalises_question_mark_ids():
    d = _deposit()
    assert d.pdb_id is None
    assert d.emdb_id is None
    assert d.bmrb_id is None


def test_deposit_parses_status_by_name():
    d = _deposit(status="PROC")
    assert d.status is Status.PROC


def test_deposit_parses_created_datetime():
    d = _deposit(created="2024-06-15T10:30:00")
    assert d.created == datetime(2024, 6, 15, 10, 30, 0)


def test_deposit_parses_nested_experiments():
    d = _deposit(experiments=[{"type": "xray", "coordinates": True}])
    assert len(d.experiments) == 1
    assert d.experiments[0].exp_type is ExperimentType.XRAY


def test_experiment_coerces_string_type():
    exp = Experiment(exp_type="xray")
    assert exp.exp_type is ExperimentType.XRAY


def test_deposited_file_parses_custom_date_format():
    f = DepositedFile(
        file_id=1, name="f.cif",
        file_type="co-cif",
        created="Monday, January 01, 2024 12:00:00",
    )
    assert f.file_id == 1
    assert f.file_type is FileType.MMCIF_COORD
    assert f.created == datetime(2024, 1, 1, 12, 0, 0)


def test_deposit_status_parses_iso_date():
    s = DepositStatus(
        status="DEP", action="deposit", step="1",
        details="deposited", date="2024-01-01T00:00:00",
    )
    assert s.date == datetime(2024, 1, 1)


def test_deposit_error_coerces_strings():
    e = DepositError(code=42, message=99)
    assert e.code == "42"
    assert e.message == "99"
