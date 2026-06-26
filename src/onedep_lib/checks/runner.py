from __future__ import annotations

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from onedep_lib.checks.report import CheckIssue, CheckReport, CheckSeverity
from onedep_lib.enums import EMSubType, ExperimentType, FileType
from onedep_lib.exceptions import SchemaError
from onedep_lib.schemas.types import SchemaProvider
from onedep_lib.session.models import LocalFile


class CheckRunner:

    subschemas: list[str] = ["xray", "neutron", "fiber", "em", "nmr", "ec"]
    validator_specification = jsonschema.Draft202012Validator
    referencing_specification = DRAFT202012

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
            resources = [
                (f"{name}.json", Resource(contents=self._schema_provider.get_schema(name), specification=CheckRunner.referencing_specification))
                for name in CheckRunner.subschemas
            ]
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

        registry = Registry().with_resources(resources)
        validator = CheckRunner.validator_specification(schema, registry=registry)
        errors = list(validator.iter_errors(data))
        if not errors:
            return CheckReport(source="session")

        messages = []
        for error in errors:
            error_schema = error.schema
            feedback = error_schema.get("feedback", {})
            message = feedback.get(error.validator, None)
            if message:
                messages.append(message)

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
