from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import jsonschema
from jsonschema import validators
from onedep_lib.config import DepositConfig
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from onedep_lib.checks.report import CheckIssue, CheckReport, CheckSeverity
from onedep_lib.enums import EMSubType, ExperimentType, FileType
from onedep_lib.exceptions import SchemaError
from onedep_lib.schemas.types import SchemaProvider
from onedep_lib.session.models import LocalFile
from onedep_lib.checks.keywords import Keywords


class CheckRunner:
    """Runs JSON-schema-based checks against deposition files and sessions.

    Uses a SchemaProvider to fetch the JSON schemas checks are validated
    against, either from a local bundled cache or a remote schema service
    depending on how the provider was constructed.
    """

    validator_specification = jsonschema.Draft202012Validator
    referencing_specification = DRAFT202012


    def __init__(self, schema_provider: SchemaProvider) -> None:
        self._schema_provider = schema_provider
        self.keywords = Keywords().registry()
        self.validator = validators.extend(getattr(CheckRunner, "validator_specification"), self.keywords)

    def check_required_files(
        self,
        files: list[LocalFile],
        experiment_type: ExperimentType | None,
        em_subtype: EMSubType | None = None,
    ) -> CheckReport:
        """Check that files satisfy the required-file rules for experiment_type.

        Returns a report with a single WARNING issue if experiment_type is
        None (check skipped) or if the required-files schema could not be
        fetched, or a FATAL issue per missing requirement otherwise.

        Args:
            files: The files registered on the session.
            experiment_type: The experiment type to check requirements for,
                or None to skip the check.
            em_subtype: The EM experiment subtype, if applicable.

        Returns:
            A CheckReport with source="session".
        """
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
            subfolder = DepositConfig().required_files_subfolder
            schema_name = DepositConfig().required_files_schema
            schema = self._schema_provider.get_schema(schema_name, subfolder)
            resources = [
                (
                    f"{name}.json",
                    Resource(
                        contents=self._schema_provider.get_schema(name, subfolder),
                        specification=CheckRunner.referencing_specification,
                    ),
                )
                for name in DepositConfig().required_files_subschemas
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
        validator = self.validator(schema, registry=registry)
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

    def validate_json_file(self, json_file_path: str, schema_subfolder: str, target_schema_name: str) -> CheckReport:
        try:
            with open(json_file_path, "r") as r:
                data:dict = json.load(r)
        except FileNotFoundError:
            sys.exit("json file not found")
        try:
            schema_dir = DepositConfig().local_schema_cache_dir
            subfolder = schema_subfolder
            subfolder_path = Path(schema_dir / subfolder)
            schema_name = os.path.splitext(os.path.basename(target_schema_name))[0]
            subschemas = [os.path.splitext(filename)[0] for filename in os.listdir(subfolder_path)]
            assert len(subschemas) >= 1, "error reading schema subfolder"
            assert schema_name in subschemas, "error reading schema subfolder"
            length = len(subschemas)
            subschemas.remove(schema_name)
            assert len(subschemas) == length - 1, "error trimming schema subfolder"
            schema = self._schema_provider.get_schema(schema_name, subfolder)
            resources = [
                (
                    f"{name}.json",
                    Resource(
                        contents=self._schema_provider.get_schema(name, subfolder),
                        specification=CheckRunner.referencing_specification,
                    ),
                )
                for name in subschemas
            ]
        except SchemaError as exc:
            return CheckReport(
                source="session",
                issues=[
                    CheckIssue(
                        severity=CheckSeverity.WARNING,
                        code="SCHEMA_UNAVAILABLE",
                        message=f"Schema not available: {exc}",
                    )
                ],
            )

        registry = Registry().with_resources(resources)
        validator = self.validator(schema, registry=registry)
        errors = list(validator.iter_errors(data))
        if not errors:
            return CheckReport(source="session")

        messages = []
        for error in errors:
            print(error)
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
        """Check that file is a structurally valid mmCIF file.

        Args:
            file: The file to check.

        Returns:
            A CheckReport with source=file.file_id.
        """
        return self._schema_check(file, "mmcif_base")

    def check_mmcif_category(self, file: LocalFile, category: str) -> CheckReport:
        """Check that file's mmCIF content contains the given category.

        Args:
            file: The file to check.
            category: The mmCIF category name expected to be present.

        Returns:
            A CheckReport with source=file.file_id.
        """
        return self._schema_check(file, f"mmcif_category_{category}")

    def check_mmcif_field(self, file: LocalFile, category: str, field: str) -> CheckReport:
        """Check that file's mmCIF content contains the given field in category.

        Args:
            file: The file to check.
            category: The mmCIF category name the field is expected in.
            field: The mmCIF field name expected to be present.

        Returns:
            A CheckReport with source=file.file_id.
        """
        return self._schema_check(file, f"mmcif_field_{category}_{field}")

    def check_file_type(self, file: LocalFile, file_type: FileType) -> CheckReport:
        """Check that file matches the schema expected for file_type.

        Args:
            file: The file to check.
            file_type: The expected FileType.

        Returns:
            A CheckReport with source=file.file_id.
        """
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
