from typing import Protocol, Union

from onedep_lib.apis.deposit.models import (
    DepositedFile,
    DepositError,
    DepositStatus,
    Experiment,
    WwPDBDeposition,
)
from onedep_lib.enums import Country, FileType


class ApiClient(Protocol):
    def create_deposition(
        self,
        email: str,
        users: list[str],
        country: Country,
        experiments: list[Experiment],
        password: str = "",
    ) -> WwPDBDeposition: ...

    def get_all_depositions(self) -> list[WwPDBDeposition]: ...

    def get_deposition(self, dep_id: str) -> WwPDBDeposition: ...

    def upload_file(
        self,
        dep_id: str,
        file_path: str,
        file_type: FileType,
        overwrite: bool = False,
    ) -> DepositedFile: ...

    def update_metadata(
        self,
        dep_id: str,
        file_id: int,
        spacing_x: float,
        spacing_y: float,
        spacing_z: float,
        contour: float,
        description: str,
    ) -> DepositedFile: ...

    def get_files(self, dep_id: str) -> list[DepositedFile]: ...

    def remove_file(self, dep_id: str, file_id: int) -> bool: ...

    def get_status(self, dep_id: str) -> Union[DepositStatus, DepositError]: ...

    def process(self, dep_id: str) -> Union[DepositStatus, DepositError]: ...
