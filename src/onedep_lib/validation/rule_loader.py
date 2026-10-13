"""Strict CIF resource compilation; no input documents or executable resource code."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any

import gemmi

from onedep_lib.checks.report import CheckSeverity
from onedep_lib.validation.cif_access import tag
from onedep_lib.validation.models import (
    MODES,
    SUBTYPES,
    Branch,
    ComparisonPlan,
    Condition,
    EnumerationPlan,
    Format,
    PreparedRules,
    PresencePlan,
    ProceduralPlan,
    RuleDescriptor,
    ValidationConfigurationError,
)

# Certified v1 columns, plus the additive v2 metadata. Optional values remain schema-specific.
SCHEMAS = {
    "pairwise": "id source_category source_item target_category target_item message operator severity extra",
    "date_order": "id category left_item right_item op severity message",
    "uniqueness": "id category severity message",
    "uniqueness_key": "rule_id ordinal item",
    "linked": (
        "id source_category source_item target_category target_item cross cross2 "
        "operator fallback break_flag warning absolute text"
    ),
    "conditional_required": "id category error error_text warning_text",
    "conditional_required_item": "rule_id ordinal item",
    "condition": "owner rule_id branch_ordinal item ordinal value value_form",
    "skip_category": "rule_id ordinal category",
    "conditional_regex": "id category item regex error error_text condition_item condition_value",
    "conditional_enum": "id category driver_item affected_item",
    "conditional_enum_value": "rule_id branch_ordinal driver_value error_text ordinal allowed_value",
    "conditional_category_item": (
        "id category item regex error error_text condition_category condition_item condition_value"
    ),
    "dictionary_enum": "id category source_item cross_item",
    "cross_reference": "id category item cross_category cross_item expt code subtype type",
    "cross_reference_expt": "rule_id ordinal mode",
    "make_mandatory": "id scope subtype category",
    "make_mandatory_item": "rule_id role ordinal item",
    "one_of": "id category",
    "one_of_item": "rule_id ordinal item",
    "procedural": (
        "id kind category item driver_item skip_if_empty severity message "
        "mismatch_message missing_message source_category source_item source_key "
        "target_category target_item target_key"
    ),
    "procedural_branch": "rule_id ordinal driver_value protocol requires predicate substring severity message",
    "procedural_format": "rule_id branch_ordinal ordinal format",
    "procedural_allowed_value": "rule_id branch_ordinal ordinal value",
    "procedural_empty_list": "rule_id ordinal protocol severity message",
    "procedural_limit": "rule_id role limit severity message",
    "message": "rule_id role severity template",
    "format": "id pattern normalization match_mode",
    "bundle": "schema_version id version source_digest",
}
OPTIONAL = {
    "extra",
    "cross2",
    "fallback",
    "break_flag",
    "warning",
    "absolute",
    "warning_text",
    "code",
    "subtype",
    "skip_if_empty",
    "source_digest",
}
ROOTS = frozenset(
    (
        "pairwise",
        "date_order",
        "uniqueness",
        "linked",
        "conditional_required",
        "conditional_regex",
        "conditional_enum",
        "conditional_category_item",
        "dictionary_enum",
        "cross_reference",
        "make_mandatory",
        "one_of",
        "procedural",
    )
)
PARENTS = {
    "uniqueness_key": {"uniqueness"},
    "conditional_required_item": {"conditional_required"},
    "condition": {"conditional_required", "procedural"},
    "skip_category": {"conditional_required"},
    "conditional_enum_value": {"conditional_enum"},
    "cross_reference_expt": {"cross_reference"},
    "make_mandatory_item": {"make_mandatory"},
    "one_of_item": {"one_of"},
    "procedural_branch": {"procedural"},
    "procedural_format": {"procedural"},
    "procedural_allowed_value": {"procedural"},
    "procedural_empty_list": {"procedural"},
    "procedural_limit": {"procedural"},
    "message": ROOTS,
}
KINDS = frozenset(
    (
        "wavelength_protocol_consistency",
        "accession_format_rule",
        "conditional_accession_format_rule",
        "conditional_value_rule",
        "atom_site_occupancy",
        "sequence_model_mismatch",
        "sequence_predicate_warning",
    )
)
TOKENS = {
    "pairwise": {"item2CrossValue"},
    "linked": {"item2CrossValue"},
    "date_order": {"left_value", "right_value"},
    "uniqueness": {"duplicate_display", "key_columns", "category"},
    "cross_reference": {"source_value", "target_item"},
    "dictionary_enum": {"source_value", "cross_item", "cross_value", "allowed_values"},
    "procedural": {"chain", "residue", "comp", "atom", "model", "total", "occupancy", "sequence_comp"},
}
OPERATORS = frozenset(("<", "<=", ">", ">=", "==", "!="))


@dataclass(frozen=True)
class CompiledBundle:
    prepared: PreparedRules
    plans: tuple[Any, ...]
    formats: Any


def severity(value: str | None, default: str = "error") -> CheckSeverity:
    value = value or default
    if value not in ("hard", "error", "soft", "warning"):
        raise ValidationConfigurationError(f"Unknown severity {value!r}")
    return CheckSeverity.ERROR if value in ("hard", "error") else CheckSeverity.WARNING


def flag(value: str | None) -> bool:
    if value not in (None, "yes", "no"):
        raise ValidationConfigurationError(f"Invalid boolean {value!r}")
    return value == "yes"


def required(row: dict, name: str) -> str:
    value = row.get(name)
    if (
        value is None
        or name
        in (
            "id",
            "rule_id",
            "message",
            "text",
            "error_text",
            "warning_text",
            "template",
            "mismatch_message",
            "missing_message",
        )
        and not value.strip()
    ):
        raise ValidationConfigurationError(f"Missing required {name} in rule {row.get('id', row.get('rule_id'))}")
    return value


def pattern(value: str):
    try:
        return re.compile(value)
    except re.error as exc:
        raise ValidationConfigurationError(f"Invalid rule regex: {exc}") from exc


def template(value: str, family: str) -> str:
    tokens = re.findall(r"\[\{([^}]+)\}\]", value)
    if set(tokens) - TOKENS.get(family, set()):
        raise ValidationConfigurationError(f"Unknown {family} message placeholders: {tokens}")
    return value


def rows(block: gemmi.cif.Block) -> dict[str, list[dict]]:
    tables = {}
    for category in block.get_mmcif_category_names():
        if not category.startswith("_validator_rule_"):
            raise ValidationConfigurationError(f"Unknown rule category {category}")
        family = category[len("_validator_rule_") : -1]
        if family not in SCHEMAS:
            raise ValidationConfigurationError(f"Unknown rule category {category}")
        table = block.find_mmcif_category(category)
        columns = [t.split(".")[1] for t in table.tags]
        if set(columns) - set(SCHEMAS[family].split()):
            raise ValidationConfigurationError(f"Unknown columns in {category}")
        mandatory = set(SCHEMAS[family].split()) - OPTIONAL
        if family == "procedural":
            mandatory = {"id", "kind"}  # operands are validated against each algorithm below
        elif family in ("procedural_branch", "procedural_limit", "bundle"):
            mandatory = (
                {"rule_id", "ordinal", "message", "severity"}
                if family == "procedural_branch"
                else (
                    {"rule_id", "role", "limit", "severity", "message"}
                    if family == "procedural_limit"
                    else {"schema_version", "id", "version"}
                )
            )
        if mandatory - set(columns):
            raise ValidationConfigurationError(f"Missing columns in {category}: {sorted(mandatory - set(columns))}")
        tables[family] = [
            {k: (None if gemmi.cif.is_null(raw) else gemmi.cif.as_string(raw)) for k, raw in zip(columns, row)}
            for row in table
        ]
    return tables


def compile_rules(path: Path | None = None) -> CompiledBundle:
    resource = resources.files("onedep_lib.validation").joinpath("data/cross_checks.cif") if path is None else path
    try:
        data = resource.read_bytes()
        # Empty declared families are valid resource scopes; input files use stricter parsing.
        document = gemmi.cif.read_string(data.decode("utf-8"), check_level=1)
        block = document.sole_block()
        if not block.name or any(item.frame is not None for item in block):
            raise ValidationConfigurationError("Rules require one named flat data block without save frames")
        tables = rows(block)
    except (OSError, RuntimeError, ValueError, UnicodeError) as exc:
        if isinstance(exc, ValidationConfigurationError):
            raise
        raise ValidationConfigurationError(f"Cannot load rules {resource}: {exc}") from exc
    if "bundle" in tables and len(tables["bundle"]) != 1:
        raise ValidationConfigurationError("Rule bundle metadata must have exactly one row")
    meta = tables.get("bundle", [{}])[0]
    version = meta.get("schema_version", "1")
    if version == "2":
        required(meta, "id")
        required(meta, "version")
    if version not in ("1", "2"):
        raise ValidationConfigurationError(f"Unsupported rule schema {version}")
    default_data = b""
    defaults = {}
    if version == "1":
        default_data = resources.files("onedep_lib.validation").joinpath("data/v1_defaults.cif").read_bytes()
        db = gemmi.cif.read_string(default_data.decode()).sole_block()
        for row in db.find_mmcif_category("_validator_rule_default_message."):
            defaults[(gemmi.cif.as_string(row[0]), gemmi.cif.as_string(row[1]))] = (
                gemmi.cif.as_string(row[3]),
                severity(gemmi.cif.as_string(row[2])),
            )
        formats_table = db.find_mmcif_category("_validator_rule_format.")
        tables["format"] = [
            {k.split(".")[1]: gemmi.cif.as_string(v) for k, v in zip(formats_table.tags, row)} for row in formats_table
        ]
    formats = {}
    for row in tables.get("format", []):
        name = required(row, "id")
        if name in formats:
            raise ValidationConfigurationError(f"Duplicate format {name}")
        norm, mode = required(row, "normalization"), required(row, "match_mode")
        if norm not in ("none", "lower", "upper") or mode not in ("match", "fullmatch"):
            raise ValidationConfigurationError(f"Invalid format policy {name}")
        formats[name] = Format(pattern(required(row, "pattern")), norm, mode)
    root_ids = {}
    for family, entries in tables.items():
        if family in ROOTS:
            for row in entries:
                name = required(row, "id")
                for column in (
                    "category",
                    "source_category",
                    "target_category",
                    "cross_category",
                    "condition_category",
                    "item",
                    "source_item",
                    "target_item",
                    "cross_item",
                    "left_item",
                    "right_item",
                    "condition_item",
                    "driver_item",
                    "affected_item",
                    "source_key",
                    "target_key",
                    "cross",
                    "cross2",
                ):
                    value = row.get(column)
                    if value is not None and re.fullmatch(r"[A-Za-z0-9_]+", value) is None:
                        raise ValidationConfigurationError(f"Invalid mmCIF operand {column} in {name}")
                if name in root_ids:
                    raise ValidationConfigurationError(f"Duplicate rule ID {name}")
                root_ids[name] = family
    children = defaultdict(list)
    ordinal_groups = defaultdict(list)
    for family, entries in tables.items():
        if family not in PARENTS:
            continue
        for row in entries:
            name = required(row, "rule_id")
            if root_ids.get(name) not in PARENTS[family]:
                raise ValidationConfigurationError(f"Orphan or wrong-family {family} row for {name}")
            for column in ("item", "category"):
                value = row.get(column)
                if value is not None and re.fullmatch(r"[A-Za-z0-9_]+", value) is None:
                    raise ValidationConfigurationError(f"Invalid child operand {column} for {name}")
            if family == "condition" and row.get("owner") != root_ids[name]:
                raise ValidationConfigurationError(f"Wrong condition owner for {name}")
            for ordinal in ("ordinal", "branch_ordinal"):
                if ordinal in row:
                    try:
                        value = int(required(row, ordinal))
                    except ValueError as exc:
                        raise ValidationConfigurationError("Invalid ordinal") from exc
                    if str(value) != row[ordinal] or value < 1:
                        raise ValidationConfigurationError("Ordinals must be positive integers")
            group = (
                family,
                name,
                row.get("branch_ordinal"),
                row.get("role"),
                row.get("item") if family == "condition" else None,
            )
            if "ordinal" in row:
                ordinal_groups[group].append(int(row["ordinal"]))
            children[(family, name)].append(row)
    for values in ordinal_groups.values():
        if sorted(values) != list(range(1, len(values) + 1)):
            raise ValidationConfigurationError("Duplicate or noncontiguous ordinals")
    for values in children.values():
        values.sort(key=lambda r: (int(r.get("branch_ordinal", "1")), int(r.get("ordinal", "1"))))
    for name, family in root_ids.items():
        branches = {int(r["ordinal"]) for r in children[("procedural_branch", name)]}
        enum_branches = {int(r["branch_ordinal"]) for r in children[("conditional_enum_value", name)]}
        selected = branches or enum_branches
        if selected and sorted(selected) != list(range(1, len(selected) + 1)):
            raise ValidationConfigurationError(f"Noncontiguous branches for {name}")
        for child in ("procedural_format", "procedural_allowed_value", "condition"):
            for row in children[(child, name)]:
                branch = int(row.get("branch_ordinal", "1"))
                if (family == "conditional_required" and branch != 1) or (
                    family == "procedural" and branch not in branches
                ):
                    raise ValidationConfigurationError(f"Orphan branch for {name}")
    message_map = {}
    for row in tables.get("message", []):
        key = (row["rule_id"], row["role"])
        if key in message_map or row["role"] not in ("default", "unmapped", "mismatch"):
            raise ValidationConfigurationError("Duplicate or unsupported message role")
        if root_ids[row["rule_id"]] not in ("cross_reference", "make_mandatory", "one_of", "dictionary_enum"):
            raise ValidationConfigurationError("Inline messages cannot be overridden")
        message_map[key] = (
            template(required(row, "template"), root_ids[row["rule_id"]]),
            severity(required(row, "severity")),
        )

    def message(name: str, family: str, role: str = "default"):
        result = message_map.get((name, role), defaults.get((family, role)))
        if result is None:
            raise ValidationConfigurationError(f"Missing {role} message for {name}")
        return result

    def conditions(name: str, ordinal: int = 1):
        groups = {}
        for row in children[("condition", name)]:
            if int(row["branch_ordinal"]) == ordinal:
                item = required(row, "item")
                form = required(row, "value_form")
                if form not in ("scalar", "list"):
                    raise ValidationConfigurationError("Unsupported condition value_form")
                previous = groups.setdefault(item, (form, []))
                if previous[0] != form:
                    raise ValidationConfigurationError("Conflicting condition forms")
                previous[1].append(required(row, "value"))
        if any(form == "scalar" and len(values) != 1 for form, values in groups.values()):
            raise ValidationConfigurationError("Scalar conditions need exactly one value")
        return tuple(Condition(item, tuple(values)) for item, (_, values) in groups.items())

    def items(child: str, name: str, role=None):
        values = tuple(required(r, "item") for r in children[(child, name)] if role is None or r.get("role") == role)
        if len(set(values)) != len(values):
            raise ValidationConfigurationError("Duplicate operand item")
        return values

    plans = []
    for family, entries in tables.items():
        if family not in ROOTS:
            continue
        for source_row, row in enumerate(entries):
            name = row["id"]
            projections = []
            selectors = ()
            for ck, ik in (
                ("category", "item"),
                ("category", "source_item"),
                ("category", "cross_item"),
                ("category", "driver_item"),
                ("category", "affected_item"),
                ("category", "left_item"),
                ("category", "right_item"),
                ("source_category", "source_item"),
                ("target_category", "target_item"),
            ):
                if row.get(ck) and row.get(ik):
                    projections.append(tag(row[ck], row[ik]))
            desc = RuleDescriptor(name, family, f"_validator_rule_{family}.", source_row, tuple(projections), selectors)
            if family in ("pairwise", "linked", "date_order"):
                if family == "date_order":
                    source = tag(required(row, "category"), required(row, "left_item"))
                    target = tag(row["category"], required(row, "right_item"))
                    op = required(row, "op")
                    if op not in ("<", "<=", ">", ">="):
                        raise ValidationConfigurationError("Invalid date relation")
                else:
                    source = tag(required(row, "source_category"), required(row, "source_item"))
                    target = tag(required(row, "target_category"), required(row, "target_item"))
                    op = required(row, "operator")
                    if op not in OPERATORS and not (family == "linked" and op == "exists"):
                        raise ValidationConfigurationError("Invalid comparison operator")
                if row.get("extra") is not None or row.get("fallback") not in (None, "single_row_if_key_missing"):
                    raise ValidationConfigurationError("Unsupported comparison configuration")
                msg = template(required(row, "text" if family == "linked" else "message"), family)
                sev = (
                    CheckSeverity.WARNING
                    if family == "linked" and flag(row.get("warning"))
                    else CheckSeverity.ERROR
                    if family == "linked"
                    else severity(required(row, "severity"))
                )
                cross = row.get("cross") if family == "linked" else None
                if family == "linked" and cross is None:
                    raise ValidationConfigurationError("Linked comparison needs a source key")
                plans.append(
                    ComparisonPlan(
                        desc,
                        source,
                        target,
                        op,
                        msg,
                        sev,
                        tag(row["source_category"], cross) if cross else None,
                        tag(row["target_category"], row.get("cross2") or cross) if cross else None,
                        row.get("fallback"),
                        flag(row.get("absolute")),
                        flag(row.get("break_flag")),
                    )
                )
            elif family in (
                "uniqueness",
                "conditional_required",
                "conditional_regex",
                "conditional_category_item",
                "make_mandatory",
                "one_of",
            ):
                category = required(row, "category")
                conds, exclude, skip, subtype, trigger, regex = (), (), (), None, None, None
                if family == "uniqueness":
                    operands = items("uniqueness_key", name)
                    msg, sev = template(required(row, "message"), family), severity(required(row, "severity"))
                elif family == "conditional_required":
                    operands = items("conditional_required_item", name)
                    conds = conditions(name)
                    if not conds:
                        raise ValidationConfigurationError("Required rule needs conditions")
                    sev = CheckSeverity.ERROR if flag(required(row, "error")) else CheckSeverity.WARNING
                    msg = template(
                        required(row, "error_text" if sev == CheckSeverity.ERROR else "warning_text"), family
                    )
                    skip = tuple(required(r, "category") for r in children[("skip_category", name)])
                elif family in ("conditional_regex", "conditional_category_item"):
                    operands = (required(row, "item"),)
                    regex = pattern(required(row, "regex"))
                    sev = CheckSeverity.ERROR if flag(required(row, "error")) else CheckSeverity.WARNING
                    msg = template(required(row, "error_text"), family)
                    if family == "conditional_regex":
                        conds = (Condition(required(row, "condition_item"), (required(row, "condition_value"),)),)
                    else:
                        trigger = (
                            tag(required(row, "condition_category"), required(row, "condition_item")),
                            required(row, "condition_value"),
                        )
                elif family == "make_mandatory":
                    if required(row, "scope") not in ("makeMandatory", "makeMandatorySubtypes"):
                        raise ValidationConfigurationError("Unknown mandatory scope")
                    operands = items("make_mandatory_item", name, "to_check")
                    exclude = items("make_mandatory_item", name, "exclude")
                    if any(
                        r.get("role") not in ("to_check", "exclude") for r in children[("make_mandatory_item", name)]
                    ):
                        raise ValidationConfigurationError("Unknown mandatory operand role")
                    subtype = row.get("subtype")
                    if row["scope"] == "makeMandatorySubtypes" and subtype not in SUBTYPES:
                        raise ValidationConfigurationError("Invalid mandatory subtype")
                    if row["scope"] == "makeMandatory" and subtype is not None:
                        raise ValidationConfigurationError("Unexpected mandatory subtype")
                    msg, sev = message(name, family)
                else:
                    operands = items("one_of_item", name)
                    msg, sev = message(name, family)
                if not operands:
                    raise ValidationConfigurationError(f"Empty operand group {name}")
                plans.append(
                    PresencePlan(desc, category, operands, msg, sev, conds, exclude, skip, subtype, trigger, regex)
                )
            elif family in ("conditional_enum", "dictionary_enum", "cross_reference"):
                category = required(row, "category")
                branches = []
                if family == "conditional_enum":
                    source, target = (
                        tag(category, required(row, "driver_item")),
                        tag(category, required(row, "affected_item")),
                    )
                    groups = defaultdict(list)
                    for r in children[("conditional_enum_value", name)]:
                        groups[int(r["branch_ordinal"])].append(r)
                    drivers = set()
                    for ordinal, rs in groups.items():
                        driver, msg = required(rs[0], "driver_value"), required(rs[0], "error_text")
                        if driver in drivers or any((r["driver_value"], r["error_text"]) != (driver, msg) for r in rs):
                            raise ValidationConfigurationError("Conflicting enum branch metadata")
                        drivers.add(driver)
                        branches.append(
                            Branch(
                                ordinal,
                                template(msg, family),
                                CheckSeverity.ERROR,
                                driver=driver,
                                values=tuple(required(r, "allowed_value") for r in rs),
                            )
                        )
                    if not branches:
                        raise ValidationConfigurationError("Missing enum branches")
                    plans.append(EnumerationPlan(desc, source, target, tuple(branches)))
                elif family == "dictionary_enum":
                    source, target = (
                        tag(category, required(row, "source_item")),
                        tag(category, required(row, "cross_item")),
                    )
                    plans.append(
                        EnumerationPlan(
                            desc,
                            source,
                            target,
                            messages=MappingProxyType(
                                {role: message(name, family, role) for role in ("unmapped", "mismatch")}
                            ),
                        )
                    )
                else:
                    if row.get("type") != "cross" or row.get("subtype") != "full":
                        raise ValidationConfigurationError("Unsupported cross-reference type")
                    mode = row.get("expt")
                    modes = tuple(required(r, "mode") for r in children[("cross_reference_expt", name)])
                    if mode not in (None, "all", "coded") or any(m not in MODES for m in modes):
                        raise ValidationConfigurationError("Invalid experiment selector")
                    if mode is None and not modes or mode == "coded" and row.get("code") not in ("PDB", "EMDB", "BMRB"):
                        raise ValidationConfigurationError("Missing cross-reference selector")
                    source, target = (
                        tag(category, required(row, "item")),
                        tag(required(row, "cross_category"), required(row, "cross_item")),
                    )
                    plans.append(
                        EnumerationPlan(
                            desc,
                            source,
                            target,
                            mode=mode,
                            code=row.get("code"),
                            modes=modes,
                            messages=MappingProxyType({"default": message(name, family)}),
                        )
                    )
            else:
                kind = required(row, "kind")
                child_kinds = {
                    "procedural_limit": {"atom_site_occupancy"},
                    "procedural_empty_list": {"wavelength_protocol_consistency"},
                    "procedural_format": {"accession_format_rule", "conditional_accession_format_rule"},
                    "procedural_allowed_value": {"conditional_value_rule"},
                    "condition": {"conditional_accession_format_rule", "conditional_value_rule"},
                    "procedural_branch": KINDS - {"atom_site_occupancy", "sequence_model_mismatch"},
                }
                for child, allowed_kinds in child_kinds.items():
                    if children[(child, name)] and kind not in allowed_kinds:
                        raise ValidationConfigurationError(f"Unexpected {child} rows for {kind}")
                if kind not in KINDS:
                    raise ValidationConfigurationError(f"Unsupported procedural kind {kind}")
                flag(row.get("skip_if_empty"))
                branches = []
                for r in children[("procedural_branch", name)]:
                    ordinal = int(r["ordinal"])
                    fmts = tuple(
                        required(f, "format")
                        for f in children[("procedural_format", name)]
                        if int(f["branch_ordinal"]) == ordinal
                    )
                    if any(f not in formats for f in fmts):
                        raise ValidationConfigurationError("Unknown procedural format")
                    vals = tuple(
                        required(v, "value")
                        for v in children[("procedural_allowed_value", name)]
                        if int(v["branch_ordinal"]) == ordinal
                    )
                    conds = conditions(name, ordinal)
                    if kind in ("accession_format_rule", "conditional_accession_format_rule") and not fmts:
                        raise ValidationConfigurationError("Accession branch needs formats")
                    if kind.startswith("conditional_") and not conds:
                        raise ValidationConfigurationError("Conditional branch needs conditions")
                    if kind == "conditional_value_rule" and not vals:
                        raise ValidationConfigurationError("Value branch needs allowed values")
                    if kind == "sequence_predicate_warning" and (
                        r.get("predicate") not in ("homopolymer_ala", "substring")
                        or r.get("predicate") == "substring"
                        and r.get("substring") is None
                    ):
                        raise ValidationConfigurationError("Invalid sequence predicate")
                    if kind == "wavelength_protocol_consistency" and (
                        r.get("requires") not in ("single", "multi") or r.get("protocol") is None
                    ):
                        raise ValidationConfigurationError("Invalid wavelength branch")
                    branches.append(
                        Branch(
                            ordinal,
                            template(required(r, "message"), "procedural"),
                            severity(required(r, "severity")),
                            r.get("driver_value"),
                            r.get("protocol"),
                            r.get("requires"),
                            r.get("predicate"),
                            r.get("substring"),
                            vals,
                            fmts,
                            conds,
                        )
                    )
                limits = {}
                for r in children[("procedural_limit", name)]:
                    role = required(r, "role")
                    if role not in ("over_one", "below") or role in limits:
                        raise ValidationConfigurationError("Invalid occupancy limit role")
                    try:
                        value = float(required(r, "limit"))
                        if not __import__("math").isfinite(value):
                            raise ValueError()
                    except ValueError as exc:
                        raise ValidationConfigurationError("Invalid limit") from exc
                    limits[role] = (
                        value,
                        template(required(r, "message"), "procedural"),
                        severity(required(r, "severity")),
                    )
                empty = tuple(
                    Branch(
                        int(r["ordinal"]),
                        template(required(r, "message"), "procedural"),
                        severity(required(r, "severity")),
                        protocol=required(r, "protocol"),
                    )
                    for r in children[("procedural_empty_list", name)]
                )
                if kind == "atom_site_occupancy" and set(limits) != {"over_one", "below"}:
                    raise ValidationConfigurationError("Occupancy needs both limits")
                if kind not in ("atom_site_occupancy", "sequence_model_mismatch") and not branches:
                    raise ValidationConfigurationError("Missing procedural branches")
                source = (
                    tag(row["source_category"], required(row, "source_item")) if row.get("source_category") else None
                )
                target = (
                    tag(row["target_category"], required(row, "target_item")) if row.get("target_category") else None
                )
                if kind == "wavelength_protocol_consistency" and (
                    not source or not target or not row.get("source_key") or not row.get("target_key")
                ):
                    raise ValidationConfigurationError("Missing wavelength operands")
                if kind not in ("wavelength_protocol_consistency", "sequence_model_mismatch") and (
                    not row.get("category") or not row.get("item")
                ):
                    raise ValidationConfigurationError("Missing procedural operands")
                if kind == "accession_format_rule" and not row.get("driver_item"):
                    raise ValidationConfigurationError("Missing accession driver")
                if kind == "sequence_model_mismatch" and (
                    not row.get("mismatch_message") or not row.get("missing_message")
                ):
                    raise ValidationConfigurationError("Missing sequence messages")
                plans.append(
                    ProceduralPlan(
                        desc,
                        kind,
                        row.get("category"),
                        row.get("item"),
                        row.get("driver_item"),
                        tuple(branches),
                        MappingProxyType(limits),
                        empty,
                        source,
                        target,
                        tag(row["source_category"], row["source_key"]) if row.get("source_key") else None,
                        tag(row["target_category"], row["target_key"]) if row.get("target_key") else None,
                        severity(
                            required(row, "severity") if kind == "sequence_model_mismatch" else row.get("severity")
                        ),
                        template(row.get("mismatch_message") or "", "procedural"),
                        template(row.get("missing_message") or "", "procedural"),
                    )
                )
    projected = []
    for plan in plans:
        tags = list(plan.descriptor.projected_tags)
        selectors = []
        for attribute in ("source", "target", "source_key", "target_key"):
            value = getattr(plan, attribute, None)
            if value:
                tags.append(value)
        category = getattr(plan, "category", None)
        if category:
            for item in getattr(plan, "items", ()) + getattr(plan, "excluded", ()):
                tags.append(tag(category, item))
            driver = getattr(plan, "driver", None)
            if driver:
                tags.append(tag(category, driver))
            for condition in getattr(plan, "conditions", ()):
                tags.append(tag(category, condition.item))
            for branch in getattr(plan, "branches", ()):
                for condition in branch.conditions:
                    tags.append(tag(category, condition.item))
        trigger = getattr(plan, "trigger", None)
        if trigger:
            tags.append(trigger[0])
        if isinstance(plan, EnumerationPlan) and plan.descriptor.family == "cross_reference":
            if plan.mode == "coded":
                selectors.append("requested_codes")
            elif plan.mode is None:
                selectors.append("experiment_modes")
        if isinstance(plan, PresencePlan) and plan.subtype:
            selectors.append("entry_subtypes")
        source = getattr(plan, "source", None)
        check_category = category or (source.split(".")[0].lstrip("_") if source else None)
        if isinstance(plan, ProceduralPlan) and plan.kind == "sequence_model_mismatch":
            check_category = "atom_site"
        descriptor = replace(
            plan.descriptor,
            projected_tags=tuple(dict.fromkeys(tags)),
            selector_dependencies=tuple(selectors),
            check_category=check_category.lower().lstrip("_") if check_category else None,
        )
        projected.append(replace(plan, descriptor=descriptor))
    plans = projected
    prepared = PreparedRules(
        meta.get("id", "legacy_cross_checks"),
        version,
        meta.get("version", "1"),
        hashlib.sha256(data).hexdigest(),
        tuple(p.descriptor for p in plans),
        version == "1",
        hashlib.sha256(default_data).hexdigest() if default_data else None,
    )
    return CompiledBundle(prepared, tuple(plans), MappingProxyType(formats))
