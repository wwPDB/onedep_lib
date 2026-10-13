from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from onedep_lib.validation.models import ValidationMetadata


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
    block_index: int | None = None
    table_index: int | None = None
    loop_column: int | None = None


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
    check_type: str | None = None
    rule_id: str | None = None
    related_locations: tuple[CifLocation, ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context", freeze(self.context))
        object.__setattr__(self, "related_locations", tuple(self.related_locations))
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
    validation: ValidationMetadata | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-safe representation; unsupported values raise TypeError."""
        return json_value(self)

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize the report without permitting nonfinite numbers or arbitrary reprs."""
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, indent=indent)

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


def freeze(value: Any) -> Any:
    """Defensively freeze nested validation context without altering old report fields."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(v) for v in value)
    return value


def json_value(value: Any) -> Any:
    """Convert explicitly supported report values, never arbitrary object reprs."""
    if isinstance(value, Enum):
        return json_value(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("Nonfinite numbers are not JSON report values")
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise TypeError("JSON report mapping keys must be strings")
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    raise TypeError(f"Unsupported JSON report value of type {type(value).__name__}")
