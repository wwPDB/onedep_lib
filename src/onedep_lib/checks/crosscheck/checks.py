"""
Run the deposition rules against an mmCIF block parsed by gemmi.

Each rule has a family (_pdbx_deposition_rule.family) and WHEN / THEN / UNLESS
conditions: a rule is checked on the rows where every WHEN condition holds
(any, for an OR group) and no UNLESS condition does, and reports the THEN
conditions that fail. WHEN conditions on another category are matched through
the rule's join_item / join_other_item parameters when it has them, and
against any row of that category otherwise.

The input file is read with gemmi.cif; each category is accessed through
gemmi's mmCIF table view (Block.find_mmcif_category). Values are read lazily,
one column at a time, and unquoted with gemmi.cif.as_string. Unquoted '?' and
'.' are treated as missing (gemmi.cif.is_null), so a quoted '?' is a value.

gemmi records line numbers per item or loop, not per value. A finding on a
key-value item carries that item's line; a finding inside a loop carries the
line of the loop and the 1-based row number.
"""

from __future__ import annotations

import operator
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from gemmi import cif

from onedep_lib.checks.crosscheck.rules import (
    Condition,
    Rule,
    RulesError,
    load_dictionary_enum_details,
    load_rules,
    split_item,
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
    """Selectors for rules gated on experiment, entry subtype, or requested codes."""

    experiment_modes: set[str]
    entry_subtypes: set[str]
    requested_codes: set[str]

    @classmethod
    def empty(cls) -> Context:
        return cls(set(), set(), set())


# Comparison against a constant value.
_ORDER = {"LT": operator.lt, "LE": operator.le, "GT": operator.gt, "GE": operator.ge}
# Comparison against another item, and the symbol of its violation (for messages).
_ITEM_ORDER = {
    "LT_ITEM": (operator.lt, ">="),
    "LE_ITEM": (operator.le, ">"),
    "GT_ITEM": (operator.gt, "<="),
    "GE_ITEM": (operator.ge, "<"),
    "EQ_ITEM": (operator.eq, "!="),
}

# Accession formats used by the procedural accession rules: (regex, match lowercased).
_FORMATS = {
    "pdb_id": (r"^(pdb_0000)?[\w\d]{4}$", True),
    "emdb_id": (r"^emd-\d+$", True),
    "deposition_id": (r"^D_1\d{9}$", False),
    "genbank_id": (r"^([A-Za-z]{2}\d{6}|[A-Za-z]\d{5})\.\d$", False),
    "uniprot_id": (r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})(-\d+)?$", False),
    "pdbdev_id": (r"^pdbdev_[\d]{8}$", True),
    "alphafold_id": (
        r"^((AF-)?([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})(-F[0-9])?)?$",
        False,
    ),
    "modelarchive_id": (r"^(ma-[\w\d]{5})?$", True),
}

_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?::(\d{1,2}):(\d{1,2}))?$")

# _exptl.method -> experiment mode token used by the experiment_modes parameter.
_METHOD_MODES = {
    "X-RAY DIFFRACTION": "xray",
    "NEUTRON DIFFRACTION": "nd",
    "FIBER DIFFRACTION": "fd",
    "ELECTRON CRYSTALLOGRAPHY": "ec",
    "ELECTRON MICROSCOPY": "em",
    "SOLUTION NMR": "nmr",
    "SOLID-STATE NMR": "ssnmr",
}
# _em_experiment.reconstruction_method -> subtype used by makeMandatorySubtypes.
_EM_SUBTYPES = {
    "SINGLE PARTICLE": "EM-single_part",
    "HELICAL": "EM-helical",
    "SUBTOMOGRAM AVERAGING": "EM-subtomogram",
    "CRYSTALLOGRAPHY": "EC",
}


def _empty(value: str | None) -> bool:
    return value is None or not value.strip()


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _date(value: str | None) -> tuple[int, ...] | None:
    m = _DATE.match(value.strip()) if value else None
    if not m:
        return None
    return tuple(int(g) if g is not None else 0 for g in m.groups())


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


def _format_ok(fmt: str, value: str) -> bool:
    pattern, lower = _FORMATS.get(fmt, (None, False))
    if pattern is None:
        return False
    return re.match(pattern, value.lower() if lower else value) is not None


def _with_label_fallback(item: str) -> tuple[str, ...]:
    """An auth_* item and, as in mmCIF, its label_* counterpart for when it has no value."""
    return (item, "label_" + item[5:]) if item.lower().startswith("auth_") else (item,)


def _combine(conditions: list[Condition], results: list[bool]) -> bool:
    """AND the results, or OR them when the conditions form an OR group."""
    if any(c.group_operator == "OR" for c in conditions):
        return any(results)
    return all(results)


class Category:
    """A gemmi mmCIF category, read column by column."""

    def __init__(self, block: cif.Block, name: str):
        self.name = name
        self.block = block
        self.table = block.find_mmcif_category(f"_{name}.")
        prefix = len(name) + 2
        # mmCIF tags are case-insensitive; keep the file's spelling for line lookup.
        self.tags = {tag[prefix:].lower(): tag for tag in self.table.tags}
        self.index = {tag[prefix:].lower(): i for i, tag in enumerate(self.table.tags)}
        self.size = len(self.table) if self.index else 0
        self.is_loop = self.size > 0 and self.table.loop is not None
        self._columns: dict[str, list[str | None]] = {}

    def __len__(self) -> int:
        return self.size

    def has(self, item: str) -> bool:
        return item.lower() in self.index

    def column(self, item: str) -> list[str | None]:
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

    def get(self, row: int, item: str) -> str | None:
        return self.column(item)[row]

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
    """Runs every enabled rule from the rule store against one data block."""

    def __init__(
        self,
        block: cif.Block,
        rules: list[Rule],
        context: Context,
        dict_enum: dict[str, dict[str, list[str]]] | None = None,
    ):
        self.block = block
        self.rules = rules
        self.context = context
        self.dict_enum = dict_enum
        self.findings: list[Finding] = []
        self._categories: dict[str, Category] = {}
        # (category, item, row) already reported by a rule with break_flag YES.
        self._broken: set[tuple[str, str, int]] = set()

    def cat(self, name: str) -> Category:
        c = self._categories.get(name)
        if c is None:
            c = self._categories[name] = Category(self.block, name)
        return c

    def report(self, rule: Rule, cat: Category, item: str, row: int, message: str) -> None:
        line, loop_row = cat.location(item, row)
        self.findings.append(Finding(rule.severity, cat.full(item), message, rule.id, line, loop_row))

    def run(self) -> list[Finding]:
        for rule in self.rules:
            if not rule.enabled:
                continue
            check = getattr(self, f"_family_{rule.family}", None)
            if check is None:
                raise RulesError(f"rule {rule.id}: unknown family {rule.family!r}")
            check(rule)
        return self.findings

    # -- conditions ---------------------------------------------------------

    def _evaluate(self, cond: Condition, c: Category, i: int) -> bool | None:
        """Whether row i of c satisfies cond; None when it cannot be decided (e.g. not a number)."""
        op = cond.operator
        if op == "EXISTS":
            return c.has(cond.item)
        if op == "NOT_EXISTS":
            return not c.has(cond.item)
        value = c.get(i, cond.item)
        if op == "EMPTY":
            return _empty(value)
        if op == "NOT_EMPTY":
            return not _empty(value)
        if value is None:
            return None
        value = value.strip()
        if op in ("EQ", "NE", "IN", "NOT_IN"):
            return (value in cond.values) == (op in ("EQ", "IN"))
        if op in _ORDER:
            left, right = _number(value), _number(cond.value)
            return None if left is None or right is None else _ORDER[op](left, right)
        if op in ("MATCHES", "NOT_MATCHES"):
            return (re.search(cond.value or "", _unwrap(value)) is not None) == (op == "MATCHES")
        if op in _ITEM_ORDER and cond.other_category == c.name:
            left, right = _number(value), _number(c.get(i, cond.other_item or ""))
            return None if left is None or right is None else _ITEM_ORDER[op][0](left, right)
        raise RulesError(f"operator {op} is not supported here ({cond.name})")

    def _join(self, rule: Rule, c: Category, i: int) -> list[int]:
        """Rows of the join_other_item category joined to row i of c (whose key is join_item)."""
        key = rule.param_item("join_item")
        other = rule.param_item("join_other_item")
        if key is None or other is None:
            return []
        return self._matching_rows(
            c, i, key[1], self.cat(other[0]), other[1], rule.param("fallback") == "single_row_if_key_missing"
        )

    def _clause(self, rule: Rule, conditions: list[Condition], c: Category, i: int) -> bool:
        """
        Evaluate WHEN or UNLESS conditions for row i of category c.

        A condition on c is tested on row i. A condition on another category is
        tested on the rows joined to row i when the rule joins to that category,
        and on any of its rows otherwise.
        """
        other = rule.param_item("join_other_item")
        results = []
        for cond in conditions:
            if cond.category == c.name:
                results.append(bool(self._evaluate(cond, c, i)))
                continue
            target = self.cat(cond.category)
            rows = self._join(rule, c, i) if other and other[0] == cond.category else range(len(target))
            results.append(any(self._evaluate(cond, target, j) for j in rows))
        return _combine(conditions, results)

    def _applies(self, rule: Rule, c: Category, i: int) -> bool:
        """WHEN holds and UNLESS does not for row i of c."""
        if rule.when and not self._clause(rule, rule.when, c, i):
            return False
        return not (rule.unless and self._clause(rule, rule.unless, c, i))

    @staticmethod
    def _matching_rows(src: Category, i: int, s_key: str, tgt: Category, t_key: str, fallback: bool) -> list[int]:
        """
        Join source row i to target rows on s_key == t_key.

        With the single-row fallback, a target category with exactly one row and
        no join key is used when the key match finds nothing (and the source key
        is present, or the source has one row too).
        """
        key = src.get(i, s_key)
        if not _empty(key):
            rows = [j for j in range(len(tgt)) if not _empty(tgt.get(j, t_key)) and tgt.get(j, t_key) == key]
            if rows:
                return rows
        if not fallback or len(tgt) != 1 or not _empty(tgt.get(0, t_key)):
            return []
        if _empty(key) and len(src) != 1:
            return []
        return [0]

    def _in_scope(self, rule: Rule) -> bool:
        """Experiment gating from experiment_scope / experiment_code / experiment_modes."""
        scope = (rule.param("experiment_scope") or "").lower()
        if scope == "all":
            return True
        if scope == "coded":
            code = rule.param("experiment_code")
            return bool(code) and code.upper() in self.context.requested_codes
        modes = {m.lower() for m in rule.param_list("experiment_modes")}
        return not modes or bool(modes & self.context.experiment_modes)

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

    # -- pairwise_order -----------------------------------------------------

    def _family_pairwise_order(self, rule: Rule) -> None:
        """
        THEN `item OP_ITEM other_item`: numbers, or dates when both values are dates.

        With join_item / join_other_item, row i is compared with the joined rows
        of the other category; otherwise with the same row when both items are in
        one category, and with every row of the other category when they are not.
        """
        joined = rule.param("join_item") is not None
        for cond in rule.then:
            if cond.operator not in _ITEM_ORDER or not cond.other_category:
                raise RulesError(f"rule {rule.id}: pairwise_order needs an *_ITEM operator")
            holds, violation = _ITEM_ORDER[cond.operator]
            left_cat, right_cat = self.cat(cond.category), self.cat(cond.other_category)
            if not len(left_cat) or not len(right_cat):
                continue
            right_item = cond.other_item or ""
            for i in range(len(left_cat)):
                if not self._applies(rule, left_cat, i):
                    continue
                left_text = left_cat.get(i, cond.item)
                if joined:
                    self._compare_joined(rule, cond, holds, left_cat, i, left_text, right_cat)
                    continue
                rows = [i] if left_cat is right_cat else range(len(right_cat))
                left_num, left_date = _number(left_text), _date(left_text)
                for j in rows:
                    right_text = right_cat.get(j, right_item)
                    if left_num is not None:
                        right_num = _number(right_text)
                        if right_num is None or holds(left_num, right_num):
                            continue
                        text = _render(rule.message, item2CrossValue=right_text) or "Cross-check failed"
                        message = f"{text} (value={left_text}, condition {violation} {cond.other_name})"
                    elif left_date is not None:
                        right_date = _date(right_text)
                        if right_date is None or holds(left_date, right_date):
                            continue
                        message = _render(rule.message, left_value=left_text.strip(), right_value=right_text.strip())
                    else:
                        break
                    self.report(rule, left_cat, cond.item, i, message)
                    break

    def _compare_joined(
        self, rule: Rule, cond: Condition, holds, left_cat: Category, i: int, left_text: str | None, right_cat
    ) -> None:
        rows = self._join(rule, left_cat, i)
        if not rows:
            return
        right_values = [right_cat.get(j, cond.other_item or "") for j in rows]
        left = _number(left_text)
        rights = [n for n in map(_number, right_values) if n is not None]
        if left is None or not rights:
            return
        if rule.flag("absolute"):
            left, rights = abs(left), [abs(n) for n in rights]
        if all(holds(left, n) for n in rights):
            return
        display = next((v for v in right_values if v is not None), "")
        self.report(
            rule, left_cat, cond.item, i, _render(rule.message, item2CrossValue=display) or "Cross-check failed"
        )

    # -- uniqueness ---------------------------------------------------------

    def _family_uniqueness(self, rule: Rule) -> None:
        """The THEN items marked UNIQUE form a (possibly composite) key that may not repeat."""
        keys = [cond for cond in rule.then if cond.operator == "UNIQUE"]
        if not keys:
            return
        c = self.cat(keys[0].category)
        if len(c) < 2:
            return
        buckets: dict[tuple, list[int]] = defaultdict(list)
        for i in range(len(c)):
            values = [c.get(i, k.item) for k in keys]
            if all(v is not None for v in values):
                buckets[tuple(v.strip() for v in values)].append(i)
        for key, rows in buckets.items():
            if len(rows) < 2:
                continue
            message = _render(
                rule.message,
                duplicate_display=", ".join(key),
                key_columns=", ".join(k.item for k in keys),
                category=c.name,
            )
            for i in rows:
                self.report(rule, c, keys[0].item, i, message)

    # -- conditional_presence -----------------------------------------------

    def _family_conditional_presence(self, rule: Rule) -> None:
        """
        THEN items must be non-empty on the rows where the rule applies.

        Without WHEN conditions, mandatory_scope rules require the THEN items in
        any row that has a value, and an OR group requires one of the THEN items
        somewhere in the category.
        """
        if not rule.then:
            return
        if rule.param("mandatory_scope"):
            self._mandatory(rule)
            return
        c = self.cat(rule.then[0].category)
        if not len(c) or any(len(self.cat(s)) for s in rule.param_list("skip_if_any_category_present")):
            return
        if not rule.when and any(cond.group_operator == "OR" for cond in rule.then):
            if not any(not _empty(v) for cond in rule.then for v in c.column(cond.item)):
                self.report(rule, c, rule.then[0].item, 0, _render(rule.message))
            return
        message = _render(rule.message) or "Required value missing"
        breaks = rule.flag("break_flag")
        for i in range(len(c)):
            if not self._applies(rule, c, i):
                continue
            for cond in rule.then:
                if self._evaluate(cond, c, i):
                    continue
                key = (c.name, cond.item.lower(), i)
                if breaks and key in self._broken:
                    continue
                self.report(rule, c, cond.item, i, message)
                if breaks:
                    self._broken.add(key)

    def _mandatory(self, rule: Rule) -> None:
        """If a row carries any value (outside exclude_items), every THEN item needs one."""
        if rule.param("mandatory_scope") == "makeMandatorySubtypes":
            if rule.param("subtype") not in self.context.entry_subtypes:
                return
        c = self.cat(rule.then[0].category)
        exclude = {split_item(name)[1].lower() for name in rule.param_list("exclude_items")}
        signal_items = [item for item in c.index if item not in exclude]
        message = _render(rule.message)
        for i in range(len(c)):
            if not any(c.get(i, item) is not None for item in signal_items):
                continue
            for cond in rule.then:
                if not self._evaluate(cond, c, i):
                    self.report(rule, c, cond.item, i, message)

    # -- conditional_value and format ---------------------------------------

    def _family_conditional_value(self, rule: Rule) -> None:
        """THEN values present on the rows where the rule applies must satisfy their operator."""
        for cond in rule.then:
            c = self.cat(cond.category)
            numeric = cond.operator in _ORDER
            for i in range(len(c)):
                value = c.get(i, cond.item)
                if _empty(value) or not self._applies(rule, c, i):
                    continue
                if self._evaluate(cond, c, i) is not False:
                    continue
                shown = _fmt_number(float(value)) if numeric else value.strip()
                fields = self._atom_display(c, i) if c.name == "atom_site" else {}
                self.report(
                    rule, c, cond.item, i, _render(rule.message, **{cond.item: shown, "value": shown}, **fields)
                )

    _family_format = _family_conditional_value

    # -- cross_reference ----------------------------------------------------

    def _family_cross_reference(self, rule: Rule) -> None:
        """THEN `item IN_COLUMN_OF other_item`: each value must occur in the other item."""
        if not self._in_scope(rule):
            return
        if (rule.param("xref_type") or "cross") != "cross" or (rule.param("xref_subtype") or "full") != "full":
            return
        dictionary = rule.param("other_item_domain") == "DICTIONARY_ENUMERATION"
        for cond in rule.then:
            if cond.operator != "IN_COLUMN_OF" or not cond.other_category:
                raise RulesError(f"rule {rule.id}: cross_reference needs IN_COLUMN_OF")
            if dictionary:
                self._dictionary_pairing(rule, cond)
                continue
            src, tgt = self.cat(cond.category), self.cat(cond.other_category)
            targets = {v for v in tgt.column(cond.other_item or "") if v is not None}
            if not len(src) or not targets:
                continue
            for i, value in enumerate(src.column(cond.item)):
                if value is not None and value not in targets:
                    self.report(rule, src, cond.item, i, f"{_render(rule.message)} (value={value})")

    def _dictionary_pairing(self, rule: Rule, cond: Condition) -> None:
        """
        The value's `_item_enumeration.detail` in the dictionary lists the values
        allowed in other_item (same row). Needs a dictionary (--dict).
        """
        if self.dict_enum is None:
            return
        c = self.cat(cond.category)
        details = self.dict_enum.get(cond.name)
        if not details:
            return
        message = _render(rule.message)
        for i in range(len(c)):
            value, other = c.get(i, cond.item), c.get(i, cond.other_item or "")
            if value is None:
                continue
            allowed = details.get(value)
            if not allowed:
                detail = f"value={value}; the dictionary pairs no {cond.other_name} values with it"
            elif other is not None and other not in allowed:
                detail = f"value={value}, {cond.other_name}={other}; allowed: {', '.join(sorted(set(allowed)))}"
            else:
                continue
            self.report(rule, c, cond.item, i, f"{message} ({detail})")

    # -- aggregate ----------------------------------------------------------

    def _family_aggregate(self, rule: Rule) -> None:
        """Sum the THEN item over group_by and compare the total with threshold."""
        cond = rule.then[0]
        if (rule.param("aggregate") or "SUM").upper() != "SUM" or cond.operator not in _ORDER:
            raise RulesError(f"rule {rule.id}: only SUM with LT/LE/GT/GE is supported")
        c = self.cat(cond.category)
        if not len(c):
            return
        group_by = [split_item(name)[1] for name in (rule.param("group_by") or "").split("+") if name.strip()]
        threshold = float(rule.param("threshold") or cond.value or 0)
        groups: dict[tuple, list[tuple[int, float]]] = defaultdict(list)
        for i, text in enumerate(c.column(cond.item)):
            number = _number(text)
            if number is not None:
                groups[tuple(c.text(i, *_with_label_fallback(item)) for item in group_by)].append((i, number))
        for members in groups.values():
            total = round(sum(n for _, n in members), 4)
            if _ORDER[cond.operator](total, threshold):
                continue
            first = members[0][0]
            fields = self._atom_display(c, first) if c.name == "atom_site" else {}
            message = _render(rule.message, total=_fmt_number(total), **fields)
            for i, _ in members:
                self.report(rule, c, cond.item, i, message)

    # -- procedural ---------------------------------------------------------

    def _family_procedural(self, rule: Rule) -> None:
        check = getattr(self, f"_proc_{rule.method}", None)
        if check is None:
            raise RulesError(f"rule {rule.id}: unknown procedural method {rule.method!r}")
        check(rule)

    def _param_category(self, rule: Rule) -> tuple[Category, str]:
        target = rule.param_item("item")
        if target is None:
            raise RulesError(f"rule {rule.id}: missing 'item' parameter")
        return self.cat(target[0]), target[1]

    def _proc_wavelength_protocol_consistency(self, rule: Rule) -> None:
        """A wavelength list must hold several values (requires=multi) or one (requires=single)."""
        c, item = self._param_category(rule)
        multi = rule.param("requires") == "multi"
        for i in range(len(c)):
            value = c.get(i, item)
            if _empty(value) or not self._applies(rule, c, i):
                continue
            if ("," in value or "-" in value) != multi:
                self.report(rule, c, item, i, _render(rule.message))

    def _proc_accession_format_rule(self, rule: Rule) -> None:
        """A non-empty value must match any (format_match=ANY) or all of the named formats."""
        c, item = self._param_category(rule)
        formats = rule.param_list("format")
        match = all if (rule.param("format_match") or "ANY").upper() == "ALL" else any
        for i in range(len(c)):
            value = c.get(i, item)
            if _empty(value) or not self._applies(rule, c, i):
                continue
            if not match(_format_ok(f, value.strip()) for f in formats):
                self.report(rule, c, item, i, _render(rule.message) or "Invalid accession code format.")

    _proc_conditional_accession_format_rule = _proc_accession_format_rule

    def _proc_sequence_predicate(self, rule: Rule) -> None:
        c, item = self._param_category(rule)
        predicate = rule.param("predicate")
        if predicate != "homopolymer_ala":
            raise RulesError(f"rule {rule.id}: unknown sequence predicate {predicate!r}")
        for i in range(len(c)):
            # Sequences in text fields are wrapped over several lines.
            seq = "".join((c.get(i, item) or "").split())
            if seq and set(seq.upper()) == {"A"} and self._applies(rule, c, i):
                self.report(rule, c, item, i, _render(rule.message))

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

        atoms = self.cat(rule.param("source_category") or "atom_site")
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


def detect_context(block: cif.Block) -> Context:
    """Derive experiment modes and EM subtypes from _exptl and _em_experiment."""
    exptl = Category(block, "exptl")
    em = Category(block, "em_experiment")
    methods = {(v or "").strip().upper() for v in exptl.column("method")}
    modes = {_METHOD_MODES[m] for m in methods if m in _METHOD_MODES}
    subtypes = {
        _EM_SUBTYPES[v.strip().upper()]
        for v in em.column("reconstruction_method")
        if v and v.strip().upper() in _EM_SUBTYPES
    }
    if "ec" in modes:
        subtypes.add("EC")
    return Context(modes, subtypes, set())


def validate(
    path: Path,
    rules_path: Path | None = None,
    dictionary_path: Path | None = None,
    context: Context | None = None,
    detect: bool = False,
    rules: list[Rule] | None = None,
    lenient: bool = False,
) -> list[Finding]:
    """
    Run all deposition rules on the first data block of an mmCIF file.

    Without a context (or detect=True), rules gated on experiment mode, entry
    subtype or requested code are skipped. dictionary_path enables the rules
    checked against dictionary enumerations. lenient=True lets gemmi accept
    duplicate tags (the last value wins) instead of rejecting the file.
    """
    rules = rules or load_rules(rules_path)
    doc = cif.read_file(str(path), check_level=0) if lenient else cif.read(str(path))
    block = doc[0]
    ctx = context or Context.empty()
    if detect:
        found = detect_context(block)
        ctx = Context(
            ctx.experiment_modes | found.experiment_modes,
            ctx.entry_subtypes | found.entry_subtypes,
            ctx.requested_codes,
        )
    dict_enum = None
    if dictionary_path is not None:
        names = [
            cond.name for r in rules if r.param("other_item_domain") == "DICTIONARY_ENUMERATION" for cond in r.then
        ]
        dict_enum = load_dictionary_enum_details(dictionary_path, names)
    return CrossChecker(block, rules, ctx, dict_enum).run()


def iter_errors(findings: Iterable[Finding]) -> Iterable[Finding]:
    return (f for f in findings if f.severity == "error")
