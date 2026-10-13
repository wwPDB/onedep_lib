"""Offline resource preparation and dictionary-first validation orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import cast

import gemmi

from onedep_lib.checks.report import CheckIssue, CheckReport, CheckSeverity
from onedep_lib.validation import evaluators
from onedep_lib.validation.cif_access import BlockView
from onedep_lib.validation.dictionary import Dictionary
from onedep_lib.validation.models import (
    ComparisonPlan,
    EnumerationPlan,
    PreparedRules,
    PresencePlan,
    ValidationConfigurationError,
    ValidationContext,
    ValidationMetadata,
    ValidationOptions,
)
from onedep_lib.validation.rule_loader import CompiledBundle, compile_rules
from onedep_lib.validation.runtime import Collector, Run
from onedep_lib.validation.values import text

_LOG = logging.getLogger(__name__)
METHODS = {
    "X-RAY DIFFRACTION": "xray",
    "NEUTRON DIFFRACTION": "nd",
    "FIBER DIFFRACTION": "fd",
    "ELECTRON MICROSCOPY": "em",
    "ELECTRON CRYSTALLOGRAPHY": "ec",
    "SOLUTION NMR": "nmr",
    "SOLID-STATE NMR": "ssnmr",
}


def _category_selection(categories: Iterable[str] | None) -> tuple[str, ...] | None:
    if categories is None:
        return None
    if isinstance(categories, str):
        categories = (categories,)
    try:
        names = tuple(categories)
    except TypeError as exc:
        raise ValidationConfigurationError("categories must be a collection of category names") from exc
    canonical = set()
    for name in names:
        if not isinstance(name, str):
            raise ValidationConfigurationError("categories must contain category names")
        value = name.strip().removeprefix("_").removesuffix(".").lower()
        if not re.fullmatch(r"[a-z0-9_]+", value):
            raise ValidationConfigurationError("Use category names such as atom_site, not item names")
        canonical.add(value)
    return tuple(sorted(canonical)) or None


class MmCifValidator:
    """Prepare one immutable rule profile and reuse its serialized Gemmi DDL state."""

    _options: ValidationOptions
    _bundle: CompiledBundle | None
    _dictionary: Dictionary | None
    _lock: threading.Lock
    _fingerprint: str
    _incompatible_rules: Mapping[str, tuple[str, ...]]

    @classmethod
    def load(
        cls,
        dictionary_paths: Sequence[Path],
        *,
        rules_path: Path | None = None,
        options: ValidationOptions | None = None,
    ) -> MmCifValidator:
        if options is not None and not isinstance(options, ValidationOptions):
            raise ValidationConfigurationError("options must be ValidationOptions")
        options = options or ValidationOptions()
        if "dictionary" in options.phases and not dictionary_paths:
            raise ValidationConfigurationError("Dictionary paths are required for dictionary validation")
        instance = cls()
        instance._options = options
        instance._lock = threading.Lock()
        instance._bundle = compile_rules(rules_path) if "cross" in options.phases else None
        instance._dictionary = Dictionary(dictionary_paths, options) if dictionary_paths else None
        instance._incompatible_rules = (
            instance._dictionary.verify_rules(instance._bundle) if instance._dictionary and instance._bundle else {}
        )
        option_values = asdict(options)
        option_values.pop("cancelled")
        option_values["unknown_tag_severity"] = options.unknown_tag_severity.value
        inputs = {
            "options": option_values,
            "backend_version": version("gemmi"),
            "dictionary_digests": [d["digest"] for d in instance._dictionary.identities]
            if instance._dictionary
            else [],
            "rule_digest": instance.rules.digest,
            "defaults_digest": instance.rules.defaults_digest,
        }
        instance._fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        _LOG.debug(
            "Prepared mmCIF validation: dictionaries=%d rules=%d", len(dictionary_paths), instance.rules.root_rule_count
        )
        return instance

    @property
    def options(self) -> ValidationOptions:
        """Inspect the immutable options used to prepare this resource snapshot."""
        return self._options

    @property
    def rules(self) -> PreparedRules:
        """Inspect rule descriptors without exposing mutable executable plans."""
        return self._bundle.prepared if self._bundle else PreparedRules("none", "2", "0", "", ())

    def validate_path(
        self,
        path: Path,
        *,
        source: str | None = None,
        context: ValidationContext | None = None,
        categories: Iterable[str] | None = None,
    ) -> CheckReport:
        """Validate a path, optionally restricting checks to named source categories."""
        categories = _category_selection(categories)
        path = Path(path)
        source = source or str(path)
        try:
            document = gemmi.cif.read_file(str(path), check_level=2)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1048576), b""):
                    digest.update(chunk)
        except (OSError, RuntimeError, ValueError) as exc:
            code = "INPUT_IO_ERROR" if isinstance(exc, OSError) or not path.exists() else "CIF_PARSE_ERROR"
            report = CheckReport(source, [CheckIssue(CheckSeverity.FATAL, code, str(exc), check_type="input")])
            report.validation = self._metadata(report, False, (), (), (), (), None, {}, {}, False, 1, categories)
            return report
        with self._lock:
            return self._validate(document, source, context or ValidationContext(), digest.hexdigest(), categories)

    def validate_document(
        self,
        document: gemmi.cif.Document,
        *,
        source: str,
        context: ValidationContext | None = None,
        categories: Iterable[str] | None = None,
    ) -> CheckReport:
        """Validate without modifying the document, optionally selecting check categories.

        None or an empty collection runs all configured checks. Target categories
        remain available to selected checks. Completeness describes the requested scope.
        """
        categories = _category_selection(categories)
        with self._lock:
            return self._validate(document, source, context or ValidationContext(), None, categories)

    def _metadata(
        self,
        report,
        complete,
        selected,
        excluded,
        phases,
        records,
        digest,
        contexts,
        counters,
        truncated,
        observed,
        categories=None,
    ):
        options = asdict(self.options)
        options.pop("cancelled")
        options["unknown_tag_severity"] = self.options.unknown_tag_severity.value
        phase_records = []
        for phase in self.options.phases:
            record = dict(next((p for p in phases if p["phase"] == phase), {"phase": phase, "status": "skipped"}))
            related = [i for i in report.issues if i.check_type == phase]
            record["retained_findings"] = len(related)
            record["errors"] = sum(i.severity in (CheckSeverity.ERROR, CheckSeverity.FATAL) for i in related)
            record["warnings"] = sum(i.severity == CheckSeverity.WARNING for i in related)
            if phase == "cross":
                record["rows_examined"] = sum(r["rows_examined"] for r in records)
            phase_records.append(record)
        return ValidationMetadata(
            complete,
            complete and report.ok,
            self.options.phases,
            tuple(selected),
            tuple(excluded),
            backend_version=version("gemmi"),
            dictionaries=tuple(self._dictionary.identities) if self._dictionary else (),
            rules=self._bundle.prepared if self._bundle else None,
            input_digest=digest,
            resource_fingerprint=self._fingerprint,
            requested_categories=categories,
            options=options,
            phases=tuple(phase_records),
            rule_execution=tuple(records),
            resolved_context=contexts,
            counters=counters,
            truncated=truncated,
            issues_observed=observed,
            errors=len(report.errors()),
            warnings=len(report.warnings()),
            dictionary_coverage={
                "profile": "PDBx/DDL2 canonical tags",
                "requested": "dictionary" in self.options.phases,
                "incompatible_rule_definitions": self._incompatible_rules,
                "parent_checks": self.options.dictionary_parent_checks,
                "category_scope": categories,
                "native_parent_checks": "dictionary" in self.options.phases
                and self.options.dictionary_parent_checks
                and categories is None,
                "diagnostics_exhaustive": False,
                "aliases_supported": False,
                "alias_declarations": len(self._dictionary.aliases) if self._dictionary else 0,
                "unresolved_parent_tags": tuple(self._dictionary.ambiguous_parents) if self._dictionary else (),
                "unresolved_parent_definitions": self._dictionary.ambiguous_parents if self._dictionary else {},
                "deposition_overrides": self.options.dictionary_deposition_overrides,
            },
        )

    def _context(self, view, supplied):
        override = supplied.block_overrides.get(view.block.name, ValidationContext())
        modes = override.experiment_modes if override.experiment_modes is not None else supplied.experiment_modes
        subtypes = override.entry_subtypes if override.entry_subtypes is not None else supplied.entry_subtypes
        codes = override.requested_codes if override.requested_codes is not None else supplied.requested_codes
        unknown = []
        unknown_count = 0
        origin = "explicit"
        if modes is None:
            origin = "file"
            methods = view.table("exptl")
            inferred = []
            if methods:
                for row in range(methods.row_count):
                    value = text(methods.raw("method", row))
                    if value:
                        value = " ".join(value.upper().split())
                        if value in METHODS and METHODS[value] not in inferred:
                            inferred.append(METHODS[value])
                        elif value not in METHODS:
                            unknown_count += 1
                            if len(unknown) < self.options.max_related_locations:
                                unknown.append(value[: self.options.max_value_chars])
            modes = tuple(inferred) if inferred else None
        context = ValidationContext(modes, subtypes, codes)
        return context, {
            "experiment_modes": modes,
            "entry_subtypes": subtypes,
            "requested_codes": codes,
            "experiment_origin": origin,
            "unknown_methods": tuple(unknown),
            "unknown_method_count": unknown_count,
            "unknown_methods_truncated": unknown_count > len(unknown),
            "subtype_origin": "explicit" if subtypes is not None else "unavailable",
            "code_origin": "explicit" if codes is not None else "unavailable",
        }

    def _validate(self, document, source, context, digest, categories=None):
        report = CheckReport(source)
        collector = Collector(report, self.options)
        names = [block.name for block in document]
        selected = []
        selection_error = not names or len(set(names)) != len(names)
        if self.options.blocks is not None:
            selection_error |= any(names.count(n) != 1 for n in self.options.blocks)
            if not selection_error:
                selected = [names.index(n) for n in self.options.blocks]
        else:
            selected = list(range(len(names)))
        if any(names.count(n) != 1 or n not in [names[i] for i in selected] for n in context.block_overrides):
            selection_error = True
        if selection_error:
            collector.add(
                CheckIssue(
                    CheckSeverity.FATAL,
                    "CIF_BLOCK_SELECTION",
                    "Empty document or unknown/ambiguous block selection or override",
                    check_type="input",
                )
            )
            collector.complete = False
            report.validation = self._metadata(
                report, False, (), tuple(names), (), (), digest, {}, {}, False, 1, categories
            )
            return report
        views = [BlockView(document[index], index) for index in selected]
        selected_names = tuple(names[i] for i in selected)
        excluded = tuple(n for i, n in enumerate(names) if i not in selected)
        phases, records, contexts = [], [], {}
        if categories is not None:
            available = {category for view in views for category in view.tables}
            available.update(r.check_category for r in self.rules.rules if r.check_category)
            if self._dictionary is not None:
                available.update(definition.category for definition in self._dictionary.definitions.values())
            unknown = tuple(c for c in categories if c not in available)
            if unknown:
                collector.add(
                    CheckIssue(
                        CheckSeverity.FATAL,
                        "CIF_CATEGORY_SELECTION",
                        "Unknown validation categories: " + ", ".join(unknown),
                        check_type="input",
                        context={"unknown_categories": unknown},
                    )
                )
                report.validation = self._metadata(
                    report, False, selected_names, excluded, (), (), digest, {}, {}, False, 1, categories
                )
                return report
        resolved_by_index = {}
        for view in views:
            resolved, provenance = self._context(view, context)
            resolved_by_index[view.index] = resolved
            contexts[str(view.index)] = provenance
        blocked = set()
        if self._dictionary is not None:
            for message in self._dictionary.preparation_diagnostics:
                parent_source = re.search(r"for (_[A-Za-z0-9_]+)\.", message)
                if categories is not None and parent_source and parent_source[1].lower().lstrip("_") not in categories:
                    continue
                collector.add(
                    CheckIssue(
                        CheckSeverity.WARNING,
                        "DICTIONARY_RESOURCE_DIAGNOSTIC",
                        message[: self.options.max_diagnostic_chars],
                        check_type="dictionary",
                        context={"preparation": True},
                    )
                )
        for view in views:
            for code, message, location in view.errors:
                if categories is not None and location.category is not None and location.category not in categories:
                    continue
                collector.add(CheckIssue(CheckSeverity.ERROR, code, message, location, check_type="dictionary"))
                blocked.add(view.index)
            if self._dictionary is not None:
                for code, message, location in self._dictionary.input_limitations(view, categories):
                    collector.add(CheckIssue(CheckSeverity.ERROR, code, message, location, check_type="dictionary"))
                    collector.complete = False
                    blocked.add(view.index)
        if "dictionary" in self.options.phases and collector.poll():
            start = time.perf_counter()
            dictionary = self._dictionary
            assert dictionary is not None
            dictionary.events.clear()
            ambiguous = False
            native_error_seen = False

            def diagnostic(message):
                nonlocal ambiguous, native_error_seen
                issue, view = dictionary.diagnose(message, views)
                if issue.severity in (CheckSeverity.ERROR, CheckSeverity.FATAL):
                    native_error_seen = True
                    if view is None:
                        ambiguous = True
                        blocked.update(v.index for v in views)
                    else:
                        blocked.add(view.index)
                collector.add(issue)  # never interrupt the native callback, including after the cap

            ddl = dictionary.native_backend(categories is not None)
            ddl.set_logger((diagnostic, 6))
            target = document
            if categories is not None:
                target = gemmi.cif.Document()
                target.source = document.source
                for view in views:
                    block = target.add_new_block(view.block.name)
                    for item in view.block:
                        if item.loop is not None:
                            tags = item.loop.tags
                        elif isinstance(item.pair, tuple):
                            tags = (cast(str, item.pair[0]),)
                        else:
                            continue
                        if any(t.lower().split(".")[0].lstrip("_") in categories for t in tags):
                            block.add_item(item)
            elif selected != list(range(len(names))):
                target = gemmi.cif.Document()
                for index in selected:
                    target.add_copied_block(document[index])
            try:
                backend_ok = ddl.validate_cif(target)
            finally:
                ddl.set_logger((dictionary.events.append, 6))
            if not backend_ok and not native_error_seen:
                collector.add(
                    CheckIssue(
                        CheckSeverity.ERROR,
                        "DICTIONARY_VALIDATION_FAILED",
                        "Dictionary backend rejected input without an attributable blocking diagnostic",
                        check_type="dictionary",
                    )
                )
                blocked.update(v.index for v in views)
                ambiguous = True
            date_tags = {
                item.lower()
                for plan in (self._bundle.plans if self._bundle else ())
                if isinstance(plan, ComparisonPlan)
                and plan.descriptor.family == "date_order"
                and (categories is None or plan.descriptor.check_category in categories)
                for item in (plan.source, plan.target)
            }
            for view in views:
                if not collector.poll():
                    break
                before = len(report.issues)
                dictionary.supplements(view, collector, date_tags, categories)
                if any(i.severity in (CheckSeverity.ERROR, CheckSeverity.FATAL) for i in report.issues[before:]):
                    blocked.add(view.index)
            phases.append(
                {
                    "phase": "dictionary",
                    "status": "truncated"
                    if collector.truncated
                    else "cancelled"
                    if collector.cancelled
                    else "completed",
                    "duration": time.perf_counter() - start,
                    "backend_ok": backend_ok,
                    "diagnostics_exhaustive": False,
                    "ambiguous_diagnostic_scope": ambiguous,
                }
            )
        if "cross" in self.options.phases:
            assert self._bundle is not None
            start = time.perf_counter()
            for view in views:
                resolved = resolved_by_index[view.index]
                run = Run(view, collector, resolved, self._dictionary, self._bundle.formats)
                for plan in self._bundle.plans:
                    run.begin(plan.descriptor)
                    if categories is not None and plan.descriptor.check_category not in categories:
                        run.record["status"] = "excluded"
                        run.skip("category_not_selected")
                        continue
                    if view.index in blocked:
                        run.skip("blocked_dictionary", blocked=True)
                        continue
                    if not collector.poll():
                        run.record["status"] = "cancelled" if collector.cancelled else "truncated"
                        collector.complete = False
                        continue
                    missing = self._incompatible_rules.get(plan.descriptor.id)
                    if missing:
                        collector.add(
                            CheckIssue(
                                CheckSeverity.WARNING,
                                "CROSS_DICTIONARY_INCOMPATIBLE",
                                "Rule operands have no definition in the selected dictionary",
                                rule_id=plan.descriptor.id,
                                check_type="engine",
                                context={"missing_definitions": missing, "block_index": view.index},
                            )
                        )
                        run.skip("dictionary_definitions_unavailable", blocked=True)
                        continue
                    before = time.perf_counter()
                    if isinstance(plan, ComparisonPlan):
                        evaluators.comparison(plan, run)
                    elif isinstance(plan, PresencePlan):
                        evaluators.presence(plan, run)
                    elif isinstance(plan, EnumerationPlan):
                        evaluators.enumeration(plan, run)
                    else:
                        from onedep_lib.validation.procedural import procedural

                        procedural(plan, run)
                    if self.options.profile:
                        run.record["duration"] = time.perf_counter() - before
                records.extend(run.records)
            phases.append(
                {
                    "phase": "cross",
                    "status": "truncated"
                    if collector.truncated
                    else "cancelled"
                    if collector.cancelled
                    else "blocked"
                    if blocked
                    else "partial"
                    if any(r["status"] == "blocked" for r in records)
                    else "completed",
                    "duration": time.perf_counter() - start,
                }
            )
        complete = collector.complete and (not blocked or "cross" not in self.options.phases)
        rule_order = (
            {plan.descriptor.id: index for index, plan in enumerate(self._bundle.plans)} if self._bundle else {}
        )
        phase_order = {"input": 0, "dictionary": 1, "cross": 2, "engine": 3}

        def issue_order(issue):
            terminal = issue.rule_id is None and issue.code in ("VALIDATION_LIMIT_REACHED", "VALIDATION_CANCELLED")
            return (
                4 if terminal else phase_order.get(issue.check_type, 3),
                issue.location.block_index if issue.location.block_index is not None else -1,
                rule_order.get(issue.rule_id, -1),
                issue.location.row if issue.location.row is not None else -1,
                issue.context.get("branch", 0),
            )

        report.issues.sort(key=issue_order)
        counters = {"parses": 0 if digest is None else 1, "index_builds": sum(v.index_builds for v in views)}
        report.validation = self._metadata(
            report,
            complete,
            selected_names,
            excluded,
            phases,
            records,
            digest,
            contexts,
            counters,
            collector.truncated,
            collector.observed,
            categories,
        )
        _LOG.debug(
            "Validation complete: source=%s errors=%d warnings=%d complete=%s",
            source,
            len(report.errors()),
            len(report.warnings()),
            complete,
        )
        return report
