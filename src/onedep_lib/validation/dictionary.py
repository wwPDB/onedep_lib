"""Gemmi DDL ownership, versioned diagnostic adaptation and narrow supplements."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from importlib.metadata import version
from pathlib import Path
from types import MappingProxyType

import gemmi

from onedep_lib.checks.report import CheckIssue, CheckSeverity, CifLocation
from onedep_lib.validation.models import ValidationConfigurationError
from onedep_lib.validation.values import date, key, number, text


@dataclass(frozen=True)
class Definition:
    name: str
    category: str
    type_code: str
    numeric: bool
    mandatory: bool
    advisory_ranges: tuple[tuple[str, str], ...]
    enumeration: tuple[str, ...] = ()


@dataclass(frozen=True)
class Link:
    group: str
    child: tuple[str, ...]
    parent: tuple[str, ...]


def decoded_rows(block, category):
    table = block.find_mmcif_category(category)
    names = tuple(t.split(".")[1] for t in table.tags)
    return [
        {k: (None if gemmi.cif.is_null(v) else gemmi.cif.as_string(v)) for k, v in zip(names, row)} for row in table
    ]


def scalar(block, name, default=None):
    raw = block.find_value(name)
    return gemmi.cif.as_string(raw) if raw is not None and not gemmi.cif.is_null(raw) else default


class Dictionary:
    def __init__(self, paths, options):
        self.options = options
        definitions = {}
        definition_signatures = {}
        category_signatures = {}
        type_signatures = {}
        cross_enums = {}
        simple_links = defaultdict(set)
        groups = defaultdict(list)
        aliases = set()
        self.identities = []
        self.events = []
        self.preparation_diagnostics = []
        self.ddl = self._make_ddl(options.dictionary_parent_checks)
        self._category_ddl = None
        resource_data = []
        for path in paths:
            path = Path(path)
            try:
                raw = path.read_bytes()
                doc = gemmi.cif.read_string(raw.decode(), check_level=2)
            except (OSError, ValueError, RuntimeError, UnicodeError) as exc:
                raise ValidationConfigurationError(f"Cannot load dictionary {path}: {exc}") from exc
            if len(doc) != 1 or not any(i.frame is not None for i in doc[0]):
                raise ValidationConfigurationError("Only the tested PDBx/DDL2 dictionary profile is supported")
            resource_data.append(raw)
            block = doc[0]
            for relation in decoded_rows(block, "_pdbx_item_linked_group_list."):
                required_link = ("child_category_id", "link_group_id", "child_name", "parent_name")
                if not all(relation.get(k) for k in required_link):
                    raise ValidationConfigurationError("Malformed composite dictionary link")
                child, parent = relation["child_name"].lower(), relation["parent_name"].lower()
                group = (relation["child_category_id"], relation["link_group_id"])
                if child.split(".")[0].lstrip("_") != relation["child_category_id"].lower():
                    raise ValidationConfigurationError("Composite link child category mismatch")
                if (child, parent) not in groups[group]:
                    groups[group].append((child, parent))
            for type_row in decoded_rows(block, "_item_type_list."):
                code = type_row.get("code")
                signature = (type_row.get("primitive_code"), type_row.get("construct"))
                if code in type_signatures and type_signatures[code] != signature:
                    raise ValidationConfigurationError(f"Conflicting dictionary type definition {code}")
                type_signatures[code] = signature
            primitives = {r["code"]: r.get("primitive_code") for r in decoded_rows(block, "_item_type_list.")}
            for item in block:
                if item.frame is None:
                    continue
                frame = item.frame
                frame_digest = hashlib.sha256(frame.as_string().encode()).hexdigest()
                category_id = scalar(frame, "_category.id")
                if category_id is not None:
                    if category_id in category_signatures and category_signatures[category_id] != frame_digest:
                        raise ValidationConfigurationError(f"Conflicting dictionary category definition {category_id}")
                    category_signatures[category_id] = frame_digest
                names = tuple(gemmi.cif.as_string(v).lower() for v in frame.find_values("_item.name"))
                primary = frame.name.lower() if frame.name.lower() in names else names[0] if names else None
                if primary is not None:
                    if primary in definition_signatures and definition_signatures[primary] != frame_digest:
                        raise ValidationConfigurationError(f"Nonidentical duplicate dictionary definition {primary}")
                    definition_signatures[primary] = frame_digest
                type_code = scalar(frame, "_item_type.code", "")
                if options.dictionary_deposition_overrides:
                    type_code = scalar(frame, "_pdbx_item_type.code", type_code)
                mandatory = scalar(frame, "_pdbx_item.mandatory_code") == "yes"
                enum_rows = decoded_rows(frame, "_item_enumeration.")
                if not enum_rows:
                    enum_rows = decoded_rows(frame, "_pdbx_item_enumeration.")
                cross_rows = enum_rows
                if options.dictionary_deposition_overrides:
                    enum_rows = decoded_rows(frame, "_pdbx_item_enumeration.") or enum_rows
                enum_values = tuple(r["value"] for r in enum_rows if r.get("value") is not None)
                mapping = {
                    r["value"]: frozenset(v.strip() for v in r["detail"].split(",") if v.strip())
                    for r in cross_rows
                    if r.get("value") is not None and r.get("detail") is not None
                }
                ranges = tuple((r.get("minimum"), r.get("maximum")) for r in decoded_rows(frame, "_pdbx_item_range."))
                for minimum, maximum in ranges:
                    try:
                        low = float(minimum) if minimum is not None else -math.inf
                        high = float(maximum) if maximum is not None else math.inf
                        if (
                            (minimum is not None and not math.isfinite(low))
                            or (maximum is not None and not math.isfinite(high))
                            or low > high
                        ):
                            raise ValueError()
                    except ValueError as exc:
                        raise ValidationConfigurationError("Invalid advisory dictionary range") from exc
                for name in names:
                    definition = Definition(
                        name,
                        name.split(".")[0].lstrip("_"),
                        type_code,
                        type_code in ("int", "float") or primitives.get(type_code) == "numb",
                        mandatory and name == primary,
                        ranges,
                        enum_values,
                    )
                    previous = definitions.get(name)
                    if previous is not None:
                        if previous.type_code and type_code and previous.type_code != type_code:
                            raise ValidationConfigurationError(f"Conflicting dictionary definition {name}")
                        # DDL2 shared frames declare child references; those are not second definitions.
                        definition = replace(
                            definition,
                            type_code=previous.type_code or type_code,
                            numeric=previous.numeric or definition.numeric,
                            mandatory=previous.mandatory or definition.mandatory,
                            advisory_ranges=previous.advisory_ranges or ranges,
                            enumeration=previous.enumeration or enum_values,
                        )
                    definitions[name] = definition
                    if mapping:
                        cross_enums[name] = MappingProxyType(mapping)
                for r in decoded_rows(frame, "_item_aliases."):
                    if not names or not r.get("alias_name"):
                        raise ValidationConfigurationError("Malformed dictionary alias declaration")
                    aliases.add(r["alias_name"].lower())
                for r in decoded_rows(frame, "_item_linked."):
                    child, parent = r.get("child_name"), r.get("parent_name")
                    if not child or not parent:
                        raise ValidationConfigurationError("Invalid dictionary parent link")
                    child, parent = child.lower(), parent.lower()
                    simple_links[child].add(parent)
                for r in decoded_rows(frame, "_pdbx_item_linked_group_list."):
                    if not all(r.get(k) for k in ("child_name", "parent_name", "link_group_id")):
                        raise ValidationConfigurationError("Malformed composite dictionary link")
                    group = (r.get("child_category_id"), r["link_group_id"], r.get("parent_category_id"))
                    groups[group].append((r["child_name"].lower(), r["parent_name"].lower()))
            self.identities.append(
                MappingProxyType(
                    {
                        "id": scalar(block, "_dictionary.title", block.name),
                        "version": scalar(block, "_dictionary.version", ""),
                        "path": str(path),
                        "digest": hashlib.sha256(raw).hexdigest(),
                    }
                )
            )
            self.events.clear()
            self.ddl.read_ddl(doc)
            fatal = [m for m in self.events if not m.startswith("Bad DDL2: different parents for ")]
            if fatal:
                raise ValidationConfigurationError("Dictionary preparation diagnostic: " + "; ".join(fatal))
            self.preparation_diagnostics.extend(self.events)
        # Historical alias declarations do not prevent validation of canonical mmCIF tags.
        self.aliases = frozenset(aliases)
        if not definitions:
            raise ValidationConfigurationError("Dictionary has no supported DDL2 item definitions")
        self.definitions = MappingProxyType(definitions)
        mandatory = defaultdict(list)
        for definition in definitions.values():
            if definition.mandatory:
                mandatory[definition.category].append(definition)
        self.mandatory_by_category = {category: tuple(ds) for category, ds in mandatory.items()}
        self.cross_enums = MappingProxyType(cross_enums)
        for group, pairs in groups.items():
            if len({c.split(".")[0] for c, _ in pairs}) != 1 or len({p.split(".")[0] for _, p in pairs}) != 1:
                raise ValidationConfigurationError(f"Ambiguous composite dictionary link {group}")
        self.ambiguous_parents = MappingProxyType(
            {child: tuple(sorted(parents)) for child, parents in simple_links.items() if len(parents) > 1}
        )
        links = [
            Link("simple", (child,), (next(iter(parents)),))
            for child, parents in simple_links.items()
            if len(parents) == 1
        ]
        links.extend(
            Link(":".join(str(x) for x in group), tuple(c for c, _ in pairs), tuple(p for _, p in pairs))
            for group, pairs in groups.items()
        )
        self.links = tuple(links)
        self.identities = tuple(self.identities)
        self.preparation_diagnostics = tuple(self.preparation_diagnostics)
        self._resource_data = tuple(resource_data)

    def _make_ddl(self, parent_checks):
        ddl = gemmi.cif.Ddl(
            logger=(self.events.append, 6),
            print_unknown_tags=True,
            use_regex=True,
            use_context=self.options.dictionary_context_checks,
            use_linked_groups=parent_checks,
            use_mandatory=True,
            use_unique_keys=True,
        )
        ddl.use_deposition_checks = self.options.dictionary_deposition_overrides
        return ddl

    def native_backend(self, category_scope=False):
        if not category_scope:
            return self.ddl
        if self._category_ddl is None:
            # Parent membership uses the full input in supplements; Gemmi checks the selected view.
            ddl = self._make_ddl(False)
            self.events.clear()
            for raw in self._resource_data:
                ddl.read_ddl(gemmi.cif.read_string(raw.decode("utf-8"), check_level=2))
            if self.events:
                raise ValidationConfigurationError(
                    "Scoped dictionary preparation diagnostic: " + "; ".join(self.events)
                )
            self._category_ddl = ddl
        return self._category_ddl

    def input_limitations(self, view, categories=None):
        """Keep unsupported input dependencies explicit while canonical checks remain available."""
        if self.aliases:
            for table_index, item in enumerate(view.block):
                names = item.loop.tags if item.loop is not None else (item.pair[0],) if item.pair is not None else ()
                for column, name in enumerate(names):
                    if categories is not None and name.lower().split(".")[0].lstrip("_") not in categories:
                        continue
                    if name.lower() in self.aliases and name.lower() not in self.definitions:
                        yield (
                            "DICTIONARY_ALIAS_UNSUPPORTED",
                            "Historical alias tags require normalization to canonical mmCIF names",
                            CifLocation(
                                view.block.name,
                                item=name,
                                line=item.line_number,
                                block_index=view.index,
                                table_index=table_index,
                                loop_column=column if item.loop is not None else None,
                            ),
                        )
        if "dictionary" in self.options.phases and self.options.dictionary_parent_checks:
            for child in self.ambiguous_parents:
                if categories is not None and child.split(".")[0].lstrip("_") not in categories:
                    continue
                table = view.table(child.split(".")[0])
                if (
                    table
                    and table.has(child)
                    and any(text(table.raw(child, row)) is not None for row in range(table.row_count))
                ):
                    yield (
                        "DICTIONARY_PARENT_UNSUPPORTED",
                        "This populated item has unresolved alternative parent definitions in the dictionary",
                        table.location(child),
                    )

    def verify_rules(self, bundle):
        incompatible = {}
        for plan in bundle.plans:
            tags = set(plan.descriptor.projected_tags)
            for attribute in ("source", "target", "source_key", "target_key"):
                value = getattr(plan, attribute, None)
                if value:
                    tags.add(value)
            category = getattr(plan, "category", None)
            if category:
                for item in getattr(plan, "items", ()):
                    tags.add(f"_{category}.{item}")
                for condition in getattr(plan, "conditions", ()):
                    tags.add(f"_{category}.{condition.item}")
                for branch in getattr(plan, "branches", ()):
                    for condition in branch.conditions:
                        tags.add(f"_{category}.{condition.item}")
            missing = sorted(t for t in tags if t.lower() not in self.definitions)
            if missing:
                if self.options.rule_dictionary_policy == "error":
                    raise ValidationConfigurationError(
                        f"Rules/dictionary incompatible for {plan.descriptor.id}: {missing}"
                    )
                incompatible[plan.descriptor.id] = tuple(missing)
        return MappingProxyType(incompatible)

    def diagnose(self, message, views):
        options = self.options
        block_match = re.search(r"\[([^]]+)\]", message)
        matches = [v for v in views if block_match and v.block.name == block_match[1]]
        view = matches[0] if len(matches) == 1 else None
        tags = re.findall(r"_[A-Za-z0-9_]+\.[A-Za-z0-9_]+", message)
        item = tags[0] if tags else None
        table = view.table(item.split(".")[0]) if view and item else None
        location = (
            table.location(item)
            if table
            else CifLocation(view.block.name if view else None, item=item, block_index=view.index if view else None)
        )
        severity = CheckSeverity.ERROR
        if "unknown tag " in message:
            code, severity = "DICTIONARY_UNKNOWN_TAG", options.unknown_tag_severity
        elif "missing mandatory tag:" in message:
            code = "DICTIONARY_MANDATORY"
        elif "duplicated key" in message or "category key" in message:
            code = "DICTIONARY_KEY"
        elif "parent" in message:
            code = "DICTIONARY_FOREIGN_KEY"
        elif any(
            token in message
            for token in (
                "expected ",
                "not a ",
                "out of range",
                "not in enumeration",
                "does not match",
                "must be ",
                "invalid ",
            )
        ):
            code = "DICTIONARY_TYPE"
        elif message.startswith("category indicated as "):
            code, severity = "DICTIONARY_CONTEXT", CheckSeverity.INFO
        else:
            code = "DICTIONARY_DIAGNOSTIC_UNCLASSIFIED"
        context = {
            "backend": "gemmi",
            "backend_version": version("gemmi"),
            "diagnostic_adapter_version": "gemmi-0.7-v1",
            "backend_diagnostic": message[: options.max_diagnostic_chars],
            "diagnostic_length": len(message),
            "diagnostic_digest": hashlib.sha256(message.encode()).hexdigest(),
            "diagnostic_truncated": len(message) > options.max_diagnostic_chars,
            "location_precision": "table" if table else "block" if view else "unknown",
        }
        return CheckIssue(
            severity, code, message[: options.max_diagnostic_chars], location, check_type="dictionary", context=context
        ), view

    def supplements(self, view, collector, date_tags, categories=None):
        for table in view.tables.values():
            if categories is not None and table.category not in categories:
                continue
            for item in table.tags:
                definition = self.definitions.get(item)
                if definition is None:
                    continue
                for row in range(table.row_count):
                    if row % 4096 == 0 and not collector.poll():
                        return
                    raw = table.raw(item, row)
                    value = text(raw)
                    code, message = None, None
                    sev = CheckSeverity.ERROR
                    if definition.mandatory and value is None:
                        code, message = "DICTIONARY_MANDATORY", "No value present for this mandatory item."
                    elif value is not None and definition.type_code == "int" and not re.fullmatch(r"[+-]?[0-9]+", raw):
                        code, message = "DICTIONARY_TYPE", "Expected an unquoted ASCII integer"
                    elif value is not None and definition.numeric:
                        try:
                            number(raw)
                        except ValueError:
                            code, message = "DICTIONARY_TYPE", "Expected a finite dictionary numeric value"
                    if value is not None and item in date_tags:
                        try:
                            date(raw)
                        except ValueError:
                            code, message = "DICTIONARY_TYPE", "Invalid calendar date or time"
                    if value is not None and definition.advisory_ranges and code is None:
                        try:
                            parsed = number(raw)
                            assert parsed is not None
                            allowed = False
                            for minimum, maximum in definition.advisory_ranges:
                                lo = float(minimum) if minimum not in (None, ".", "?") else -math.inf
                                hi = float(maximum) if maximum not in (None, ".", "?") else math.inf
                                allowed |= lo <= parsed <= hi
                            if not allowed:
                                code, message, sev = (
                                    "DICTIONARY_ADVISORY_RANGE",
                                    "Value outside dictionary advisory ranges",
                                    CheckSeverity.WARNING,
                                )
                        except ValueError:
                            pass  # backend/type supplements diagnose invalid numeric input
                    if code and message:
                        collector.add(
                            CheckIssue(
                                sev,
                                code,
                                message,
                                table.location(item, row),
                                actual=raw,
                                check_type="dictionary",
                                context={"definition_tag": item, "location_precision": "table", "supplement": True},
                            )
                        )
                        if collector.stopped:
                            return
            for definition in self.mandatory_by_category.get(table.category, ()):
                if not table.has(definition.name):
                    for row in range(table.row_count):
                        collector.add(
                            CheckIssue(
                                CheckSeverity.ERROR,
                                "DICTIONARY_MANDATORY",
                                "No tag present for this mandatory item.",
                                table.location(definition.name, row),
                                check_type="dictionary",
                                context={"definition_tag": definition.name, "supplement": True},
                            )
                        )
                        if collector.stopped:
                            return
        if not self.options.dictionary_parent_checks:
            return
        for link in self.links:
            if categories is not None and link.child[0].split(".")[0].lstrip("_") not in categories:
                continue
            child = view.table(link.child[0].split(".")[0])
            if child is None or any(not child.has(t) for t in link.child):
                continue
            parent = view.table(link.parent[0].split(".")[0])
            signature = ("dictionary_link", link.parent)
            if signature not in view.indexes:
                membership = set()
                if parent:
                    for row in range(parent.row_count):
                        if row % 4096 == 0 and not collector.poll():
                            return
                        parts = tuple(key(parent.raw(t, row)) for t in link.parent)
                        if all(p is not None for p in parts):
                            membership.add(parts)
                view.indexes[signature] = membership
                view.index_builds += 1
            membership = view.indexes[signature]
            for row in range(child.row_count):
                if row % 4096 == 0 and not collector.poll():
                    return
                parts = tuple(key(child.raw(t, row)) for t in link.child)
                if all(p is not None for p in parts) and parts not in membership:
                    collector.add(
                        CheckIssue(
                            CheckSeverity.ERROR,
                            "DICTIONARY_FOREIGN_KEY",
                            "Populated child key has no corresponding parent key",
                            child.location(link.child[0], row),
                            actual=parts,
                            check_type="dictionary",
                            context={
                                "definition_tag": link.child[0],
                                "link_group": link.group,
                                "child_tags": link.child,
                                "parent_tags": link.parent,
                                "supplement": True,
                            },
                        )
                    )
                    if collector.stopped:
                        return
