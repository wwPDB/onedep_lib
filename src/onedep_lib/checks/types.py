from __future__ import annotations

from typing import Protocol

from onedep_lib.checks.report import CheckReport
from onedep_lib.enums import EMSubType, ExperimentType
from onedep_lib.session.models import LocalFile


class CheckRunner(Protocol):
    def check_required_files(
        self,
        files: list[LocalFile],
        experiment_type: ExperimentType | None,
        em_subtype: EMSubType | None = None,
    ) -> CheckReport: ...
