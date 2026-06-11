from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from onedep_lib.enums import Country, EMSubType, ExperimentType, FileType


@dataclass
class LocalFile:
    file_id: str
    session_id: str
    file_path: str
    file_type: FileType
    voxel: dict | None = None
    md5: str | None = None
    file_mtime: datetime | None = None


@dataclass
class LocalSession:
    session_id: str
    email: str
    users: list[str]
    country: Country
    experiment_type: ExperimentType | None
    created_at: datetime
    remote_dep_id: str | None = None
    em_subtype: EMSubType | None = None
    coordinates: bool | None = None
