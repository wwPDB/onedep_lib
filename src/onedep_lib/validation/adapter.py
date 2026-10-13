"""One shared mmCIF adapter for deposition routes and legacy check entry points."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Union

import gemmi

from onedep_lib.checks.report import CheckIssue, CheckReport, CheckSeverity, CifLocation
from onedep_lib.enums import EMSubType, ExperimentType, FileType
from onedep_lib.validation.cif_access import BlockView, tag
from onedep_lib.validation.engine import MmCifValidator
from onedep_lib.validation.models import (
    ValidationConfigurationError,
    ValidationContext,
    ValidationMetadata,
    ValidationOptions,
)

_LOG = logging.getLogger(__name__)
MMCIF_TYPES = frozenset((FileType.MMCIF_COORD, FileType.MD_CIF, FileType.CRYSTAL_STRUC_FACTORS))
ValidatorOverride = Union[MmCifValidator, Mapping[FileType, MmCifValidator], None]


def session_context(experiment: ExperimentType | None, subtype: EMSubType | None) -> ValidationContext:
    modes = {
        ExperimentType.XRAY: "xray",
        ExperimentType.NEUTRON: "nd",
        ExperimentType.FIBER: "fd",
        ExperimentType.EM: "em",
        ExperimentType.EC: "ec",
        ExperimentType.NMR: "nmr",
        ExperimentType.SSNMR: "ssnmr",
    }
    subtypes = {
        EMSubType.HELICAL: "EM-helical",
        EMSubType.SPA: "EM-single_part",
        EMSubType.SUBTOMOGRAM: "EM-subtomogram",
    }
    return ValidationContext(
        (modes[experiment],) if experiment in modes else None,
        ("EC",) if experiment == ExperimentType.EC else (subtypes[subtype],) if subtype in subtypes else None,
    )


def unavailable(source: str, message: str, *, fatal=False, code="VALIDATION_UNAVAILABLE") -> CheckReport:
    report = CheckReport(
        source,
        [CheckIssue(CheckSeverity.FATAL if fatal else CheckSeverity.WARNING, code, message, check_type="engine")],
    )
    report.validation = ValidationMetadata(
        False,
        False,
        ("dictionary", "cross"),
        phases=({"phase": "dictionary", "status": "skipped"}, {"phase": "cross", "status": "skipped"}),
    )
    return report


class MmcifAdapter:
    def __init__(self, config=None, override: ValidatorOverride = None):
        self.routes: dict[FileType, MmCifValidator] = {}
        self.errors: dict[FileType, tuple[str, str]] = {}
        self.dictionary_only: set[FileType] = set()
        if isinstance(override, MmCifValidator):
            self.routes[FileType.MMCIF_COORD] = override
        elif override is not None:
            for file_type, engine in override.items():
                kind = FileType(file_type)
                if kind not in MMCIF_TYPES or not isinstance(engine, MmCifValidator):
                    raise ValidationConfigurationError("Invalid prepared validator route")
                self.routes[kind] = engine
        if config is None or not config.mmcif_dictionary_paths:
            return
        prepared: dict[tuple, MmCifValidator] = {}
        failed: dict[tuple, tuple[str, str]] = {}
        for kind in sorted(MMCIF_TYPES, key=lambda k: k.value):
            if kind in self.routes:
                continue
            rules_path = config.mmcif_rule_paths.get(kind.value)
            cross = rules_path is not None or kind == FileType.MMCIF_COORD
            profile = (tuple(config.mmcif_dictionary_paths), rules_path, cross)
            if profile not in prepared and profile not in failed:
                try:
                    prepared[profile] = MmCifValidator.load(
                        config.mmcif_dictionary_paths,
                        rules_path=rules_path,
                        options=ValidationOptions(phases=("dictionary", "cross") if cross else ("dictionary",)),
                    )
                except ValidationConfigurationError as exc:
                    failed[profile] = ("VALIDATION_UNAVAILABLE", str(exc))
                except Exception as exc:
                    _LOG.exception("Validation preparation failed")
                    failed[profile] = ("VALIDATION_INTERNAL_ERROR", str(exc))
            if profile in failed:
                self.errors[kind] = failed[profile]
            else:
                self.routes[kind] = prepared[profile]
                if not cross:
                    self.dictionary_only.add(kind)

    def check_file(self, file, *, context: ValidationContext | None = None) -> CheckReport:
        kind = file.file_type
        if kind in self.errors:
            code, message = self.errors[kind]
            return unavailable(file.file_id, message, fatal=True, code=code)
        engine = self.routes.get(kind)
        if engine is None:
            return unavailable(file.file_id, "mmCIF validator is unconfigured; validation skipped")
        try:
            report = engine.validate_path(Path(file.file_path), source=file.file_id, context=context)
        except Exception as exc:
            _LOG.exception("mmCIF validation failed for file %s", file.file_id)
            return unavailable(file.file_id, str(exc), fatal=True, code="VALIDATION_INTERNAL_ERROR")
        if kind in self.dictionary_only:
            report.issues.append(
                CheckIssue(
                    CheckSeverity.WARNING,
                    "CROSS_RULESET_UNCONFIGURED",
                    "No cross-check rule file is configured for this file type",
                    check_type="engine",
                    context={"file_type": kind.value},
                )
            )
            assert report.validation is not None
            report.validation = replace(
                report.validation,
                complete=False,
                valid=False,
                requested_phases=("dictionary", "cross"),
                warnings=len(report.warnings()),
                phases=report.validation.phases
                + ({"phase": "cross", "status": "skipped", "reason": "file_type_rules_unconfigured"},),
            )
        return report

    def presence(self, file, category: str, item: str | None = None) -> CheckReport:
        report = CheckReport(file.file_id)
        try:
            document = gemmi.cif.read_file(file.file_path, check_level=2)
        except (OSError, RuntimeError, ValueError) as exc:
            report.issues.append(
                CheckIssue(
                    CheckSeverity.FATAL,
                    "INPUT_IO_ERROR" if isinstance(exc, OSError) else "CIF_PARSE_ERROR",
                    str(exc),
                    check_type="input",
                )
            )
            return report
        category = category.lstrip("_").rstrip(".").lower()
        if not document:
            report.issues.append(
                CheckIssue(CheckSeverity.FATAL, "CIF_BLOCK_SELECTION", "Empty mmCIF document", check_type="input")
            )
        for index, block in enumerate(document):
            view = BlockView(block, index)
            for code, message, location in view.errors:
                report.issues.append(CheckIssue(CheckSeverity.ERROR, code, message, location, check_type="input"))
            table = view.table(category)
            if table is None or item is not None and not table.has(item):
                location = (
                    table.location(item)
                    if table
                    else CifLocation(block.name, category, tag(category, item) if item else None, block_index=index)
                )
                report.issues.append(
                    CheckIssue(
                        CheckSeverity.ERROR,
                        "CIF_FIELD_MISSING" if item else "CIF_CATEGORY_MISSING",
                        f"Missing {tag(category, item) if item else category}",
                        location,
                        check_type="input",
                    )
                )
        return report
