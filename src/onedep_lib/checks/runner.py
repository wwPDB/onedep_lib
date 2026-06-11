from __future__ import annotations

from collections import Counter

import jsonschema

from onedep_lib.checks.report import CheckIssue, CheckReport, CheckSeverity
from onedep_lib.enums import EMSubType, ExperimentType, FileType
from onedep_lib.exceptions import SchemaError
from onedep_lib.schemas.types import SchemaProvider
from onedep_lib.session.models import LocalFile

_COORD_TYPES = {"co-pdb", "co-cif"}
_SF_TYPES = {"xs-cif", "xs-mtz"}
_EC_DATA_TYPES = {"vo-map", "xs-cif", "xs-mtz"}
_NMR_UNIFIED_TYPES = {"nm-uni-nef", "nm-uni-str"}
_NMR_RESTRAINT_TYPES = {
    "nm-res-amb",
    "nm-res-bio",
    "nm-res-cha",
    "nm-res-cns",
    "nm-res-cya",
    "nm-res-dyn",
    "nm-res-gro",
    "nm-res-isd",
    "nm-res-ros",
    "nm-res-syb",
    "nm-res-xpl",
    "nm-res-oth",
}
_HALF_MAP_SUBTYPES = {"single", "helical", "subtomogram"}


def _human_readable_messages(
    filetypes: list[str], experiment_type: ExperimentType, em_subtype: EMSubType | None
) -> list[str]:
    counts = Counter(filetypes)
    present = set(filetypes)
    messages: list[str] = []

    if experiment_type != ExperimentType.EM and not present.intersection(_COORD_TYPES):
        messages.append("Missing required coordinate file: expected one of co-pdb or co-cif")

    if experiment_type in {ExperimentType.XRAY, ExperimentType.NEUTRON}:
        if not present.intersection(_SF_TYPES):
            messages.append("Missing required structure factors file: expected one of xs-cif or xs-mtz")

    if experiment_type == ExperimentType.FIBER and "layer-lines" not in present:
        messages.append("Missing required fiber diffraction file: expected layer-lines")

    if experiment_type == ExperimentType.EM:
        if not em_subtype:
            messages.append("Missing required EM subtype")
        if "img-emdb" not in present:
            messages.append("Missing required EM image file: expected img-emdb")
        if "vo-map" not in present:
            messages.append("Missing required EM map file: expected vo-map")
        if em_subtype and em_subtype.value in _HALF_MAP_SUBTYPES and counts["half-map"] < 2:
            messages.append("Missing required half-map files: expected 2 half-map files")

    if experiment_type == ExperimentType.EC and not present.intersection(_EC_DATA_TYPES):
        messages.append("Missing required EC data file: expected at least one of vo-map, xs-cif, or xs-mtz")

    if experiment_type in {ExperimentType.NMR, ExperimentType.SSNMR}:
        if not present.intersection(_NMR_UNIFIED_TYPES):
            if "nm-shi" not in present:
                messages.append("Missing required chemical shifts file: expected nm-shi")
            if not present.intersection(_NMR_RESTRAINT_TYPES):
                messages.append("Missing required NMR restraints file: expected at least one nm-res-* file")

    return messages


class CheckRunner:
    def __init__(self, schema_provider: SchemaProvider) -> None:
        self._schema_provider = schema_provider

    def check_required_files(
        self,
        files: list[LocalFile],
        experiment_type: ExperimentType | None,
        em_subtype: EMSubType | None = None,
    ) -> CheckReport:
        if experiment_type is None:
            return CheckReport(
                source="session",
                issues=[
                    CheckIssue(
                        severity=CheckSeverity.WARNING,
                        code="EXPERIMENT_TYPE_UNSET",
                        message="Experiment type not set - required-file check skipped",
                    )
                ],
            )

        try:
            schema = self._schema_provider.get_schema("required_files")
        except SchemaError as exc:
            return CheckReport(
                source="session",
                issues=[
                    CheckIssue(
                        severity=CheckSeverity.WARNING,
                        code="SCHEMA_UNAVAILABLE",
                        message=f"Required-files schema not available: {exc}",
                    )
                ],
            )

        data: dict[str, object] = {
            "method": experiment_type.value,
            "files": [file.file_type.value for file in files],
        }
        if em_subtype:
            data["subtype"] = em_subtype.value

        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(data))
        if not errors:
            return CheckReport(source="session")

        filetypes = [file.file_type.value for file in files]
        messages = _human_readable_messages(filetypes, experiment_type, em_subtype)
        if not messages:
            messages = [error.message for error in errors]

        return CheckReport(
            source="session",
            issues=[
                CheckIssue(
                    severity=CheckSeverity.FATAL,
                    code="REQ_FILES_MISSING",
                    message=message,
                )
                for message in messages
            ],
        )

    def check_mmcif_file(self, file: LocalFile) -> CheckReport:
        return self._schema_check(file, "mmcif_base")

    def check_mmcif_category(self, file: LocalFile, category: str) -> CheckReport:
        return self._schema_check(file, f"mmcif_category_{category}")

    def check_mmcif_field(self, file: LocalFile, category: str, field: str) -> CheckReport:
        return self._schema_check(file, f"mmcif_field_{category}_{field}")

    def check_file_type(self, file: LocalFile, file_type: FileType) -> CheckReport:
        return self._schema_check(file, f"filetype_{file_type.value.replace('-', '_')}")

    def _schema_check(self, file: LocalFile, schema_name: str) -> CheckReport:
        try:
            self._schema_provider.get_schema(schema_name)
        except SchemaError:
            return CheckReport(
                source=file.file_id,
                issues=[
                    CheckIssue(
                        severity=CheckSeverity.INFO,
                        code="SCHEMA_UNAVAILABLE",
                        message=f"Schema '{schema_name}' not available - check skipped",
                    )
                ],
            )
        return CheckReport(source=file.file_id)
