from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from onedep_lib.config import DepositConfig
from onedep_lib.enums import Country, EMSubType, ExperimentType, FileType
from onedep_lib.session.models import LocalFile, LocalSession


class JsonSessionStore:
    def __init__(self, session_id: str, base_dir: Path | None = None) -> None:
        _base = base_dir or Path(DepositConfig.load().session_dir)
        self._session_id = session_id
        self._json_path = _base / session_id / "session.json"
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        tmp = self._json_path.with_suffix(".json.tmp")
        if tmp.exists():
            tmp.unlink()

        if self._json_path.exists():
            with self._json_path.open() as f:
                self._data: dict = json.load(f)
        else:
            self._data = {"session": None, "files": {}}
            self._save()

    @property
    def json_path(self) -> Path:
        return self._json_path

    def _save(self) -> None:
        tmp = self._json_path.with_suffix(".json.tmp")
        try:
            with tmp.open("w") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._json_path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _require_session(self) -> dict:
        session = self._data["session"]
        if session is None:
            raise RuntimeError(f"No session initialised for {self._session_id!r}. Call create_session() first.")
        return session

    def create_session(self, session: LocalSession) -> None:
        self._data["session"] = {
            "session_id": session.session_id,
            "email": session.email,
            "users": session.users,
            "country": session.country.value,
            "experiment_type": session.experiment_type.value if session.experiment_type else None,
            "created_at": session.created_at.isoformat(),
            "remote_dep_id": session.remote_dep_id,
            "site_url": session.site_url,
            "em_subtype": session.em_subtype.value if session.em_subtype else None,
            "coordinates": session.coordinates,
        }
        self._save()

    def get_session(self) -> LocalSession:
        session = self._data["session"]
        if session is None:
            raise KeyError(f"No session found for {self._session_id!r}")
        return LocalSession(
            session_id=session["session_id"],
            email=session["email"],
            users=session["users"],
            country=Country(session["country"]),
            experiment_type=ExperimentType(session["experiment_type"]) if session["experiment_type"] else None,
            created_at=datetime.fromisoformat(session["created_at"]),
            remote_dep_id=session.get("remote_dep_id"),
            site_url=session.get("site_url"),
            em_subtype=EMSubType(session.get("em_subtype")) if session.get("em_subtype") else None,
            coordinates=session.get("coordinates"),
        )

    def update_experiment_type(self, experiment_type: ExperimentType) -> None:
        session = self._require_session()
        session["experiment_type"] = experiment_type.value
        self._save()

    def update_em_params(self, em_subtype: EMSubType | None, coordinates: bool | None) -> None:
        session = self._require_session()
        session["em_subtype"] = em_subtype.value if em_subtype else None
        session["coordinates"] = coordinates
        self._save()

    def set_remote_dep_id(self, dep_id: str, site_url: str | None = None) -> None:
        session = self._require_session()
        session["remote_dep_id"] = dep_id
        session["site_url"] = site_url
        self._save()

    def add_file(self, file: LocalFile) -> None:
        if file.session_id != self._session_id:
            raise ValueError(
                f"File session_id {file.session_id!r} does not match store session_id {self._session_id!r}"
            )
        if file.file_mtime is not None and file.file_mtime.tzinfo is None:
            raise ValueError("file_mtime must be timezone-aware")
        self._data["files"][file.file_id] = {
            "file_id": file.file_id,
            "session_id": file.session_id,
            "file_path": file.file_path,
            "file_type": file.file_type.value,
            "voxel": file.voxel,
            "md5": file.md5,
            "file_mtime": file.file_mtime.isoformat() if file.file_mtime else None,
        }
        self._save()

    def set_voxel_values(
        self,
        file_id: str,
        spacing_x: float,
        spacing_y: float,
        spacing_z: float,
        contour: float,
    ) -> None:
        if file_id not in self._data["files"]:
            raise KeyError(f"File {file_id!r} not found in session")
        self._data["files"][file_id]["voxel"] = {
            "spacing_x": spacing_x,
            "spacing_y": spacing_y,
            "spacing_z": spacing_z,
            "contour": contour,
        }
        self._save()

    def remove_file(self, file_id: str) -> None:
        if file_id not in self._data["files"]:
            raise KeyError(f"File {file_id!r} not found in session")
        del self._data["files"][file_id]
        self._save()

    def get_file(self, file_id: str) -> LocalFile:
        entry = self._data["files"].get(file_id)
        if entry is None:
            raise KeyError(f"File {file_id!r} not found in session")
        return LocalFile(
            file_id=entry["file_id"],
            session_id=entry["session_id"],
            file_path=entry["file_path"],
            file_type=FileType(entry["file_type"]),
            voxel=entry.get("voxel"),
            md5=entry.get("md5"),
            file_mtime=datetime.fromisoformat(entry["file_mtime"]) if entry.get("file_mtime") else None,
        )

    def get_all_files(self) -> list[LocalFile]:
        return [
            LocalFile(
                file_id=entry["file_id"],
                session_id=entry["session_id"],
                file_path=entry["file_path"],
                file_type=FileType(entry["file_type"]),
                voxel=entry.get("voxel"),
                md5=entry.get("md5"),
                file_mtime=datetime.fromisoformat(entry["file_mtime"]) if entry.get("file_mtime") else None,
            )
            for entry in self._data["files"].values()
        ]

    def close(self) -> None:
        pass

    def __enter__(self) -> JsonSessionStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
