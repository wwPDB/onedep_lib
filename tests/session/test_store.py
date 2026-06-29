from __future__ import annotations

from datetime import datetime, timezone

import pytest

from onedep_lib.enums import Country, ExperimentType, FileType
from onedep_lib.session.models import LocalFile, LocalSession
from onedep_lib.session.json_store import JsonSessionStore


def _make_session(session_id: str = "sess-1") -> LocalSession:
    return LocalSession(
        session_id=session_id,
        email="user@example.com",
        users=["0000-0001-2345-6789", "0000-0002-3456-7890"],
        country=Country.UK,
        experiment_type=ExperimentType.XRAY,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
    )


def test_store_creates_db_file(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    assert store.json_path.exists()
    store.close()


def test_create_and_get_session(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    store.create_session(session)

    result = store.get_session()
    assert result.session_id == "sess-1"
    assert result.email == "user@example.com"
    assert result.users == ["0000-0001-2345-6789", "0000-0002-3456-7890"]
    assert result.country == Country.UK
    assert result.experiment_type == ExperimentType.XRAY
    assert result.remote_dep_id is None
    assert result.site_url is None
    store.close()


def test_session_with_no_experiment_type(tmp_path):
    store = JsonSessionStore("sess-2", base_dir=tmp_path)
    session = LocalSession(
        session_id="sess-2",
        email="x@x.com",
        users=[],
        country=Country.USA,
        experiment_type=None,
        created_at=datetime(2026, 1, 1),
    )
    store.create_session(session)
    result = store.get_session()
    assert result.experiment_type is None
    store.close()


def test_update_experiment_type(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    session.experiment_type = None
    store.create_session(session)

    store.update_experiment_type(ExperimentType.EM)
    result = store.get_session()
    assert result.experiment_type == ExperimentType.EM
    store.close()


def test_set_remote_dep_id(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    store.create_session(session)

    store.set_remote_dep_id("D_8000000001", site_url="https://deposit.wwpdb.org/D_8000000001")
    result = store.get_session()
    assert result.remote_dep_id == "D_8000000001"
    assert result.site_url == "https://deposit.wwpdb.org/D_8000000001"
    store.close()


def test_add_and_get_file(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    store.create_session(session)

    f = LocalFile(
        file_id="file-abc",
        session_id="sess-1",
        file_path="/data/model.cif",
        file_type=FileType.MMCIF_COORD,
    )
    store.add_file(f)
    result = store.get_file("file-abc")
    assert result.file_id == "file-abc"
    assert result.file_path == "/data/model.cif"
    assert result.file_type == FileType.MMCIF_COORD
    store.close()


def test_get_file_raises_for_unknown_id(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    store.create_session(session)

    with pytest.raises(KeyError):
        store.get_file("nonexistent")
    store.close()


def test_remove_file(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    store.create_session(session)

    f = LocalFile(file_id="f1", session_id="sess-1", file_path="/a.cif", file_type=FileType.MMCIF_COORD)
    store.add_file(f)
    store.remove_file("f1")

    with pytest.raises(KeyError):
        store.get_file("f1")
    store.close()


def test_get_all_files(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    store.create_session(session)

    store.add_file(LocalFile("f1", "sess-1", "/a.cif", FileType.MMCIF_COORD))
    store.add_file(LocalFile("f2", "sess-1", "/b.mtz", FileType.CRYSTAL_MTZ))
    files = store.get_all_files()
    assert len(files) == 2
    assert {f.file_id for f in files} == {"f1", "f2"}
    store.close()


def test_context_manager_closes_connection(tmp_path):
    with JsonSessionStore("sess-cm", base_dir=tmp_path) as store:
        session = _make_session("sess-cm")
        store.create_session(session)
    # After exiting the context, store should be closed
    # Re-opening should work fine (proves the file was properly closed)
    with JsonSessionStore("sess-cm", base_dir=tmp_path) as store2:
        result = store2.get_session()
        assert result.session_id == "sess-cm"


def test_remove_file_raises_for_unknown_id(tmp_path):
    with JsonSessionStore("sess-1", base_dir=tmp_path) as store:
        session = _make_session()
        store.create_session(session)
        with pytest.raises(KeyError):
            store.remove_file("nonexistent-id")


def test_get_session_raises_key_error_on_empty_db(tmp_path):
    store = JsonSessionStore("sess-empty", base_dir=tmp_path)
    with pytest.raises(KeyError):
        store.get_session()
    store.close()


def test_set_voxel_values_persists_and_round_trips(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    store.create_session(session)

    f = LocalFile(file_id="file-v1", session_id="sess-1", file_path="/data/map.mrc", file_type=FileType.EM_MAP)
    store.add_file(f)
    store.set_voxel_values("file-v1", spacing_x=1.1, spacing_y=2.2, spacing_z=3.3, contour=0.5)

    result = store.get_file("file-v1")
    assert result.voxel == {"spacing_x": 1.1, "spacing_y": 2.2, "spacing_z": 3.3, "contour": 0.5}
    store.close()


def test_add_file_raises_for_mismatched_session_id(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    store.create_session(session)

    with pytest.raises(ValueError):
        store.add_file(
            LocalFile(
                file_id="f-mismatch",
                session_id="wrong-session",
                file_path="/data/file.cif",
                file_type=FileType.MMCIF_COORD,
            )
        )
    store.close()


def test_add_file_stores_md5_and_mtime(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    store.create_session(session)

    f = LocalFile(
        file_id="file-m1",
        session_id="sess-1",
        file_path="/data/model.cif",
        file_type=FileType.MMCIF_COORD,
        md5="abcdef1234567890abcdef1234567890",
        file_mtime=datetime(2026, 4, 10, 9, 12, 0, tzinfo=timezone.utc),
    )
    store.add_file(f)
    result = store.get_file("file-m1")
    assert result.md5 == "abcdef1234567890abcdef1234567890"
    assert result.file_mtime == datetime(2026, 4, 10, 9, 12, 0, tzinfo=timezone.utc)
    store.close()


def test_add_file_without_md5_mtime_returns_none(tmp_path):
    store = JsonSessionStore("sess-1", base_dir=tmp_path)
    session = _make_session()
    store.create_session(session)

    f = LocalFile(
        file_id="file-legacy",
        session_id="sess-1",
        file_path="/data/model.cif",
        file_type=FileType.MMCIF_COORD,
    )
    store.add_file(f)
    result = store.get_file("file-legacy")
    assert result.md5 is None
    assert result.file_mtime is None
    store.close()
