"""
Run the deposition rules against an mmCIF block parsed by gemmi.

Each rule is evaluated as the extension dictionary describes
(_pdbx_deposition_rule_condition), separately for each row of its scope
category:

1. rows of each category the rule refers to are restricted by
   _pdbx_deposition_rule_row_selection (the MIN or MAX row per group, or
   every row with a value: ANY_VALUE);
2. each remaining scope row is paired with rows of each partner category
   through _pdbx_deposition_rule_join, or with every row when the rule has no
   join; a scope row without a match is skipped, reported, or paired by the
   single-row fallback, as on_no_match says;
3. the rule applies when its WHEN conditions hold and its UNLESS conditions do
   not; it fails when its THEN conditions do not hold. A condition on a partner
   category is evaluated over the paired rows with its quantifier (ALL or ANY).

Values are typed by the PDBx/mmCIF dictionary: numbers, dates or text, with
uchar types (ucode, uline, ...) compared case-insensitively. A value is
missing when its item or category is absent, when it is empty, '?' or '.'
once stripped, or when it cannot be read as its type. A comparison with a
missing value is skipped: the WHEN or UNLESS condition does not hold, and the
THEN condition is satisfied. EXISTS, NOT_EXISTS, EMPTY and NOT_EMPTY test for
missing values and are always evaluated.

Each failing scope row is reported once. gemmi records line numbers per item
or loop, not per value: a finding on a key-value item carries that item's
line; a finding inside a loop carries the line of the loop and the 1-based row.
"""

from __future__ import annotations

import operator
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from gemmi import cif

from onedep_lib.checks.crosscheck.dictionary import DATE, NUMBER, TEXT, Dictionary, convert
from onedep_lib.checks.crosscheck.rules import (
    EXISTENCE_OPERATORS,
    ITEM_OPERATORS,
    Condition,
    ItemName,
    Join,
    Rule,
    RulesError,
    RuleSet,
    load_rules,
)


@dataclass
class Finding:
    severity: str  # "error", "warning" or "info"
    item: str  # full mmCIF item name, e.g. _refine.ls_d_res_high
    message: str
    rule_id: str
    line: int | None = None
    row: int | None = None  # 1-based loop row; None for key-value items

    def as_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        where = f"line {self.line}" if self.line else "line ?"
        if self.row is not None:
            where += f", row {self.row}"
        return f"{self.severity.upper()}: {self.item} ({where}) [{self.rule_id}]\n  {self.message}"


@dataclass
class Context:
    """What the depositor asked for: requested accession codes (e.g. PDB) for experiment_code rules."""

    requested_codes: set[str] = field(default_factory=set)

    @classmethod
    def empty(cls) -> Context:
        return cls()


# Comparison of two items, and the symbol of its violation (for messages).
_ITEM_COMPARE = {
    "EQ_ITEM": (operator.eq, "!="),
    "LE_ITEM": (operator.le, ">"),
    "GE_ITEM": (operator.ge, "<"),
    "LT_ITEM": (operator.lt, ">="),
    "GT_ITEM": (operator.gt, "<="),
}
# Comparison with the literal value.
_LITERAL_COMPARE = {
    "EQ": operator.eq,
    "NE": operator.ne,
    "LT": operator.lt,
    "LE": operator.le,
    "GT": operator.gt,
    "GE": operator.ge,
}
_AGGREGATES = {"SUM": sum, "COUNT": len}

# Families evaluated row by row from their conditions alone.
_CONDITION_FAMILIES = {"pairwise_order", "conditional_presence", "conditional_value", "format", "cross_reference"}


def _fmt_number(value: float) -> str:
    return f"{round(value, 4):.4f}".rstrip("0").rstrip(".") or "0"


def _unwrap(value: str) -> str:
    """Join a text field wrapped over several lines (e.g. a one-letter sequence)."""
    return re.sub(r"\s*\n\s*", "", value.strip())


def _render(template: str | None, **values) -> str:
    """Fill [{name}] placeholders and drop any left unresolved."""
    text = (template or "").strip()
    for key, value in values.items():
        text = text.replace(f"[{{{key}}}]", str(value or ""))
    text = re.sub(r"\[\{[^}]+\}\]", "", text).strip()
    return re.sub(r"\s{2,}", " ", text)


def _with_label_fallback(item: str) -> tuple[str, ...]:
    """An auth_* item and, as in mmCIF, its label_* counterpart for when it has no value."""
    return (item, "label_" + item[5:]) if item.lower().startswith("auth_") else (item,)


def _combine(conditions: list[Condition], results: list[bool]) -> bool:
    """AND the results, or OR them when the conditions form an OR group."""
    if any(c.group_operator == "OR" for c in conditions):
        return any(results)
    return all(results)


class Category:
    """A gemmi mmCIF category, read column by column and typed by the dictionary."""

    def __init__(self, block: cif.Block, name: str, dictionary: Dictionary):
        self.name = name
        self.block = block
        self.dictionary = dictionary
        self.table = block.find_mmcif_category(f"_{name}.")
        prefix = len(name) + 2
        # mmCIF tags are case-insensitive; keep the file's spelling for line lookup.
        self.tags = {tag[prefix:].lower(): tag for tag in self.table.tags}
        self.index = {tag[prefix:].lower(): i for i, tag in enumerate(self.table.tags)}
        self.size = len(self.table) if self.index else 0
        self.is_loop = self.size > 0 and self.table.loop is not None
        self._columns: dict[str, list[str | None]] = {}
        self._typed: dict[tuple[str, str, bool], list] = {}

    def __len__(self) -> int:
        return self.size

    def has(self, item: str) -> bool:
        return item.lower() in self.index

    def column(self, item: str) -> list[str | None]:
        """Raw values; None for unquoted '?' and '.' and for an absent item."""
        key = item.lower()
        col = self._columns.get(key)
        if col is None:
            i = self.index.get(key)
            if i is None:
                col = [None] * self.size
            else:
                col = [None if cif.is_null(raw) else cif.as_string(raw) for raw in self.table.column(i)]
            self._columns[key] = col
        return col

    def values(self, item: str, kind: str | None = None, case_insensitive: bool | None = None) -> list:
        """Typed values (by default the item's own type); None where a value is missing."""
        tag = self.full(item)
        kind = kind or self.dictionary.kind(tag)
        if case_insensitive is None:
            case_insensitive = self.dictionary.case_insensitive(tag)
        key = (item.lower(), kind, case_insensitive)
        typed = self._typed.get(key)
        if typed is None:
            typed = self._typed[key] = [convert(v, kind, case_insensitive) for v in self.column(item)]
        return typed

    def get(self, row: int, item: str) -> str | None:
        return self.column(item)[row]

    def missing(self, row: int, item: str) -> bool:
        return self.values(item)[row] is None

    def text(self, row: int, *items: str, default: str = "") -> str:
        """First non-empty stripped value among items (fallback chain)."""
        for item in items:
            value = self.get(row, item)
            if value is not None and value.strip():
                return value.strip()
        return default

    def full(self, item: str) -> str:
        return f"_{self.name}.{item}"

    def location(self, item: str, row: int) -> tuple[int | None, int | None]:
        """(line, 1-based loop row) for a value; row is None for key-value items."""
        if self.size == 0:
            return None, None
        tag = self.tags.get(item.lower()) or next(iter(self.tags.values()))
        if self.is_loop:
            found = self.block.find_loop_item(tag)
            return (found.line_number if found else None), row + 1
        found = self.block.find_pair_item(tag)
        return (found.line_number if found else None), None


class CrossChecker:
    """Runs every enabled rule of a RuleSet against one data block."""

    def __init__(self, block: cif.Block, rules: RuleSet, context: Context | None = None):
        self.block = block
        self.rules = rules
        self.dictionary = rules.dictionary
        self.context = context or Context.empty()
        self.findings: list[Finding] = []
        self._categories: dict[str, Category] = {}
        self._selected: dict[tuple[str, str], list[int]] = {}
        self._join_index: dict[tuple[str, str], dict[tuple, list[int]]] = {}
        self._column_sets: dict[tuple[str, int], set] = {}
        self._literals: dict[tuple[str, int], list] = {}
        for rule in rules:
            if rule.family == "procedural":
                if getattr(self, f"_proc_{rule.method}", None) is None:
                    raise RulesError(f"rule {rule.id}: unknown procedural method {rule.method!r}")
            elif rule.family not in _CONDITION_FAMILIES | {"uniqueness", "aggregate"}:
                raise RulesError(f"rule {rule.id}: unknown family {rule.family!r}")

    def cat(self, name: str) -> Category:
        c = self._categories.get(name)
        if c is None:
            c = self._categories[name] = Category(self.block, name, self.dictionary)
        return c

    def report(self, rule: Rule, cat: Category, item: str, row: int, message: str) -> None:
        line, loop_row = cat.location(item, row)
        self.findings.append(Finding(rule.severity, cat.full(item), message, rule.id, line, loop_row))

    def run(self) -> list[Finding]:
        for rule in self.rules:
            if not rule.enabled or not self._in_effect(rule):
                continue
            if rule.family == "uniqueness":
                self._uniqueness(rule)
            elif rule.family == "aggregate":
                self._aggregate(rule)
            elif rule.family == "procedural":
                getattr(self, f"_proc_{rule.method}")(rule)
            else:
                self._check_rows(rule)
        return self.findings

    def _in_effect(self, rule: Rule) -> bool:
        """An experiment_code rule applies only when that accession code type is requested."""
        code = rule.param("experiment_code")
        return code is None or code.upper() in {c.upper() for c in self.context.requested_codes}

    # -- rows: selection, pairing and conditions ------------------------------

    def _selected_rows(self, rule: Rule, category: str) -> list[int]:
        """Rows of a category left by the rule's row selection (all rows without one)."""
        key = (rule.id, category)
        rows = self._selected.get(key)
        if rows is not None:
            return rows
        c = self.cat(category)
        selection = rule.selections.get(category)
        if selection is None:
            rows = list(range(len(c)))
        elif selection.selector == "ANY_VALUE":
            exclude = {n.item.lower() for n in selection.exclude_items}
            items = [item for item in c.index if item not in exclude]
            rows = [i for i in range(len(c)) if any(c.values(item, TEXT)[i] is not None for item in items)]
        else:
            ranks = c.values(selection.selector_item.item) if selection.selector_item else [None] * len(c)
            groups: dict[tuple, list[int]] = defaultdict(list)
            for i in range(len(c)):
                if ranks[i] is not None:
                    groups[tuple(c.values(g.item, TEXT)[i] for g in selection.group_by)].append(i)
            pick = min if selection.selector == "MIN" else max
            rows = []
            for members in groups.values():
                best = pick(ranks[i] for i in members)
                rows.extend(i for i in members if ranks[i] == best)
            rows.sort()
        self._selected[key] = rows
        return rows

    @staticmethod
    def _key(c: Category, names: tuple[ItemName, ...], i: int) -> tuple | None:
        values = tuple(c.values(n.item, TEXT)[i] for n in names)
        return None if None in values else values

    def _partners(self, rule: Rule, scope: Category, i: int) -> tuple[dict[str, list[int]], Join | None]:
        """Partner rows paired with scope row i, by category, and the first join that found none."""
        partners: dict[str, list[int]] = {}
        for name in rule.partner_categories:
            rows = self._selected_rows(rule, name)
            join = rule.joins.get(name)
            if join is None:
                partners[name] = rows
                continue
            matches = self._joined(rule, join, rows).get(self._key(scope, join.scope_items, i) or (), [])
            if not matches:
                fallback = join.on_no_match == "SINGLE_ROW_FALLBACK"
                if not fallback or not self._single_row_fallback(rule, join, scope, i, rows):
                    return partners, join
                matches = rows
            partners[name] = matches
        return partners, None

    def _joined(self, rule: Rule, join: Join, rows: list[int]) -> dict[tuple, list[int]]:
        """Partner rows by join key (rows with a missing key part are left out)."""
        key = (rule.id, join.partner_category)
        index = self._join_index.get(key)
        if index is None:
            index = defaultdict(list)
            partner = self.cat(join.partner_category)
            for j in rows:
                value = self._key(partner, join.partner_items, j)
                if value is not None:
                    index[value].append(j)
            self._join_index[key] = index
        return index

    def _single_row_fallback(self, rule: Rule, join: Join, scope: Category, i: int, rows: list[int]) -> bool:
        """Pair the only rows of both categories when a join item of either is missing."""
        if len(self._selected_rows(rule, scope.name)) != 1 or len(rows) != 1:
            return False
        partner = self.cat(join.partner_category)
        return self._key(scope, join.scope_items, i) is None or self._key(partner, join.partner_items, rows[0]) is None

    def _applicable_rows(self, rule: Rule) -> Iterator[tuple[int, dict[str, list[int]]]]:
        """Scope rows the rule applies to, with their partner rows."""
        scope = self.cat(rule.scope_category)
        for i in self._selected_rows(rule, scope.name):
            partners, unmatched = self._partners(rule, scope, i)
            if unmatched is not None:
                if unmatched.on_no_match == "FAIL":
                    first = rule.then[0].item.item if rule.then else next(iter(scope.index), "")
                    message = f"{_render(rule.message)} (no matching {unmatched.partner_category} row)"
                    self.report(rule, scope, first, i, message)
                continue
            if self._clause(rule, rule.when, scope, i, partners, empty=True) and not self._clause(
                rule, rule.unless, scope, i, partners, empty=False
            ):
                yield i, partners

    def _clause(
        self, rule: Rule, conditions: list[Condition], scope: Category, i: int, partners: dict, empty: bool
    ) -> bool:
        """WHEN or UNLESS: a skipped comparison counts as not holding."""
        if not conditions:
            return empty
        return _combine(conditions, [bool(self._condition(rule, c, scope, i, partners)[0]) for c in conditions])

    def _then_failure(
        self, rule: Rule, scope: Category, i: int, partners: dict
    ) -> tuple[Condition, tuple[Category, int] | None] | None:
        """The first failing THEN condition (and the row it failed on), or None when THEN holds."""
        outcomes = [(c, *self._condition(rule, c, scope, i, partners)) for c in rule.then]
        satisfied = [result is None or result for _, result, _ in outcomes]
        if _combine(rule.then, satisfied):
            return None
        return next((c, witness) for (c, _, witness), ok in zip(outcomes, satisfied) if not ok)

    # -- one condition ----------------------------------------------------------

    def _kind(self, cond: Condition) -> tuple[str, bool]:
        """How the condition compares values: NUMBER, DATE or TEXT, and whether case-insensitively."""
        names = [cond.item]
        if cond.compare is not None and cond.operator in ITEM_OPERATORS | {"IN_COLUMN_OF"}:
            names.append(cond.compare)
        kinds = {self.dictionary.kind(n.tag) for n in names}
        kind = NUMBER if NUMBER in kinds else DATE if DATE in kinds else TEXT
        return kind, any(self.dictionary.case_insensitive(n.tag) for n in names)

    def _literal_values(self, rule: Rule, cond: Condition) -> list:
        key = (rule.id, cond.ordinal)
        values = self._literals.get(key)
        if values is None:
            kind, ci = self._kind(cond)
            values = [convert(v, kind, ci) for v in cond.values]
            if None in values:
                raise RulesError(f"rule {rule.id}: value {cond.value!r} cannot be read as {kind} for {cond.item}")
            self._literals[key] = values
        return values

    def _condition(
        self, rule: Rule, cond: Condition, scope: Category, i: int, partners: dict
    ) -> tuple[bool | None, tuple[Category, int] | None]:
        """
        (result, witness): result is None when no comparison could be made; witness
        is the row the condition failed on.
        """
        if cond.operator == "IN_COLUMN_OF":
            return self._in_column(rule, cond, scope, i), (scope, i)
        if cond.operator == "IN_ENUMERATION_DETAILS_OF":
            return self._in_enumeration_details(cond, scope, i), (scope, i)
        item_in_scope = cond.item.category == scope.name
        if item_in_scope and (cond.compare is None or cond.compare.category == scope.name):
            return self._test(rule, cond, scope, i, scope, i), (scope, i)
        partner = self.cat(cond.compare.category if item_in_scope and cond.compare else cond.item.category)
        existence = cond.operator in EXISTENCE_OPERATORS
        quantify = all if cond.effective_quantifier == "ALL" else any
        results: list[bool] = []
        witness = None
        for j in partners.get(partner.name, []):
            a, ai = (scope, i) if item_in_scope else (partner, j)
            result = self._test(rule, cond, a, ai, partner, j)
            if result is None:
                continue  # comparisons ignore partner rows with a missing value
            results.append(result)
            if not result and witness is None:
                witness = (partner, j)
        if not results:
            # Existence tests over no partner rows: ANY does not hold, ALL holds.
            return (quantify([]) if existence else None), None
        holds = quantify(results)
        return holds, None if holds else witness

    def _test(self, rule: Rule, cond: Condition, a: Category, ai: int, b: Category, bi: int) -> bool | None:
        """Evaluate cond with item_name from row ai of a and compare_item_name from row bi of b."""
        op = cond.operator
        if op == "EXISTS":
            return a.has(cond.item.item)
        if op == "NOT_EXISTS":
            return not a.has(cond.item.item)
        if op in ("EMPTY", "NOT_EMPTY"):
            return a.missing(ai, cond.item.item) == (op == "EMPTY")
        if op in ("MATCHES", "NOT_MATCHES", "MATCHES_TYPE"):
            raw = a.get(ai, cond.item.item)
            if raw is None or a.missing(ai, cond.item.item):
                return None
            if op == "MATCHES_TYPE":
                # Constructs match the whole value, as in DDL2 type checking.
                return any(self.rules.type_pattern(code).fullmatch(raw.strip()) for code in cond.values)
            return (re.search(cond.value or "", _unwrap(raw)) is not None) == (op == "MATCHES")
        if op == "UNIQUE":
            return None  # checked over all rows by _uniqueness
        kind, ci = self._kind(cond)
        left = a.values(cond.item.item, kind, ci)[ai]
        if left is None:
            return None
        absolute = cond.value_transform == "ABS" and kind == NUMBER
        if absolute:
            left = abs(left)
        if op in _ITEM_COMPARE:
            right = b.values(cond.compare.item, kind, ci)[bi] if cond.compare else None
            if right is None:
                return None
            return _ITEM_COMPARE[op][0](left, abs(right) if absolute else right)
        literals = self._literal_values(rule, cond)
        if op in ("IN", "NOT_IN"):
            return (left in literals) == (op == "IN")
        if op in _LITERAL_COMPARE:
            return _LITERAL_COMPARE[op](left, literals[0])
        raise RulesError(f"rule {rule.id}: operator {op} is not supported")

    def _in_column(self, rule: Rule, cond: Condition, scope: Category, i: int) -> bool | None:
        """The value occurs among the compare item's values (after row selection); skipped if there are none."""
        kind, ci = self._kind(cond)
        value = scope.values(cond.item.item, kind, ci)[i]
        if value is None or cond.compare is None:
            return None
        key = (rule.id, cond.ordinal)
        targets = self._column_sets.get(key)
        if targets is None:
            other = self.cat(cond.compare.category)
            column = other.values(cond.compare.item, kind, ci)
            targets = self._column_sets[key] = {
                column[j] for j in self._selected_rows(rule, other.name) if column[j] is not None
            }
        if not targets:
            return None
        return value in targets

    def _allowed_by_details(self, cond: Condition, scope: Category, i: int) -> list[str] | None:
        """The values the dictionary allows for item_name, given compare_item_name in the same row."""
        if cond.compare is None or scope.missing(i, cond.compare.item):
            return None
        details = self.dictionary.enum_details(cond.compare.tag)
        selected = (scope.get(i, cond.compare.item) or "").strip()
        if self.dictionary.case_insensitive(cond.compare.tag):
            folded = {value.upper(): allowed for value, allowed in details.items()}
            return folded.get(selected.upper())
        return details.get(selected)

    def _in_enumeration_details(self, cond: Condition, scope: Category, i: int) -> bool | None:
        """
        The detail of the dictionary's enumeration entry for the compare value lists
        the values allowed for item_name; skipped when there is no such detail.
        """
        allowed = self._allowed_by_details(cond, scope, i)
        value = scope.values(cond.item.item, TEXT)[i]
        if not allowed or value is None:
            return None
        if self.dictionary.case_insensitive(cond.item.tag):
            return value in {a.upper() for a in allowed}
        return value in allowed

    # -- reporting ----------------------------------------------------------------

    @staticmethod
    def _atom_display(c: Category, i: int) -> dict[str, str]:
        seq = c.text(i, "auth_seq_id", "label_seq_id", default="?")
        return {
            "model": c.text(i, "pdbx_PDB_model_num", default="1"),
            "chain": c.text(i, "auth_asym_id", "label_asym_id", default="?"),
            "residue": f"{seq}{c.text(i, 'pdbx_PDB_ins_code')}",
            "comp": c.text(i, "auth_comp_id", "label_comp_id", default="?"),
            "atom": c.text(i, "label_atom_id", "auth_atom_id"),
        }

    def _report_failure(
        self, rule: Rule, scope: Category, i: int, cond: Condition, witness: tuple[Category, int] | None
    ) -> None:
        # The THEN item is usually in the scope row; one in a partner category is
        # reported at the partner row it failed on.
        at, ai = (scope, i) if cond.item.category == scope.name or witness is None else witness
        if at.name != cond.item.category:
            at, ai = scope, i
        left = (at.get(ai, cond.item.item) or "").strip()
        shown = left
        typed = at.values(cond.item.item)[ai]
        if self.dictionary.kind(cond.item.tag) == NUMBER and typed is not None:
            shown = _fmt_number(typed)
        fields = {"value": shown, cond.item.item: shown, "left_value": left}
        if witness is not None and cond.compare is not None and cond.operator in ITEM_OPERATORS:
            right = (witness[0].get(witness[1], cond.compare.item) or "").strip()
            fields.update(item2CrossValue=right, right_value=right)
        if at.name == "atom_site":
            fields.update(self._atom_display(at, ai))
        message = _render(rule.message, **fields) or "Deposition rule failed"
        detail = None
        if cond.operator in _ITEM_COMPARE:
            detail = f"value={left}, condition {_ITEM_COMPARE[cond.operator][1]} {cond.compare}"
        elif cond.operator == "IN_COLUMN_OF":
            detail = f"value={left}"
        elif cond.operator == "IN_ENUMERATION_DETAILS_OF":
            allowed = ", ".join(self._allowed_by_details(cond, scope, i) or [])
            selected = (scope.get(i, cond.compare.item) or "").strip() if cond.compare else ""
            detail = f"value={left}, {cond.compare}={selected}; allowed: {allowed}"
        elif cond.operator == "MATCHES_TYPE":
            detail = f"value={left}; expected type: {', '.join(cond.values)}"
        line, loop_row = at.location(cond.item.item, ai) if len(at) else scope.location(cond.item.item, i)
        text = f"{message} ({detail})" if detail else message
        self.findings.append(Finding(rule.severity, cond.item.tag, text, rule.id, line, loop_row))

    # -- families -------------------------------------------------------------------

    def _check_rows(self, rule: Rule) -> None:
        """Report each applicable scope row whose THEN conditions fail."""
        scope = self.cat(rule.scope_category)
        for i, partners in self._applicable_rows(rule):
            failure = self._then_failure(rule, scope, i, partners)
            if failure is not None:
                self._report_failure(rule, scope, i, *failure)

    def _uniqueness(self, rule: Rule) -> None:
        """The THEN items marked UNIQUE form a (possibly composite) key that may not repeat."""
        keys = [c.item for c in rule.then if c.operator == "UNIQUE"]
        scope = self.cat(rule.scope_category)
        buckets: dict[tuple, list[int]] = defaultdict(list)
        for i, _ in self._applicable_rows(rule):
            values = tuple(scope.values(k.item, TEXT)[i] for k in keys)
            if None not in values:
                buckets[values].append(i)
        for rows in buckets.values():
            if len(rows) < 2:
                continue
            shown = ", ".join((scope.get(rows[0], k.item) or "").strip() for k in keys)
            message = _render(
                rule.message,
                duplicate_display=shown,
                key_columns=", ".join(k.item for k in keys),
                category=scope.name,
            )
            for i in rows:
                self.report(rule, scope, keys[0].item, i, message)

    def _aggregate(self, rule: Rule) -> None:
        """Aggregate the THEN item over group_by and test the THEN condition on each group's result."""
        cond = rule.then[0]
        function = _AGGREGATES.get((rule.param("aggregate") or "SUM").upper())
        if function is None or cond.operator not in _LITERAL_COMPARE:
            raise RulesError(f"rule {rule.id}: aggregate needs SUM or COUNT and a comparison with a value")
        scope = self.cat(rule.scope_category)
        values = scope.values(cond.item.item, NUMBER)
        group_by = rule.param_items("group_by")
        groups: dict[tuple, list[int]] = defaultdict(list)
        for i, _ in self._applicable_rows(rule):
            if values[i] is not None:  # rows with a missing value are excluded
                groups[tuple(scope.text(i, *_with_label_fallback(g.item)) for g in group_by)].append(i)
        limit = self._literal_values(rule, cond)[0]
        for members in groups.values():
            total = function([values[i] for i in members])
            total = round(total, 4) if isinstance(total, float) else total
            if _LITERAL_COMPARE[cond.operator](total, limit):
                continue
            fields = self._atom_display(scope, members[0]) if scope.name == "atom_site" else {}
            message = _render(rule.message, total=_fmt_number(total), **fields)
            for i in members:
                self.report(rule, scope, cond.item.item, i, message)

    # -- procedural ---------------------------------------------------------------

    def _proc_seq_model_mismatch(self, rule: Rule) -> None:
        """
        Compare modelled polymer residues in _atom_site with _entity_poly_seq.

        Residues are mapped by label_seq_id when every residue of a chain has
        one; otherwise the chain is slid along the sequence to the offset with
        the most matching residue types.
        """
        entity = self.cat("entity")
        entity_types = {entity.get(i, "id"): (entity.get(i, "type") or "").lower() for i in range(len(entity))}

        eps = self.cat(rule.param("target_category") or "entity_poly_seq")
        poly_seq: dict[str, list[tuple[int, str]]] = defaultdict(list)
        poly_mons: dict[tuple[str, int], set[str]] = defaultdict(set)
        for i in range(len(eps)):
            eid, mon = eps.text(i, "entity_id"), eps.text(i, "mon_id").upper()
            try:
                num = int(eps.text(i, "num"))
            except ValueError:
                continue
            if eid and mon:
                poly_seq[eid].append((num, mon))
                poly_mons[(eid, num)].add(mon)
        if not poly_mons:
            return
        for seq in poly_seq.values():
            seq.sort()

        atoms = self.cat(rule.param("source_category") or rule.scope_category)
        chains: dict[str, dict[tuple[int, str], dict]] = defaultdict(dict)
        for i in range(len(atoms)):
            eid = atoms.text(i, "label_entity_id")
            if eid not in poly_seq or entity_types.get(eid) in ("non-polymer", "water"):
                continue
            comp_item = "label_comp_id" if atoms.get(i, "label_comp_id") is not None else "auth_comp_id"
            comp = (atoms.get(i, comp_item) or "").strip().upper()
            if atoms.get(i, comp_item) is None:
                continue
            try:
                auth_seq = int(atoms.text(i, "auth_seq_id", "label_seq_id"))
            except ValueError:
                continue
            try:
                label_seq: int | None = int(atoms.text(i, "label_seq_id"))
            except ValueError:
                label_seq = None
            chain = atoms.text(i, "auth_asym_id", "label_asym_id", default="?")
            ins = atoms.text(i, "pdbx_PDB_ins_code")
            preferred = atoms.text(i, "label_atom_id", "auth_atom_id").upper() in ("CA", "C1'", "P")
            res = chains[chain].get((auth_seq, ins))
            if res is None:
                chains[chain][(auth_seq, ins)] = {
                    "chain": chain,
                    "entity_id": eid,
                    "residue": f"{auth_seq}{ins}",
                    "comp": comp,
                    "label_seq": label_seq,
                    "row": i,
                    "comp_item": comp_item,
                    "preferred": preferred,
                }
            else:
                if res["label_seq"] is None:
                    res["label_seq"] = label_seq
                if preferred and not res["preferred"]:
                    res.update(row=i, comp_item=comp_item, preferred=True)

        def emit(res: dict, sequence_comp: str | None) -> None:
            template = rule.message if sequence_comp else rule.param("missing_message")
            message = _render(
                template,
                chain=res["chain"],
                comp=res["comp"],
                residue=res["residue"],
                sequence_comp=sequence_comp or "",
            )
            self.report(rule, atoms, res["comp_item"], res["row"], message)

        for residue_map in chains.values():
            residues = [residue_map[k] for k in sorted(residue_map)]
            if all(res["label_seq"] is not None for res in residues):
                for res in residues:
                    allowed = poly_mons.get((res["entity_id"], res["label_seq"]))
                    if not allowed:
                        emit(res, None)
                    elif res["comp"] not in allowed:
                        emit(res, sorted(allowed)[0])
                continue
            seq = [mon for _, mon in poly_seq.get(residues[0]["entity_id"], [])]
            if not seq:
                continue
            n, m = len(seq), len(residues)
            best_start, best_score = 0, -1
            for start in range(max(n - m + 1, 1)):
                score = sum(1 for j, res in enumerate(residues) if start + j < n and seq[start + j] == res["comp"])
                if score > best_score:
                    best_start, best_score = start, score
            for j, res in enumerate(residues):
                idx = best_start + j
                if idx >= n:
                    emit(res, None)
                elif seq[idx] != res["comp"]:
                    emit(res, seq[idx])


def validate(
    path: Path,
    rules_path: Path | None = None,
    dictionary_path: Path | None = None,
    context: Context | None = None,
    rules: RuleSet | None = None,
    lenient: bool = False,
) -> list[Finding]:
    """
    Run all deposition rules on the first data block of an mmCIF file.

    rules is a RuleSet from load_rules(); without it the store at rules_path
    (or the packaged one) is loaded against the PDBx/mmCIF dictionary at
    dictionary_path (or the packaged v50 dictionary). context gives the
    requested accession codes for experiment_code rules, which are skipped
    without one. lenient=True lets gemmi accept duplicate tags (the last value
    wins) instead of rejecting the file.
    """
    rules = rules or load_rules(rules_path, dictionary_path)
    doc = cif.read_file(str(path), check_level=0) if lenient else cif.read(str(path))
    return CrossChecker(doc[0], rules, context).run()


def iter_errors(findings: Iterable[Finding]) -> Iterable[Finding]:
    return (f for f in findings if f.severity == "error")
