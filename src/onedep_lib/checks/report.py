from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"


@dataclass(frozen=True)
class CifLocation:
    data_block: str | None = None
    category: str | None = None
    item: str | None = None
    row: int | None = None
    line: int | None = None
    column: int | None = None


@dataclass(frozen=True)
class CheckIssue:
    severity: CheckSeverity
    code: str
    message: str
    location: CifLocation = field(default_factory=CifLocation)
    expected: Any = None
    actual: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.severity, CheckSeverity):
            object.__setattr__(self, "severity", CheckSeverity(self.severity))


@dataclass
class CheckReport:
    source: str
    issues: list[CheckIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity in (CheckSeverity.ERROR, CheckSeverity.FATAL) for i in self.issues)

    def errors(self) -> list[CheckIssue]:
        return [i for i in self.issues if i.severity in (CheckSeverity.ERROR, CheckSeverity.FATAL)]

    def warnings(self) -> list[CheckIssue]:
        return [i for i in self.issues if i.severity == CheckSeverity.WARNING]
