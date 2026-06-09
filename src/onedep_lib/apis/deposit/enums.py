import enum


class Status(enum.Enum):
    # Values are never used for parsing; the API sends names (e.g. "DEP").
    # Parse with Status["DEP"], not Status("1").
    DEP = enum.auto()
    PROC = enum.auto()
    AUTH = enum.auto()
    REPL = enum.auto()
    AUCO = enum.auto()
    AUXS = enum.auto()
    AUXU = enum.auto()
    HOLD = enum.auto()
    HPUB = enum.auto()
    OBS = enum.auto()
    POLC = enum.auto()
    REL = enum.auto()
    REUP = enum.auto()
    WAIT = enum.auto()
    WDRN = enum.auto()
