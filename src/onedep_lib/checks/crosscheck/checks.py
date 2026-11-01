"""
Run the cross-check rule families against an mmCIF block parsed by gemmi.

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

from onedep_lib.checks.crosscheck.rules import load_dictionary_enum_details, load_rules


@dataclass
class Finding:
    severity: str  # "error" or "warning"
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


_OPS = {
    "<": operator.lt,
    ">": operator.gt,
    "<=": operator.le,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
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

# _exptl.method -> experiment mode token used by _validator_rule_cross_reference_expt.
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


def _severity(flag: str | None) -> str:
    return "warning" if (flag or "").strip().lower() == "soft" else "error"


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
    """Runs every rule family from the rule store against one data block."""

    def __init__(
        self,
        block: cif.Block,
        rules: dict[str, list],
        context: Context,
        dict_enum: dict[str, dict[str, list[str]]] | None = None,
    ):
        self.block = block
        self.rules = rules
        self.context = context
        self.dict_enum = dict_enum
        self.findings: list[Finding] = []
        self._categories: dict[str, Category] = {}

    def cat(self, name: str) -> Category:
        c = self._categories.get(name)
        if c is None:
            c = self._categories[name] = Category(self.block, name)
        return c

    def report(self, rule_id: str, severity: str, cat: Category, item: str, row: int, message: str) -> None:
        line, loop_row = cat.location(item, row)
        self.findings.append(Finding(severity, cat.full(item), message, rule_id, line, loop_row))

    def run(self) -> list[Finding]:
        self.pairwise()
        self.date_order()
        self.uniqueness()
        self.linked()
        self.conditional_required()
        self.conditional_regex()
        self.conditional_enum()
        self.conditional_category_item()
        self.make_mandatory()
        self.one_of()
        self.dictionary_enum()
        self.cross_reference()
        self.procedural()
        return self.findings

    # -- numeric and date comparisons ---------------------------------------

    def pairwise(self) -> None:
        """Operator states the violation: report when `source op target` holds."""
        for r in self.rules["pairwise"]:
            src, tgt = self.cat(r["source_category"]), self.cat(r["target_category"])
            if not len(src) or not len(tgt):
                continue
            op, s_item, t_item = r["operator"], r["source_item"], r["target_item"]
            compare = _OPS[op]
            target_full = tgt.full(t_item)
            for i in range(len(src)):
                left_text = src.get(i, s_item)
                left = _number(left_text)
                if left is None:
                    continue
                # Same category: compare within the row. Otherwise: against every target row.
                rows = [i] if src is tgt else range(len(tgt))
                hit = next(
                    (
                        tgt.get(j, t_item)
                        for j in rows
                        if _number(tgt.get(j, t_item)) is not None and compare(left, _number(tgt.get(j, t_item)))
                    ),
                    None,
                )
                if hit is None:
                    continue
                text = _render(r["message"], item2CrossValue=hit) or "Cross-check failed"
                self.report(
                    r["id"],
                    _severity(r["severity"]),
                    src,
                    s_item,
                    i,
                    f"{text} (value={left_text}, condition {op} {target_full})",
                )

    def date_order(self) -> None:
        violates = {"<=": operator.gt, "<": operator.ge, ">=": operator.lt, ">": operator.le}
        for r in self.rules["date_order"]:
            c = self.cat(r["category"])
            for i in range(len(c)):
                left_text, right_text = c.get(i, r["left_item"]), c.get(i, r["right_item"])
                left, right = _date(left_text), _date(right_text)
                if left is None or right is None or not violates[r["op"]](left, right):
                    continue
                message = _render(r["message"], left_value=left_text.strip(), right_value=right_text.strip())
                self.report(r["id"], _severity(r["severity"]), c, r["left_item"], i, message)

    def uniqueness(self) -> None:
        for r in self.rules["uniqueness"]:
            c = self.cat(r["category"])
            if len(c) < 2:
                continue
            keys = r["key_items"]
            buckets: dict[tuple, list[int]] = defaultdict(list)
            for i in range(len(c)):
                values = [c.get(i, k) for k in keys]
                if all(v is not None for v in values):
                    buckets[tuple(v.strip() for v in values)].append(i)
            for key, rows in buckets.items():
                if len(rows) < 2:
                    continue
                message = _render(
                    r["message"], duplicate_display=", ".join(key), key_columns=", ".join(keys), category=r["category"]
                )
                for i in rows:
                    self.report(r["id"], _severity(r["severity"]), c, keys[0], i, message)

    # -- linked presence / comparison across joined categories --------------

    @staticmethod
    def _blank(value: str | None) -> bool:
        return value is None or not value.strip()

    def _matching_rows(self, src: Category, i: int, s_key: str, tgt: Category, t_key: str, fallback: bool) -> list[int]:
        """
        Join source row i to target rows on s_key == t_key.

        With the single-row fallback, a target category with exactly one row and
        no join key is used when the key match finds nothing (and the source key
        is present, or the source has one row too).
        """
        key = src.get(i, s_key)
        if not self._blank(key):
            rows = [j for j in range(len(tgt)) if not self._blank(tgt.get(j, t_key)) and tgt.get(j, t_key) == key]
            if rows:
                return rows
        if not fallback or len(tgt) != 1 or not self._blank(tgt.get(0, t_key)):
            return []
        if self._blank(key) and len(src) != 1:
            return []
        return [0]

    def linked(self) -> None:
        by_source: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in self.rules["linked"]:
            by_source[(r["source_category"], r["source_item"])].append(r)

        for (s_cat, s_item), rules in by_source.items():
            src = self.cat(s_cat)
            for i in range(len(src)):
                source_value = src.get(i, s_item)
                for r in rules:
                    tgt = self.cat(r["target_category"])
                    if not len(tgt):
                        continue
                    rows = self._matching_rows(
                        src, i, r["cross"], tgt, r["cross2"], r["fallback"] == "single_row_if_key_missing"
                    )
                    if not rows:
                        continue
                    t_item, op = r["target_item"], r["operator"]
                    target_values = [tgt.get(j, t_item) for j in rows]
                    if op == "exists":
                        failed = source_value is None and any(v is not None for v in target_values)
                    else:
                        left = _number(source_value)
                        rights = [n for n in map(_number, target_values) if n is not None]
                        if left is None or not rights:
                            continue
                        if r["absolute"]:
                            left, rights = abs(left), [abs(n) for n in rights]
                        failed = any(_OPS[op](left, n) for n in rights)
                    if not failed:
                        continue
                    display = next((v for v in target_values if v is not None), "")
                    message = _render(r["text"], item2CrossValue=display) or "Cross-check failed"
                    self.report(r["id"], "warning" if r["warning"] else "error", src, s_item, i, message)
                    if r["break_flag"]:
                        break

    # -- conditional rules within one category ------------------------------

    def conditional_required(self) -> None:
        for r in self.rules["conditional_required"]:
            c = self.cat(r["category"])
            if not len(c) or any(len(self.cat(s)) for s in r["skip_if_present"]):
                continue
            message = _render(r["error_text"] or r["warning_text"] or "Required value missing")
            severity = "error" if r["error"] else "warning"
            for i in range(len(c)):
                if not all(c.get(i, item) in allowed for item, allowed in r["conditions"].items()):
                    continue
                for item in r["items"]:
                    if c.get(i, item) is None:
                        self.report(r["id"], severity, c, item, i, message)

    def conditional_regex(self) -> None:
        for r in self.rules["conditional_regex"]:
            c = self.cat(r["category"])
            for i in range(len(c)):
                value = c.get(i, r["item"])
                if c.get(i, r["condition_item"]) != r["condition_value"] or value is None:
                    continue
                if re.match(r["regex"], value):
                    continue
                message = _render(r["error_text"] or "Value does not match required pattern")
                self.report(r["id"], "error" if r["error"] else "warning", c, r["item"], i, message)

    def conditional_enum(self) -> None:
        """When the driver item has a given value, the affected item must be one of a list."""
        for r in self.rules["conditional_enum"]:
            c = self.cat(r["category"])
            driver_item, affected_item = r["driver_item"], r["affected_item"]
            for i in range(len(c)):
                driver, affected = c.get(i, driver_item), c.get(i, affected_item)
                if driver is None or affected is None:
                    continue
                for branch in r["branches"]:
                    if driver == branch["driver_value"] and affected not in branch["allowed"]:
                        self.report(
                            r["id"],
                            "error",
                            c,
                            affected_item,
                            i,
                            _render(branch["error_text"] or "Invalid value for conditional enumeration"),
                        )
                        break

    def conditional_category_item(self) -> None:
        """When any row of another category has a value, every row here must match a regex."""
        for r in self.rules["conditional_category_item"]:
            c = self.cat(r["category"])
            cond = self.cat(r["condition_category"])
            if not len(c) or r["condition_value"] not in cond.column(r["condition_item"]):
                continue
            message = _render(r["error_text"] or "Required value missing")
            for i in range(len(c)):
                value = c.get(i, r["item"])
                if value is not None and re.match(r["regex"], value):
                    continue
                self.report(r["id"], "error" if r["error"] else "warning", c, r["item"], i, message)

    # -- presence rules -----------------------------------------------------

    def make_mandatory(self) -> None:
        """If a row carries any value (outside `exclude`), every `to_check` item needs one."""
        for r in self.rules["make_mandatory"]:
            if r["scope"] == "makeMandatorySubtypes" and r["subtype"] not in self.context.entry_subtypes:
                continue
            c = self.cat(r["category"])
            exclude = {e.lower() for e in r["exclude"]}
            signal_items = [item for item in c.index if item not in exclude]
            for i in range(len(c)):
                if not any(c.get(i, item) is not None for item in signal_items):
                    continue
                for item in r["to_check"]:
                    if c.get(i, item) is None:
                        self.report(r["id"], "error", c, item, i, "No value present for this mandatory item.")

    def one_of(self) -> None:
        for r in self.rules["one_of"]:
            c = self.cat(r["category"])
            if len(c) and not any(v is not None for item in r["items"] for v in c.column(item)):
                self.report(r["id"], "error", c, r["items"][0], 0, "At least one related item must be provided.")

    def dictionary_enum(self) -> None:
        """Paired enumerations from the dictionary's _item_enumeration.detail (needs --dict)."""
        if self.dict_enum is None:
            return
        for r in self.rules["dictionary_enum"]:
            c = self.cat(r["category"])
            s_item, x_item = r["source_item"], r["cross_item"]
            details = self.dict_enum.get(c.full(s_item))
            if not details:
                continue
            for i in range(len(c)):
                value, cross = c.get(i, s_item), c.get(i, x_item)
                if value is None:
                    continue
                allowed = details.get(value)
                if not allowed:
                    self.report(
                        r["id"],
                        "error",
                        c,
                        s_item,
                        i,
                        f"Value '{value}' is not compatible with '{c.full(x_item)}' "
                        "because no dictionary cross-enumeration mapping is defined.",
                    )
                elif cross is not None and cross not in allowed:
                    self.report(
                        r["id"],
                        "error",
                        c,
                        s_item,
                        i,
                        f"Value '{value}' is not compatible with '{c.full(x_item)}={cross}'. "
                        f"Allowed values: {', '.join(sorted(set(allowed)))}.",
                    )

    def _selector_matches(self, r: dict) -> bool:
        expt = (r["expt"] or "").lower()
        if expt == "all":
            return True
        if expt == "coded":
            return bool(r["code"]) and r["code"].upper() in self.context.requested_codes
        modes = {m.lower() for m in r["modes"]}
        return bool(modes & self.context.experiment_modes)

    def cross_reference(self) -> None:
        """Every value of the source item must appear in the referenced item."""
        for r in self.rules["cross_reference"]:
            if r["type"] != "cross" or r["subtype"] != "full" or not self._selector_matches(r):
                continue
            src, tgt = self.cat(r["category"]), self.cat(r["cross_category"])
            targets = {v for v in tgt.column(r["cross_item"]) if v is not None}
            if not len(src) or not targets:
                continue
            for i, value in enumerate(src.column(r["item"])):
                if value is not None and value not in targets:
                    self.report(
                        r["id"],
                        "error",
                        src,
                        r["item"],
                        i,
                        f"Cross-reference value '{value}' does not exist in '{tgt.full(r['cross_item'])}'.",
                    )

    # -- procedural validators ----------------------------------------------

    def procedural(self) -> None:
        for r in self.rules["procedural"]:
            getattr(self, f"_proc_{r['kind']}")(r)

    def _proc_wavelength_protocol_consistency(self, r: dict) -> None:
        src, tgt = self.cat(r["source_category"]), self.cat(r["target_category"])
        if not len(src) or not len(tgt):
            return
        s_item, s_key, t_item, t_key = r["source_item"], r["source_key"], r["target_item"], r["target_key"]
        empty_protocols = {e["protocol"].strip().upper() for e in r["empty_list"]}
        for i in range(len(src)):
            value = src.get(i, s_item)
            if value is None:
                continue
            value = value.strip()
            key = src.get(i, s_key)
            rows = range(len(tgt)) if key is None else [j for j in range(len(tgt)) if tgt.get(j, t_key) == key]
            protocols = [tgt.get(j, t_item).strip().upper() for j in rows if tgt.get(j, t_item) is not None]
            if not protocols:
                continue
            if not value:
                if empty_protocols & set(protocols):
                    first = r["empty_list"][0]
                    self.report(r["id"], _severity(first["severity"]), src, s_item, i, _render(first["message"]))
                continue
            is_multi = "," in value or "-" in value
            for b in r["branches"]:
                if b["protocol"].strip().upper() not in protocols:
                    continue
                if (b["requires"] == "multi") != is_multi:
                    self.report(r["id"], _severity(b["severity"]), src, s_item, i, _render(b["message"]))
                    break

    def _proc_accession_format_rule(self, r: dict) -> None:
        c = self.cat(r["category"])
        for i in range(len(c)):
            value, driver = c.get(i, r["item"]), c.get(i, r["driver_item"])
            if value is None or driver is None or not value.strip():
                continue
            for b in r["branches"]:
                if driver.strip().upper() != b["driver_value"].strip().upper():
                    continue
                if not any(_format_ok(f, value.strip()) for f in b["formats"]):
                    self.report(
                        r["id"],
                        _severity(b["severity"]),
                        c,
                        r["item"],
                        i,
                        _render(b["message"] or "Invalid accession code format."),
                    )
                    break

    def _row_conditions_met(self, c: Category, i: int, conditions: dict[str, str]) -> bool:
        for item, expected in conditions.items():
            actual = c.get(i, item)
            if actual is None or actual.strip().lower() != expected.strip().lower():
                return False
        return True

    def _proc_conditional_accession_format_rule(self, r: dict) -> None:
        c = self.cat(r["category"])
        for i in range(len(c)):
            value = (c.get(i, r["item"]) or "").strip()
            if not value:
                continue
            for b in r["branches"]:
                if not self._row_conditions_met(c, i, b["conditions"]):
                    continue
                if not any(_format_ok(f, value) for f in b["formats"]):
                    self.report(
                        r["id"],
                        _severity(b["severity"]),
                        c,
                        r["item"],
                        i,
                        _render(b["message"] or "Invalid accession code format."),
                    )
                    break

    def _proc_conditional_value_rule(self, r: dict) -> None:
        c = self.cat(r["category"])
        for i in range(len(c)):
            value = (c.get(i, r["item"]) or "").strip()
            if not value:
                continue
            for b in r["branches"]:
                if not self._row_conditions_met(c, i, b["conditions"]):
                    continue
                if value.lower() not in {a.strip().lower() for a in b["allowed"]}:
                    self.report(
                        r["id"],
                        _severity(b["severity"]),
                        c,
                        r["item"],
                        i,
                        _render(b["message"] or "Invalid value for condition."),
                    )
                    break

    def _proc_sequence_predicate_warning(self, r: dict) -> None:
        c = self.cat(r["category"])
        for i in range(len(c)):
            value = c.get(i, r["item"])
            # Sequences in text fields are wrapped over several lines.
            seq = "".join((value or "").split())
            if not seq:
                continue
            for b in r["branches"]:
                if b["predicate"] == "homopolymer_ala":
                    matched = set(seq.upper()) == {"A"}
                elif b["predicate"] == "substring":
                    matched = bool(b["substring"]) and b["substring"] in seq
                else:
                    continue
                if matched:
                    self.report(r["id"], _severity(b["severity"]), c, r["item"], i, _render(b["message"]))

    @staticmethod
    def _fmt_occ(value: float) -> str:
        return f"{round(value, 4):.4f}".rstrip("0").rstrip(".") or "0"

    def _proc_atom_site_occupancy(self, r: dict) -> None:
        """Sum occupancy over alternate locations of an atom (> limit: error); flag low values."""
        c = self.cat(r["category"])
        occ_item = r["item"]
        groups: dict[tuple, list[tuple[int, float, dict]]] = defaultdict(list)
        for i, occ_text in enumerate(c.column(occ_item)):
            occ = _number(occ_text)
            atom = c.text(i, "label_atom_id", "auth_atom_id")
            if occ is None or not atom:
                continue
            ins = c.text(i, "pdbx_PDB_ins_code")
            seq = c.text(i, "auth_seq_id", "label_seq_id", default="?")
            display = {
                "model": c.text(i, "pdbx_PDB_model_num", default="1"),
                "chain": c.text(i, "auth_asym_id", "label_asym_id", default="?"),
                "residue": f"{seq}{ins}",
                "comp": c.text(i, "auth_comp_id", "label_comp_id", default="?"),
                "atom": atom,
            }
            key = (display["model"], display["chain"], seq, ins, atom)
            groups[key].append((i, occ, display))

        over, below = r["over_one"], r["below"]
        for members in groups.values():
            total = round(sum(occ for _, occ, _ in members), 4)
            if over and total > float(over["limit"]):
                message = _render(over["message"], total=self._fmt_occ(total), **members[0][2])
                for i, _, _ in members:
                    self.report(r["id"], _severity(over["severity"]), c, occ_item, i, message)
            if below:
                for i, occ, display in members:
                    if round(occ, 4) < float(below["limit"]):
                        message = _render(below["message"], occupancy=self._fmt_occ(occ), **display)
                        self.report(r["id"], _severity(below["severity"]), c, occ_item, i, message)

    def _proc_sequence_model_mismatch(self, r: dict) -> None:
        """
        Compare modelled polymer residues in _atom_site with _entity_poly_seq.

        Residues are mapped by label_seq_id when every residue of a chain has
        one; otherwise the chain is slid along the sequence to the offset with
        the most matching residue types.
        """
        entity = self.cat("entity")
        entity_types = {entity.get(i, "id"): (entity.get(i, "type") or "").lower() for i in range(len(entity))}

        eps = self.cat("entity_poly_seq")
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

        atoms = self.cat("atom_site")
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

        severity = _severity(r["severity"])

        def emit(res: dict, sequence_comp: str | None) -> None:
            template = r["mismatch_message"] if sequence_comp else r["missing_message"]
            message = _render(
                template,
                chain=res["chain"],
                comp=res["comp"],
                residue=res["residue"],
                sequence_comp=sequence_comp or "",
            )
            self.report(r["id"], severity, atoms, res["comp_item"], res["row"], message)

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
    rules: dict[str, list] | None = None,
    lenient: bool = False,
) -> list[Finding]:
    """
    Run all cross-checks on the first data block of an mmCIF file.

    Without a context (or detect=True), rules gated on experiment mode, entry
    subtype or requested code are skipped. dictionary_path enables the
    dictionary-enumeration pairing rules. lenient=True lets gemmi accept
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
        names = [f"_{r['category']}.{r['source_item']}" for r in rules["dictionary_enum"]]
        dict_enum = load_dictionary_enum_details(dictionary_path, names)
    return CrossChecker(block, rules, ctx, dict_enum).run()


def iter_errors(findings: Iterable[Finding]) -> Iterable[Finding]:
    return (f for f in findings if f.severity == "error")
