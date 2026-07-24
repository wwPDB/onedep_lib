from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckSeverity(str, Enum):
    """Severity level of a single CheckIssue, from least to most severe."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"


@dataclass(frozen=True)
class CifLocation:
    """Location of a check issue within an mmCIF file, when known.

    All fields are optional; any subset may be populated depending on what
    the originating check was able to determine.
    """

    data_block: str | None = None
    category: str | None = None
    item: str | None = None
    row: int | None = None
    line: int | None = None
    column: int | None = None


@dataclass(frozen=True)
class CheckIssue:
    """A single finding produced by a check.

    For example, a missing required file or an invalid mmCIF field.
    """

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
    """Result of running one or more checks against a file or session.

    Attributes:
        source: Identifier of what was checked (a file_id, or "session" for
            session-level checks like check_required_files).
        issues: All issues found, of any severity.
    """

    source: str
    issues: list[CheckIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if issues contains no ERROR or FATAL severity issue."""
        return not any(i.severity in (CheckSeverity.ERROR, CheckSeverity.FATAL) for i in self.issues)

    def errors(self) -> list[CheckIssue]:
        """Return only the issues with ERROR or FATAL severity."""
        return [i for i in self.issues if i.severity in (CheckSeverity.ERROR, CheckSeverity.FATAL)]

    def warnings(self) -> list[CheckIssue]:
        """Return only the issues with WARNING severity."""
        return [i for i in self.issues if i.severity == CheckSeverity.WARNING]
