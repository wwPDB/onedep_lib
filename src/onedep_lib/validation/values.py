"""Raw-token missingness and strict shared scalar conversions."""

from __future__ import annotations

import math
import re
from datetime import datetime

import gemmi

ABSENT = None
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:\([0-9]+\))?(?:[eE][+-]?[0-9]+)?")
_DATE = re.compile(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})(?::([0-9]{1,2}):([0-9]{1,2}))?")


def state(raw: str | None) -> str:
    if raw is None:
        return "absent"
    if raw == "?":
        return "unknown"
    if raw == ".":
        return "not_applicable"
    return "present" if gemmi.cif.as_string(raw).strip() else "empty"


def text(raw: str | None) -> str | None:
    # Inlined rather than built on state(): on hot per-cell paths over million-row
    # tables, a second decode of the same raw token is a measurable cost.
    if raw is None or raw == "?" or raw == ".":
        return None
    value = gemmi.cif.as_string(raw)
    return value if value.strip() else None


def key(raw: str | None) -> str | None:
    value = text(raw)
    return value.strip() if value is not None else None


def parse_number(value: str, *, uncertainty: bool = True) -> float:
    """Parse an already-decoded (`text()`-produced) CIF numeric token."""
    if not _NUMBER.fullmatch(value) or ("(" in value and not uncertainty):
        raise ValueError("Not a finite CIF number")
    cleaned = re.sub(r"\([0-9]+\)", "", value)
    parsed = float(cleaned)
    if not math.isfinite(parsed):
        raise ValueError("Not a finite CIF number")
    return parsed


def number(raw: str | None, *, uncertainty: bool = True) -> float | None:
    value = text(raw)
    if value is None:
        return None
    return parse_number(value.strip(), uncertainty=uncertainty)


def parse_date(value: str) -> datetime:
    """Parse an already-decoded (`text()`-produced) CIF date/time token."""
    match = _DATE.fullmatch(value.strip())
    if match is None:
        raise ValueError("Not a supported CIF date")
    y, m, d, h, minute = match.groups()
    return datetime(int(y), int(m), int(d), int(h or 0), int(minute or 0))


def date(raw: str | None) -> datetime | None:
    value = text(raw)
    if value is None:
        return None
    return parse_date(value)


def render(template: str, **values: object) -> str:
    return re.sub(r"\[\{([A-Za-z_][A-Za-z_0-9]*)\}\]", lambda m: str(values[m.group(1)]), template)
