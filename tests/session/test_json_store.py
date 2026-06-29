from datetime import datetime, timezone
from pathlib import Path

import pytest

from onedep_lib.enums import Country, ExperimentType, FileType
from onedep_lib.session.json_store import JsonSessionStore
from onedep_lib.session.models import LocalFile, LocalSession


@pytest.fixture
def store(tmp_path: Path) -> JsonSessionStore:
    return JsonSessionStore("test-session", base_dir=tmp_path)


@pytest.fixture
def session() -> LocalSession:
    return LocalSession(
        session_id="test-session",
        email="user@lab.org",
        users=["0000-0002-5109-8728"],
        country=Country.USA,
        experiment_type=ExperimentType.XRAY,
        created_at=datetime.now(),
    )


def test_create_and_get_session(store: JsonSessionStore, session: LocalSession):
    store.create_session(session)
    loaded = store.get_session()
    assert loaded.session_id == session.session_id
    assert loaded.email == session.email
    assert loaded.country == Country.USA
    assert loaded.experiment_type == ExperimentType.XRAY


def test_get_session_raises_when_no_session(store: JsonSessionStore):
    with pytest.raises(KeyError):
        store.get_session()


def test_add_and_get_file(store: JsonSessionStore, session: LocalSession):
    store.create_session(session)
    f = LocalFile(
        file_id="f1",
        session_id="test-session",
        file_path="/tmp/model.cif",
        file_type=FileType.MMCIF_COORD,
        md5="abc123",
        file_mtime=datetime.now(tz=timezone.utc),
    )
    store.add_file(f)
    loaded = store.get_file("f1")
    assert loaded.file_id == "f1"
    assert loaded.file_type == FileType.MMCIF_COORD


def test_remove_file(store: JsonSessionStore, session: LocalSession):
    store.create_session(session)
    f = LocalFile(
        file_id="f1",
        session_id="test-session",
        file_path="/tmp/model.cif",
        file_type=FileType.MMCIF_COORD,
        file_mtime=datetime.now(tz=timezone.utc),
    )
    store.add_file(f)
    store.remove_file("f1")
    with pytest.raises(KeyError):
        store.get_file("f1")


def test_set_voxel_values(store: JsonSessionStore, session: LocalSession):
    store.create_session(session)
    f = LocalFile(
        file_id="f1",
        session_id="test-session",
        file_path="/tmp/map.map",
        file_type=FileType.EM_MAP,
        file_mtime=datetime.now(tz=timezone.utc),
    )
    store.add_file(f)
    store.set_voxel_values("f1", 1.08, 1.08, 1.08, 0.01)
    loaded = store.get_file("f1")
    assert loaded.voxel == {
        "spacing_x": 1.08,
        "spacing_y": 1.08,
        "spacing_z": 1.08,
        "contour": 0.01,
    }


def test_update_experiment_type(store: JsonSessionStore, session: LocalSession):
    store.create_session(session)
    store.update_experiment_type(ExperimentType.EM)
    loaded = store.get_session()
    assert loaded.experiment_type == ExperimentType.EM


def test_set_remote_dep_id(store: JsonSessionStore, session: LocalSession):
    store.create_session(session)
    store.set_remote_dep_id("D_8000000001", site_url="https://deposit.wwpdb.org/D_8000000001")
    loaded = store.get_session()
    assert loaded.remote_dep_id == "D_8000000001"
    assert loaded.site_url == "https://deposit.wwpdb.org/D_8000000001"


def test_persists_across_store_instances(tmp_path: Path, session: LocalSession):
    store1 = JsonSessionStore("test-session", base_dir=tmp_path)
    store1.create_session(session)
    store1.close()

    store2 = JsonSessionStore("test-session", base_dir=tmp_path)
    loaded = store2.get_session()
    assert loaded.email == session.email


def test_uses_configured_session_dir_when_base_dir_omitted(monkeypatch, tmp_path: Path):
    session_dir = tmp_path / "configured-sessions"
    config_dir = tmp_path / ".config" / "onedep"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(f'[default]\nsession_dir = "{session_dir}"\n')
    monkeypatch.setenv("HOME", str(tmp_path))

    store = JsonSessionStore("configured-session")

    assert store.json_path == session_dir / "configured-session" / "session.json"
