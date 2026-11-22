"""
Read DDL2 dictionaries with gemmi.

Two dictionaries are used:

- the PDBx/mmCIF dictionary (schemas/cif/mmcif_pdbx_v50.dic), for the type,
  enumeration and category of every item a rule refers to;
- the deposition-rules extension (schemas/cif/mmcif_pdbx_deposition_rules_ext.dic),
  which defines the rule store's own categories: their keys, mandatory items,
  defaults, enumerations and links.

Item types decide how values are compared: a value is a number, a date or
text, and a value that cannot be read as its type is treated as missing.
Type constructs (_item_type_list.construct) are kept for MATCHES_TYPE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from gemmi import cif

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "cif"
DEFAULT_DICTIONARY_PATH = SCHEMA_DIR / "mmcif_pdbx_v50.dic"
DEFAULT_EXTENSION_PATH = SCHEMA_DIR / "mmcif_pdbx_deposition_rules_ext.dic"

# Kinds of value, from the item type.
NUMBER = "number"
DATE = "date"
TEXT = "text"

# Primitive codes of the standard DDL2 types, for dictionaries that use them without a _item_type_list.
_STANDARD_PRIMITIVES = {
    "code": "char",
    "line": "char",
    "text": "char",
    "ucode": "uchar",
    "uline": "uchar",
    "int": "numb",
    "float": "numb",
}

_NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)(\(\d+\))?([eE][+-]?\d+)?")
_DATE = re.compile(r"(\d{1,4})-(\d{1,2})(?:-(\d{1,2}))?(?::(\d{1,2}):(\d{1,2}))?")


def _text(raw: str) -> str:
    return cif.as_string(raw)


def convert(value: str | None, kind: str, case_insensitive: bool = False):
    """
    The value as a number, a date tuple or stripped text; None when it is missing.

    A value that is empty, '?' or '.' once stripped (quoted or not) is missing,
    as is one that cannot be read as `kind`. Standard uncertainties such as
    `1.234(5)` are dropped from numbers.
    """
    if value is None:
        return None
    value = value.strip()
    if value in ("", "?", "."):
        return None
    if kind == NUMBER:
        if not _NUMBER.fullmatch(value):
            return None
        return float(re.sub(r"\(\d+\)", "", value))
    if kind == DATE:
        m = _DATE.fullmatch(value)
        return tuple(int(g or 0) for g in m.groups()) if m else None
    return value.upper() if case_insensitive else value


@dataclass
class ItemDef:
    name: str
    category: str
    type_code: str | None = None
    mandatory: bool = False
    default: str | None = None
    # Enumerated values, each with its detail text (or None), in dictionary order.
    enumeration: dict[str, str | None] = field(default_factory=dict)
    parent: str | None = None  # _item_linked.parent_name for this item
    examples: list[str] = field(default_factory=list)  # _item_examples.case


@dataclass
class CategoryDef:
    name: str
    keys: list[str]


class Dictionary:
    """Items, categories and types of a DDL2 dictionary, looked up case-insensitively."""

    def __init__(self, path: Path):
        block = cif.read(str(path)).sole_block()
        self.path = Path(path)
        self.name = block.name
        self.items: dict[str, ItemDef] = {}
        self.categories: dict[str, CategoryDef] = {}
        # type code -> primitive code (numb, char, uchar)
        type_list = block.find("_item_type_list.", ["code", "primitive_code", "?construct"])
        self.primitives = {**_STANDARD_PRIMITIVES, **{_text(r[0]): _text(r[1]) for r in type_list}}
        # type code -> construct (a POSIX extended regular expression for the whole value)
        self.constructs = {_text(r[0]): _text(r[2]) for r in type_list if r.has(2) and not cif.is_null(r[2])}
        self._links: dict[str, str] = {}  # child -> parent, from any frame's _item_linked
        for entry in block:
            if entry.frame is not None:
                self._read_frame(entry.frame)
        for item in self.items.values():
            if item.parent is None:
                item.parent = self._links.get(item.name.lower())
        # A child item without a type of its own takes its parent's type.
        for item in self.items.values():
            seen = {item.name.lower()}
            parent = item
            while item.type_code is None and parent.parent and parent.parent.lower() not in seen:
                seen.add(parent.parent.lower())
                parent = self.items.get(parent.parent.lower())
                if parent is None:
                    break
                item.type_code = parent.type_code

    def _read_frame(self, frame: cif.Block) -> None:
        for row in frame.find("_item_linked.", ["child_name", "parent_name"]):
            self._links.setdefault(_text(row[0]).lower(), _text(row[1]))
        category_id = frame.find_value("_category.id")
        if category_id is not None:
            name = _text(category_id)
            keys = [_text(k) for k in frame.find_values("_category_key.name")]
            self.categories[name.lower()] = CategoryDef(name, keys)
            return
        name = frame.name
        if not name.startswith("_") or "." not in name:
            return
        item = ItemDef(name=name, category=name[1:].split(".", 1)[0])
        type_code = frame.find_value("_item_type.code")
        item.type_code = _text(type_code) if type_code is not None else None
        for row in frame.find("_item.", ["name", "?mandatory_code"]):
            if _text(row[0]).lower() == name.lower() and row.has(1):
                item.mandatory = _text(row[1]).lower() == "yes"
        default = frame.find_value("_item_default.value")
        item.default = _text(default) if default is not None else None
        for prefix in ("_item_enumeration.", "_pdbx_item_enumeration."):
            for row in frame.find(prefix, ["value", "?detail"]):
                value = _text(row[0])
                detail = _text(row[1]) if row.has(1) and not cif.is_null(row[1]) else None
                if item.enumeration.get(value) is None:
                    item.enumeration[value] = detail
        for row in frame.find("_item_linked.", ["child_name", "parent_name"]):
            if _text(row[0]).lower() == name.lower():
                item.parent = _text(row[1])
        item.examples = [_text(v) for v in frame.find_values("_item_examples.case")]
        self.items[name.lower()] = item

    def item(self, tag: str) -> ItemDef | None:
        return self.items.get(tag.lower())

    def category(self, name: str) -> CategoryDef | None:
        return self.categories.get(name.lower())

    def category_items(self, name: str) -> list[ItemDef]:
        return [i for i in self.items.values() if i.category.lower() == name.lower()]

    def kind(self, tag: str) -> str:
        """NUMBER, DATE or TEXT, from the item's type; TEXT for unknown items."""
        item = self.item(tag)
        code = (item.type_code or "") if item else ""
        if code.startswith("yyyy-mm-dd") or code.startswith("date"):
            return DATE
        if self.primitives.get(code) == "numb" and not code.endswith("-range"):
            return NUMBER
        return TEXT

    def case_insensitive(self, tag: str) -> bool:
        """Values of uchar types (ucode, uline, ...) compare case-insensitively."""
        item = self.item(tag)
        return bool(item and self.primitives.get(item.type_code or "") == "uchar")

    def enum_details(self, tag: str) -> dict[str, list[str]]:
        """{enumerated value: values listed in its comma-separated detail} for values that have one."""
        item = self.item(tag)
        if item is None:
            return {}
        return {
            value: [p.strip() for p in detail.split(",") if p.strip()]
            for value, detail in item.enumeration.items()
            if detail
        }


@lru_cache(maxsize=8)
def _load(path: str) -> Dictionary:
    return Dictionary(Path(path))


def load_dictionary(path: Path | None = None) -> Dictionary:
    """The PDBx/mmCIF dictionary (packaged v50 by default); cached per path."""
    return _load(str(Path(path or DEFAULT_DICTIONARY_PATH).resolve()))


def load_extension(path: Path | None = None) -> Dictionary:
    """The deposition-rules extension dictionary (packaged by default); cached per path."""
    return _load(str(Path(path or DEFAULT_EXTENSION_PATH).resolve()))
