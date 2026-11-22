"""
Load the deposition rule store (onedep_lib/schemas/cif/cross_checks_deposition_rules.cif) with gemmi.

The store's categories are defined in the extension dictionary
mmcif_pdbx_deposition_rules_ext.dic, and the store is checked against it:
every column must be defined there, mandatory items must have a value,
enumerated items must use an enumerated value, defaults fill unset values,
category keys must be unique and rule_id links must resolve.

The rules are then checked against the PDBx/mmCIF dictionary (every category
and item they name) and against the constraints the extension dictionary
describes in prose: THEN items in the scope category, one group operator per
role, at most one partner category per condition, flags matching the records
present, and so on. All problems are collected and raised together as a
RulesError. An item missing from the PDBx/mmCIF dictionary is only a warning,
since dictionary versions differ; an unknown category is an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from gemmi import cif

from onedep_lib.checks.crosscheck.dictionary import (
    DATE,
    NUMBER,
    SCHEMA_DIR,
    Dictionary,
    load_dictionary,
    load_extension,
)

DEFAULT_RULES_PATH = SCHEMA_DIR / "cross_checks_deposition_rules.cif"

ROLES = ("WHEN", "THEN", "UNLESS")
EXISTENCE_OPERATORS = {"EXISTS", "NOT_EXISTS", "EMPTY", "NOT_EMPTY"}
LIST_OPERATORS = {"IN", "NOT_IN"}
ORDER_OPERATORS = {"LT", "LE", "GT", "GE"}
VALUE_OPERATORS = {"EQ", "NE", "MATCHES", "NOT_MATCHES"} | LIST_OPERATORS | ORDER_OPERATORS
ITEM_OPERATORS = {"EQ_ITEM", "LE_ITEM", "GE_ITEM", "LT_ITEM", "GT_ITEM"}
COLUMN_OPERATORS = {"IN_COLUMN_OF", "IN_ENUMERATION_OF"}


class RulesError(Exception):
    """The rule store does not conform to the extension dictionary or names unknown categories."""

    def __init__(self, problems: list[str] | str):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("\n".join(self.problems))


def split_list(value: str | None) -> list[str]:
    """Comma-separated values, stripped, empty entries dropped."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


@dataclass(frozen=True)
class ItemName:
    """An mmCIF item name split into category and item, e.g. _refine.ls_d_res_high."""

    category: str
    item: str

    @classmethod
    def parse(cls, name: str) -> ItemName:
        category, dot, item = name.strip().lstrip("_").partition(".")
        if not dot or not category or not item:
            raise RulesError(f"not an mmCIF item name: {name!r}")
        return cls(category, item)

    @property
    def tag(self) -> str:
        return f"_{self.category}.{self.item}"

    def __str__(self) -> str:
        return self.tag


def item_names(value: str | None) -> tuple[ItemName, ...]:
    return tuple(ItemName.parse(name) for name in split_list(value))


@dataclass(frozen=True)
class Condition:
    """One row of _pdbx_deposition_rule_condition."""

    ordinal: int
    role: str  # WHEN, THEN or UNLESS
    item: ItemName
    operator: str
    value: str | None = None
    compare: ItemName | None = None
    group_operator: str = "AND"
    quantifier: str | None = None
    value_transform: str | None = None

    @property
    def values(self) -> list[str]:
        """The literal operand(s): a list for IN / NOT_IN, else the single value."""
        if self.operator in LIST_OPERATORS:
            return split_list(self.value)
        return [] if self.value is None else [self.value]

    @property
    def effective_quantifier(self) -> str:
        """ALL or ANY; by default ANY for WHEN and UNLESS and ALL for THEN."""
        return self.quantifier or ("ALL" if self.role == "THEN" else "ANY")

    @property
    def paired_categories(self) -> set[str]:
        """Categories whose rows are paired with the scope row (not the IN_COLUMN_OF / IN_ENUMERATION_OF item)."""
        names = {self.item.category}
        if self.compare is not None and self.operator not in COLUMN_OPERATORS:
            names.add(self.compare.category)
        return names


@dataclass(frozen=True)
class Join:
    """One row of _pdbx_deposition_rule_join: scope items matched with partner items."""

    ordinal: int
    scope_items: tuple[ItemName, ...]
    partner_items: tuple[ItemName, ...]
    on_no_match: str = "SKIP"

    @property
    def partner_category(self) -> str:
        return self.partner_items[0].category


@dataclass(frozen=True)
class RowSelection:
    """One row of _pdbx_deposition_rule_row_selection: keep the MIN or MAX row per group."""

    category: str
    selector: str  # MIN or MAX
    selector_item: ItemName
    group_by: tuple[ItemName, ...] = ()


@dataclass
class Rule:
    """One _pdbx_deposition_rule row with everything that refers to it."""

    id: str
    family: str
    severity: str  # error, warning or info
    scope_category: str
    method: str | None
    message: str
    enabled: bool = True
    flags: dict[str, bool] = field(default_factory=dict)  # param_flag, join_flag, row_selection_flag
    when: list[Condition] = field(default_factory=list)
    then: list[Condition] = field(default_factory=list)
    unless: list[Condition] = field(default_factory=list)
    params: dict[str, str] = field(default_factory=dict)
    joins: dict[str, Join] = field(default_factory=dict)  # by partner category
    selections: dict[str, RowSelection] = field(default_factory=dict)  # by category

    @property
    def conditions(self) -> list[Condition]:
        return sorted(self.when + self.then + self.unless, key=lambda c: c.ordinal)

    @property
    def partner_categories(self) -> list[str]:
        """Categories other than the scope category whose rows are paired with each scope row."""
        names: list[str] = []
        for cond in self.conditions:
            for name in sorted(cond.paired_categories):
                if name != self.scope_category and name not in names:
                    names.append(name)
        return names

    def param(self, name: str, default: str | None = None) -> str | None:
        return self.params.get(name, default)

    def flag(self, name: str) -> bool:
        return (self.params.get(name) or "").upper() == "YES"

    def param_list(self, name: str) -> list[str]:
        return split_list(self.params.get(name))

    def param_items(self, name: str) -> tuple[ItemName, ...]:
        return item_names(self.params.get(name))


class RuleSet:
    """The rules of a store, with the PDBx/mmCIF dictionary they were checked against."""

    def __init__(self, rules: list[Rule], dictionary: Dictionary, warnings: list[str] | None = None):
        self.rules = rules
        self.dictionary = dictionary
        self.warnings = warnings or []
        self._by_id = {r.id: r for r in rules}

    def __iter__(self):
        return iter(self.rules)

    def __len__(self) -> int:
        return len(self.rules)

    def get(self, rule_id: str) -> Rule:
        return self._by_id[rule_id]

    def subset(self, *rule_ids: str) -> RuleSet:
        """A RuleSet with only the given rules (in store order)."""
        wanted = set(rule_ids)
        return RuleSet([r for r in self.rules if r.id in wanted], self.dictionary, self.warnings)


# -- reading the store against the extension dictionary ---------------------


def _store_rows(block: cif.Block, ext: Dictionary, problems: list[str]) -> dict[str, list[dict]]:
    """Rows of each extension category, with defaults applied and enumerations normalised."""
    for entry in block:
        tags = [entry.pair[0]] if entry.pair else list(entry.loop.tags) if entry.loop else []
        for tag in tags:
            if ext.item(tag) is None:
                problems.append(f"{tag} is not defined in {ext.name}")

    rows_by_category: dict[str, list[dict]] = {}
    for key, category in ext.categories.items():
        name = category.name
        table = block.find_mmcif_category(f"_{name}.")
        columns = [tag[len(name) + 2 :].lower() for tag in table.tags]
        definitions = {d.name.split(".", 1)[1].lower(): d for d in ext.category_items(name)}
        rows = []
        for n, raw_row in enumerate(table, 1):
            raw = {
                col: (None if cif.is_null(raw_row[k]) else cif.as_string(raw_row[k])) for k, col in enumerate(columns)
            }
            row: dict[str, str | None] = {}
            for item_key, d in definitions.items():
                value = raw.get(item_key)
                if value is None:
                    value = d.default
                if value is None and d.mandatory:
                    problems.append(f"_{name} row {n}: {d.name} is mandatory")
                if value is not None and d.enumeration:
                    folded = {v.upper(): v for v in d.enumeration} if ext.case_insensitive(d.name) else None
                    canonical = folded.get(value.upper()) if folded else (value if value in d.enumeration else None)
                    if canonical is None:
                        allowed = ", ".join(d.enumeration)
                        problems.append(f"_{name} row {n}: {d.name} = {value!r} is not one of {allowed}")
                    value = canonical or value
                row[item_key] = value
            row["_row"] = str(n)
            rows.append(row)
        keys = [k.split(".", 1)[1].lower() for k in category.keys]
        seen: set[tuple] = set()
        for row in rows:
            key_value = tuple(row.get(k) for k in keys)
            if key_value in seen:
                problems.append(f"_{name} row {row['_row']}: duplicate key {dict(zip(keys, key_value))}")
            seen.add(key_value)
        rows_by_category[key] = rows

    # _item_linked: child values must exist in the parent item.
    for key, rows in rows_by_category.items():
        for d in ext.category_items(key):
            if not d.parent:
                continue
            parent = ItemName.parse(d.parent)
            parents = {r.get(parent.item.lower()) for r in rows_by_category.get(parent.category.lower(), [])}
            child = d.name.split(".", 1)[1].lower()
            for row in rows:
                if row.get(child) is not None and row[child] not in parents:
                    problems.append(f"{d.name} row {row['_row']}: {row[child]!r} has no {d.parent}")
    return rows_by_category


def _parse_names(value: str | None, where: str, problems: list[str]) -> tuple[ItemName, ...]:
    try:
        return item_names(value)
    except RulesError as exc:
        problems.append(f"{where}: {exc}")
        return ()


def _build(rows: dict[str, list[dict]], problems: list[str]) -> list[Rule]:
    rules: dict[str, Rule] = {}
    for r in rows["pdbx_deposition_rule"]:
        rules[r["id"]] = Rule(
            id=r["id"],
            family=r["family"] or "",
            severity=(r["severity"] or "ERROR").lower(),
            scope_category=r["scope_category"] or "",
            method=r["procedural_method"],
            message=r["message"] or "",
            enabled=(r["enabled"] or "YES") != "NO",
            flags={k: (r.get(k) or "NO") == "YES" for k in ("param_flag", "join_flag", "row_selection_flag")},
        )

    def ordinal(row: dict, where: str) -> int:
        try:
            return int(row["ordinal"])
        except (TypeError, ValueError):
            problems.append(f"{where}: ordinal {row['ordinal']!r} is not an integer")
            return 0

    for c in rows["pdbx_deposition_rule_condition"]:
        rule = rules.get(c["rule_id"])
        where = f"rule {c['rule_id']} condition {c['ordinal']}"
        items = _parse_names(c["item_name"], where, problems)
        compare = _parse_names(c["compare_item_name"], where, problems)
        if rule is None or len(items) != 1:
            continue
        cond = Condition(
            ordinal=ordinal(c, where),
            role=c["role"],
            item=items[0],
            operator=c["operator"],
            value=c["value"],
            compare=compare[0] if compare else None,
            group_operator=c["group_operator"] or "AND",
            quantifier=c["quantifier"],
            value_transform=c["value_transform"],
        )
        getattr(rule, cond.role.lower()).append(cond)
    for rule in rules.values():
        for role in ROLES:
            getattr(rule, role.lower()).sort(key=lambda c: c.ordinal)

    for p in rows["pdbx_deposition_rule_param"]:
        if p["rule_id"] in rules:
            rules[p["rule_id"]].params[p["name"]] = p["value"]

    for j in rows["pdbx_deposition_rule_join"]:
        rule = rules.get(j["rule_id"])
        where = f"rule {j['rule_id']} join {j['ordinal']}"
        scope_items = _parse_names(j["scope_item_names"], where, problems)
        partner_items = _parse_names(j["partner_item_names"], where, problems)
        if rule is None or not scope_items or not partner_items:
            continue
        join = Join(ordinal(j, where), scope_items, partner_items, j["on_no_match"] or "SKIP")
        if join.partner_category in rule.joins:
            problems.append(f"{where}: a second join to {join.partner_category}")
        rule.joins[join.partner_category] = join

    for s in rows["pdbx_deposition_rule_row_selection"]:
        rule = rules.get(s["rule_id"])
        where = f"rule {s['rule_id']} row selection on {s['category_id']}"
        selector_item = _parse_names(s["selector_item"], where, problems)
        group_by = _parse_names(s["group_by"], where, problems)
        if rule is None or len(selector_item) != 1:
            continue
        rule.selections[s["category_id"]] = RowSelection(s["category_id"], s["selector"], selector_item[0], group_by)
    return list(rules.values())


# -- rule constraints from the extension dictionary -------------------------


def _check_rule(rule: Rule, dic: Dictionary, problems: list[str], warnings: list[str]) -> None:
    def problem(text: str) -> None:
        problems.append(f"rule {rule.id}: {text}")

    scope = rule.scope_category
    if dic.category(scope) is None:
        problem(f"scope category {scope} is not in {dic.name}")
    if rule.family != "procedural" and not rule.then:
        problem("needs at least one THEN condition")
    if rule.family == "procedural" and not rule.method:
        problem("procedural rule without procedural_method")

    # Flags must match the records present.
    for flag, present in (
        ("param_flag", bool(rule.params)),
        ("join_flag", bool(rule.joins)),
        ("row_selection_flag", bool(rule.selections)),
    ):
        if rule.flags.get(flag, False) != present:
            stated = "YES" if rule.flags.get(flag) else "NO"
            problem(f"{flag} is {stated} but the rule has {'' if present else 'no '}records")

    for role in ROLES:
        operators = {c.group_operator for c in getattr(rule, role.lower())}
        if len(operators) > 1:
            problem(f"{role} conditions mix group operators {sorted(operators)}")

    def known_item(name: ItemName, where: str) -> None:
        if dic.category(name.category) is None:
            problem(f"{where}: category {name.category} is not in {dic.name}")
        elif dic.item(name.tag) is None:
            warnings.append(f"rule {rule.id}: {where}: {name.tag} is not in {dic.name}")

    for cond in rule.conditions:
        where = f"condition {cond.ordinal}"
        known_item(cond.item, where)
        if cond.compare is not None:
            known_item(cond.compare, where)
        op = cond.operator
        if cond.role == "THEN" and cond.item.category != scope:
            problem(f"{where}: THEN item {cond.item} is not in the scope category {scope}")
        partners = cond.paired_categories - {scope}
        if len(partners) > 1:
            problem(f"{where}: refers to more than one partner category {sorted(partners)}")
        if (
            cond.role != "THEN"
            and cond.compare is not None
            and cond.item.category != scope
            and cond.compare.category == scope
            and op not in COLUMN_OPERATORS
        ):
            problem(f"{where}: compare item {cond.compare} is in the scope category but {cond.item} is not")
        if (op in ITEM_OPERATORS or op in COLUMN_OPERATORS) and cond.compare is None:
            problem(f"{where}: {op} needs compare_item_name")
        if op in VALUE_OPERATORS and cond.value is None:
            problem(f"{where}: {op} needs a value")
        if op in ("MATCHES", "NOT_MATCHES") and cond.value is not None:
            try:
                re.compile(cond.value)
            except re.error as exc:
                problem(f"{where}: invalid pattern {cond.value!r}: {exc}")
        if op in ORDER_OPERATORS or (op in ITEM_OPERATORS and op != "EQ_ITEM"):
            kinds = {dic.kind(cond.item.tag)} | ({dic.kind(cond.compare.tag)} if cond.compare else set())
            if not kinds & {NUMBER, DATE}:
                problem(f"{where}: {op} needs a numeric or date item")
        if cond.value_transform == "ABS":
            for name in (cond.item, cond.compare if op in ITEM_OPERATORS else None):
                if name is not None and dic.kind(name.tag) != NUMBER:
                    problem(f"{where}: value_transform ABS on non-numeric {name}")
        if op == "IN_ENUMERATION_OF" and cond.compare is not None:
            compare_def = dic.item(cond.compare.tag)
            if compare_def is not None and not compare_def.enumeration:
                problem(f"{where}: {cond.compare} has no enumeration in {dic.name}")
            if cond.compare.category != cond.item.category:
                problem(f"{where}: IN_ENUMERATION_OF pairs values of one row, so both items must be in one category")
            if not dic.enum_details(cond.item.tag):
                warnings.append(
                    f"rule {rule.id}: {cond.item} has no enumeration details in {dic.name}, so the rule never fails"
                )

    for partner, join in rule.joins.items():
        where = f"join {join.ordinal}"
        if any(n.category != scope for n in join.scope_items):
            problem(f"{where}: scope items must be in {scope}")
        if len({n.category for n in join.partner_items}) != 1 or partner == scope:
            problem(f"{where}: partner items must be in one category other than {scope}")
        if len(join.scope_items) != len(join.partner_items):
            problem(f"{where}: scope and partner item lists differ in length")
        if partner not in rule.partner_categories:
            warnings.append(f"rule {rule.id}: {where} joins {partner}, which no condition refers to")
        for name in join.scope_items + join.partner_items:
            known_item(name, where)

    for category, selection in rule.selections.items():
        where = f"row selection on {category}"
        if category != scope and category not in rule.partner_categories:
            problem(f"{where}: the rule does not refer to {category}")
        if any(n.category != category for n in (selection.selector_item, *selection.group_by)):
            problem(f"{where}: selector and group_by items must be in {category}")
        if dic.kind(selection.selector_item.tag) not in (NUMBER, DATE):
            problem(f"{where}: selector item {selection.selector_item} must have a numeric or date type")
        for name in (selection.selector_item, *selection.group_by):
            known_item(name, where)

    for name in ("item", "group_by", "exclude_items"):
        for item in _parse_names(rule.param(name), f"rule {rule.id}: param {name}", problems):
            known_item(item, f"param {name}")
    for name in ("skip_if_any_category_present", "source_category", "target_category"):
        for category in rule.param_list(name):
            if dic.category(category) is None:
                problem(f"param {name}: category {category} is not in {dic.name}")


def load_rules(
    path: Path | None = None,
    dictionary_path: Path | None = None,
    extension_path: Path | None = None,
) -> RuleSet:
    """
    Read and check the rule store; defaults are the packaged store and dictionaries.

    Raises RulesError listing every problem found. Items missing from the
    PDBx/mmCIF dictionary are reported in RuleSet.warnings instead.
    """
    ext = load_extension(extension_path)
    dic = load_dictionary(dictionary_path)
    block = cif.read(str(path or DEFAULT_RULES_PATH)).sole_block()

    problems: list[str] = []
    warnings: list[str] = []
    rows = _store_rows(block, ext, problems)
    if not rows.get("pdbx_deposition_rule"):
        problems.append("rule store has no _pdbx_deposition_rule rows")
    rules = _build(rows, problems) if not problems else []
    for rule in rules:
        _check_rule(rule, dic, problems, warnings)
    if problems:
        raise RulesError(problems)
    return RuleSet(rules, dic, warnings)
