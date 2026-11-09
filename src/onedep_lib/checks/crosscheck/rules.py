"""
Load the deposition rule store (onedep_lib/schemas/cif/cross_checks_deposition_rules.cif) with gemmi.

The store has three categories, defined in mmcif_pdbx_deposition_rules_ext.dic:
_pdbx_deposition_rule (one row per rule), _pdbx_deposition_rule_condition (the
WHEN / THEN / UNLESS clauses of a rule, ordered by `ordinal`) and
_pdbx_deposition_rule_param (name/value settings). load_rules() joins them into
one Rule per rule id, in file order. Unset values ('.' or '?') become None.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gemmi import cif

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "schemas" / "cif" / "cross_checks_deposition_rules.cif"

ROLES = ("WHEN", "THEN", "UNLESS")

# Operators whose value is a comma-separated list rather than a single value.
LIST_OPERATORS = {"IN", "NOT_IN"}


class RulesError(Exception):
    """The rule store is missing a category or is internally inconsistent."""


def split_item(name: str) -> tuple[str, str]:
    """'_category.item' -> ('category', 'item')."""
    category, dot, item = name.strip().lstrip("_").partition(".")
    if not dot or not category or not item:
        raise RulesError(f"not an mmCIF item name: {name!r}")
    return category, item


def split_list(value: str | None) -> list[str]:
    """Comma-separated values, stripped, empty entries dropped."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


@dataclass(frozen=True)
class Condition:
    """One row of _pdbx_deposition_rule_condition."""

    role: str  # WHEN, THEN or UNLESS
    category: str
    item: str
    operator: str
    value: str | None = None
    other_category: str | None = None
    other_item: str | None = None
    group_operator: str = "AND"

    @property
    def name(self) -> str:
        return f"_{self.category}.{self.item}"

    @property
    def other_name(self) -> str | None:
        return f"_{self.other_category}.{self.other_item}" if self.other_category else None

    @property
    def values(self) -> list[str]:
        """The operator's operand(s): a list for IN / NOT_IN, else the single value."""
        if self.operator in LIST_OPERATORS:
            return split_list(self.value)
        return [] if self.value is None else [self.value]


@dataclass
class Rule:
    """One row of _pdbx_deposition_rule with its conditions and parameters."""

    id: str
    family: str
    severity: str  # error, warning or info
    scope_category: str | None
    method: str | None
    message: str
    enabled: bool = True
    when: list[Condition] = field(default_factory=list)
    then: list[Condition] = field(default_factory=list)
    unless: list[Condition] = field(default_factory=list)
    params: dict[str, str] = field(default_factory=dict)

    def param(self, name: str, default: str | None = None) -> str | None:
        return self.params.get(name, default)

    def flag(self, name: str) -> bool:
        return (self.params.get(name) or "").upper() == "YES"

    def param_list(self, name: str) -> list[str]:
        return split_list(self.params.get(name))

    def param_item(self, name: str) -> tuple[str, str] | None:
        value = self.params.get(name)
        return split_item(value) if value else None


def _value(raw: str) -> str | None:
    return None if cif.is_null(raw) else cif.as_string(raw)


def _rows(block: cif.Block, category: str, required: bool = True) -> list[dict]:
    table = block.find_mmcif_category(f"_{category}.")
    if not table.tags:
        if required:
            raise RulesError(f"rule store has no _{category} category")
        return []
    names = [tag[len(category) + 2 :] for tag in table.tags]
    return [{name: _value(row[i]) for i, name in enumerate(names)} for row in table]


def _owner(rules: dict[str, Rule], rule_id: str | None, where: str) -> Rule:
    rule = rules.get(rule_id or "")
    if rule is None:
        raise RulesError(f"{where} refers to unknown rule {rule_id!r}")
    return rule


def load_rules(path: Path | None = None) -> list[Rule]:
    """Return every rule in the store, in file order, with conditions and parameters attached."""
    block = cif.read(str(path or DEFAULT_RULES_PATH)).sole_block()

    rules: dict[str, Rule] = {}
    for r in _rows(block, "pdbx_deposition_rule"):
        if not r["id"] or not r["family"]:
            raise RulesError(f"rule without id or family: {r}")
        rules[r["id"]] = Rule(
            id=r["id"],
            family=r["family"],
            severity=(r["severity"] or "ERROR").lower(),
            scope_category=r["scope_category"],
            method=r["procedural_method"],
            message=r["message"] or "",
            enabled=(r["enabled"] or "YES").upper() != "NO",
        )

    conditions = _rows(block, "pdbx_deposition_rule_condition", required=False)
    for c in sorted(conditions, key=lambda c: int(c["ordinal"])):
        rule = _owner(rules, c["rule_id"], "condition")
        role = (c["role"] or "").upper()
        if role not in ROLES:
            raise RulesError(f"rule {rule.id}: unknown condition role {c['role']!r}")
        category, item = split_item(c["item_name"] or "")
        other_category, other_item = split_item(c["other_item_name"]) if c["other_item_name"] else (None, None)
        condition = Condition(
            role=role,
            category=category,
            item=item,
            operator=(c["operator"] or "").upper(),
            value=c["value"],
            other_category=other_category,
            other_item=other_item,
            group_operator=(c["group_operator"] or "AND").upper(),
        )
        getattr(rule, role.lower()).append(condition)

    for p in _rows(block, "pdbx_deposition_rule_param", required=False):
        _owner(rules, p["rule_id"], "parameter").params[p["name"]] = p["value"]

    return list(rules.values())


def load_dictionary_enum_details(dictionary_path: Path, item_names: list[str]) -> dict[str, dict[str, list[str]]]:
    """
    Read `_item_enumeration.detail` for the given items from a DDL2 dictionary.

    The detail column of these items holds a comma-separated list of values
    allowed in a companion item (e.g. which detector types go with a detector).
    Returns {item_name: {enum_value: [allowed companion values]}}.
    """
    doc = cif.read(str(dictionary_path))
    block = doc.sole_block()
    result: dict[str, dict[str, list[str]]] = {}
    for name in item_names:
        frame = block.find_frame(name)
        if frame is None:
            continue
        for prefix in ("_item_enumeration.", "_pdbx_item_enumeration."):
            table = frame.find(prefix, ["value", "?detail"])
            if len(table) == 0:
                continue
            details: dict[str, list[str]] = {}
            for row in table:
                if not row.has(1) or cif.is_null(row[1]):
                    continue
                parts = [p.strip() for p in cif.as_string(row[1]).split(",") if p.strip()]
                if parts:
                    details[cif.as_string(row[0])] = parts
            result[name] = details
            break
    return result
