import dataclasses
from datetime import datetime

from onedep_lib.enums import Country, ExperimentType, FileType
from onedep_lib.session.models import LocalFile, LocalSession


def test_local_session_fields():
    session = LocalSession(
        session_id="abc-123",
        email="user@lab.org",
        users=["0000-0002-5109-8728"],
        country=Country.USA,
        experiment_type=ExperimentType.XRAY,
        created_at=datetime.now(),
    )
    assert session.session_id == "abc-123"
    assert session.remote_dep_id is None
    assert session.site_url is None
    assert session.em_subtype is None
    assert session.coordinates is None


def test_local_session_has_no_db_path():
    field_names = {f.name for f in dataclasses.fields(LocalSession)}
    assert "db_path" not in field_names


def test_local_file_fields():
    f = LocalFile(
        file_id="f1",
        session_id="abc-123",
        file_path="/tmp/model.cif",
        file_type=FileType.MMCIF_COORD,
    )
    assert f.file_id == "f1"
    assert f.voxel is None
    assert f.md5 is None
