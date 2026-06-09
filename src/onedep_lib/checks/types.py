from __future__ import annotations

from typing import Protocol

from onedep_lib.checks.report import CheckReport
from onedep_lib.enums import EMSubType, ExperimentType, FileType
from onedep_lib.session.models import LocalFile


class CheckRunner(Protocol):
    def check_required_files(
        self,
        files: list[LocalFile],
        experiment_type: ExperimentType | None,
        em_subtype: EMSubType | None = None,
    ) -> CheckReport: ...

    def check_mmcif_file(self, file: LocalFile) -> CheckReport: ...

    def check_mmcif_category(self, file: LocalFile, category: str) -> CheckReport: ...

    def check_mmcif_field(self, file: LocalFile, category: str, field: str) -> CheckReport: ...

    def check_file_type(self, file: LocalFile, file_type: FileType) -> CheckReport: ...
