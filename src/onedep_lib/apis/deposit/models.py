from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Union

from onedep_lib.apis.deposit.enums import Status
from onedep_lib.enums import EMSubType, ExperimentType, FileType


@dataclass
class Experiment:
    exp_type: ExperimentType
    coordinates: bool = True
    subtype: EMSubType | None = None
    related_emdb: str | None = None
    related_bmrb: str | None = None
    sf_only: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.exp_type, str):
            self.exp_type = ExperimentType(self.exp_type)
        if isinstance(self.subtype, str):
            self.subtype = EMSubType(self.subtype)
        self.coordinates = bool(self.coordinates)
        self.sf_only = bool(self.sf_only)
        if self.related_emdb is not None:
            self.related_emdb = str(self.related_emdb)
        if self.related_bmrb is not None:
            self.related_bmrb = str(self.related_bmrb)

    def to_dict(self) -> dict:
        out: dict = {"type": self.exp_type.value, "coordinates": self.coordinates}
        if self.subtype:
            out["subtype"] = self.subtype.value
        if self.related_emdb:
            out["related_emdb"] = self.related_emdb
        if self.related_bmrb:
            out["related_bmrb"] = self.related_bmrb
        if self.sf_only:
            out["sf_only"] = self.sf_only
        return out


@dataclass
class DepositError:
    code: str
    message: str
    extras: str | None = None

    def __post_init__(self) -> None:
        self.code = str(self.code)
        self.message = str(self.message)


@dataclass
class PixelSpacing:
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        self.x = float(self.x)
        self.y = float(self.y)
        self.z = float(self.z)


@dataclass
class EmVoxel:
    spacing: PixelSpacing
    contour: float

    def __post_init__(self) -> None:
        if isinstance(self.spacing, dict):
            self.spacing = PixelSpacing(**self.spacing)
        self.contour = float(self.contour)


@dataclass
class EmMapMetadata:
    voxel: EmVoxel
    description: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.voxel, dict):
            self.voxel = EmVoxel(**self.voxel)
        self.description = str(self.description)


@dataclass
class WwPDBDeposition:
    dep_id: str
    email: str
    pdb_id: str | None
    emdb_id: str | None
    bmrb_id: str | None
    title: str
    hold_exp_date: str | None
    created: datetime
    last_login: datetime
    site: str
    status: Status
    experiments: list[Experiment] = field(default_factory=list)
    errors: list[DepositError] = field(default_factory=list)
    site_url: str | None = None

    def __post_init__(self) -> None:
        self.dep_id = str(self.dep_id)
        self.email = str(self.email)
        self.pdb_id = str(self.pdb_id) if self.pdb_id and self.pdb_id != "?" else None
        self.emdb_id = str(self.emdb_id) if self.emdb_id and self.emdb_id != "?" else None
        self.bmrb_id = str(self.bmrb_id) if self.bmrb_id and self.bmrb_id != "?" else None
        self.title = str(self.title)
        if isinstance(self.created, str):
            self.created = datetime.fromisoformat(self.created)
        if isinstance(self.last_login, str):
            self.last_login = datetime.fromisoformat(self.last_login)
        if isinstance(self.status, str):
            self.status = Status[self.status]
        parsed_experiments = []
        for exp in self.experiments:
            if isinstance(exp, dict):
                exp = dict(exp)
                if "type" in exp:
                    exp["exp_type"] = exp.pop("type")
                parsed_experiments.append(Experiment(**exp))
            else:
                parsed_experiments.append(exp)
        self.experiments = parsed_experiments
        self.errors = [DepositError(**e) if isinstance(e, dict) else e for e in self.errors]


@dataclass
class DepositedFile:
    file_id: int
    name: str
    file_type: FileType
    created: datetime
    metadata: EmMapMetadata | None = None
    uploadedBytes: int = 0
    errors: list[DepositError] = field(default_factory=list)
    warnings: list[DepositError] = field(default_factory=list)

    _DATE_FORMAT: ClassVar[str] = "%A, %B %d, %Y %H:%M:%S"

    def __post_init__(self) -> None:
        self.file_id = int(self.file_id)
        self.name = str(self.name)
        if isinstance(self.file_type, str):
            self.file_type = FileType(self.file_type)
        if isinstance(self.created, str):
            self.created = datetime.strptime(self.created, self._DATE_FORMAT)
        if isinstance(self.metadata, dict):
            self.metadata = EmMapMetadata(**self.metadata)
        self.errors = [DepositError(**e) if isinstance(e, dict) else e for e in self.errors if e != ""]
        self.warnings = [DepositError(**w) if isinstance(w, dict) else w for w in self.warnings if w != ""]


@dataclass
class DepositStatus:
    status: str
    action: str
    step: str
    details: str
    date: datetime

    def __post_init__(self) -> None:
        self.status = str(self.status)
        self.action = str(self.action)
        self.step = str(self.step)
        self.details = str(self.details)
        if isinstance(self.date, str):
            self.date = datetime.fromisoformat(self.date)
