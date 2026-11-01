"""
Load the cross-check rule store (onedep_lib/schemas/cif/cross_checks.cif) with gemmi.

Each `_validator_rule_*` category becomes a list of row dicts keyed by the
short item name. Unset values ('.' or '?') become None. Child categories are
attached to their parent rule, ordered by their `ordinal` column, so the check
runners receive one self-contained dict per rule.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from gemmi import cif

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "schemas" / "cif" / "cross_checks.cif"


class RulesError(Exception):
    """The rule store is missing a category or is internally inconsistent."""


def _value(raw: str) -> str | None:
    return None if cif.is_null(raw) else cif.as_string(raw)


def _table(block: cif.Block, category: str) -> list[dict]:
    table = block.find_mmcif_category(f"_{category}.")
    if not table.tags:
        raise RulesError(f"rule store has no _{category} category")
    names = [tag[len(category) + 2 :] for tag in table.tags]
    return [{name: _value(row[i]) for i, name in enumerate(names)} for row in table]


def _children(rows: list[dict], *keys: str) -> dict[tuple, list[dict]]:
    """Group child rows by key columns; each group is sorted by `ordinal`."""
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row[k] for k in keys)].append(row)
    for group in grouped.values():
        group.sort(key=lambda r: int(r["ordinal"]))
    return grouped


def _bool(value: str | None) -> bool:
    return value == "yes"


def load_rules(path: Path | None = None) -> dict[str, list]:
    """Return {family: [rule, ...]} for every rule family in the store."""
    doc = cif.read(str(path or DEFAULT_RULES_PATH))
    block = doc.sole_block()
    t = lambda name: _table(block, f"validator_rule_{name}")  # noqa: E731

    conditions = _children(t("condition"), "owner", "rule_id", "branch_ordinal")

    def condition_map(owner: str, rule_id: str, branch: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for row in conditions.get((owner, rule_id, branch), []):
            result.setdefault(row["item"], []).append(row["value"])
        return result

    rules: dict[str, list] = {}

    rules["pairwise"] = t("pairwise")
    rules["date_order"] = t("date_order")

    keys = _children(t("uniqueness_key"), "rule_id")
    rules["uniqueness"] = [{**r, "key_items": [k["item"] for k in keys[(r["id"],)]]} for r in t("uniqueness")]

    rules["linked"] = [
        {
            **r,
            "cross2": r["cross2"] or r["cross"],
            "warning": _bool(r["warning"]),
            "absolute": _bool(r["absolute"]),
            "break_flag": _bool(r["break_flag"]),
        }
        for r in t("linked")
    ]

    req_items = _children(t("conditional_required_item"), "rule_id")
    skips = _children(t("skip_category"), "rule_id")
    rules["conditional_required"] = []
    for r in t("conditional_required"):
        conds = condition_map("conditional_required", r["id"], "1")
        if not conds:
            raise RulesError(f"conditional required rule {r['id']} has no conditions")
        rules["conditional_required"].append(
            {
                **r,
                "error": _bool(r["error"]),
                "conditions": conds,
                "items": [c["item"] for c in req_items[(r["id"],)]],
                "skip_if_present": [c["category"] for c in skips.get((r["id"],), [])],
            }
        )

    rules["conditional_regex"] = [{**r, "error": _bool(r["error"])} for r in t("conditional_regex")]

    enum_values = _children(t("conditional_enum_value"), "rule_id", "branch_ordinal")
    rules["conditional_enum"] = []
    for r in t("conditional_enum"):
        branches = []
        for (rule_id, _branch), rows in sorted(enum_values.items(), key=lambda kv: int(kv[0][1])):
            if rule_id == r["id"]:
                branches.append(
                    {
                        "driver_value": rows[0]["driver_value"],
                        "error_text": rows[0]["error_text"],
                        "allowed": [row["allowed_value"] for row in rows],
                    }
                )
        rules["conditional_enum"].append({**r, "branches": branches})

    rules["conditional_category_item"] = [{**r, "error": _bool(r["error"])} for r in t("conditional_category_item")]

    rules["dictionary_enum"] = t("dictionary_enum")

    modes = _children(t("cross_reference_expt"), "rule_id")
    rules["cross_reference"] = [
        {**r, "modes": [m["mode"] for m in modes.get((r["id"],), [])]} for r in t("cross_reference")
    ]

    mm_items = _children(t("make_mandatory_item"), "rule_id", "role")
    rules["make_mandatory"] = [
        {
            **r,
            "to_check": [c["item"] for c in mm_items.get((r["id"], "to_check"), [])],
            "exclude": [c["item"] for c in mm_items.get((r["id"], "exclude"), [])],
        }
        for r in t("make_mandatory")
    ]

    one_of_items = _children(t("one_of_item"), "rule_id")
    rules["one_of"] = [{**r, "items": [c["item"] for c in one_of_items[(r["id"],)]]} for r in t("one_of")]

    branches = _children(t("procedural_branch"), "rule_id")
    formats = _children(t("procedural_format"), "rule_id", "branch_ordinal")
    allowed = _children(t("procedural_allowed_value"), "rule_id", "branch_ordinal")
    empty_lists = _children(t("procedural_empty_list"), "rule_id")
    limits = {(r["rule_id"], r["role"]): r for r in t("procedural_limit")}
    rules["procedural"] = []
    for r in t("procedural"):
        rid = r["id"]
        built = []
        for b in branches.get((rid,), []):
            ordinal = b["ordinal"]
            built.append(
                {
                    **b,
                    "formats": [f["format"] for f in formats.get((rid, ordinal), [])],
                    "allowed": [a["value"] for a in allowed.get((rid, ordinal), [])],
                    # Procedural conditions are scalar: one expected value per item.
                    "conditions": {k: v[0] for k, v in condition_map("procedural", rid, ordinal).items()},
                }
            )
        rules["procedural"].append(
            {
                **r,
                "branches": built,
                "empty_list": empty_lists.get((rid,), []),
                "over_one": limits.get((rid, "over_one")),
                "below": limits.get((rid, "below")),
            }
        )

    return rules


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
