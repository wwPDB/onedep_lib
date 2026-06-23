from typing import Union
from onedep_lib.apis.deposit.models import (
    WwPDBDeposition,
    DepositedFile,
    DepositError,
    DepositStatus,
    Experiment,
)
from onedep_lib.enums import Country, FileType


def _stub_deposit(dep_id: str = "D_999", email: str = "test@example.com") -> WwPDBDeposition:
    return WwPDBDeposition(
        dep_id=dep_id,
        email=email,
        pdb_id=None,
        emdb_id=None,
        bmrb_id=None,
        title="",
        hold_exp_date=None,
        created="2024-01-01T00:00:00",
        last_login="2024-01-01T00:00:00",
        site="pdbe",
        status="DEP",
    )


def _stub_file(file_id: int = 1, file_type: FileType = FileType.MMCIF_COORD) -> DepositedFile:
    return DepositedFile(
        file_id=file_id,
        name="f.cif",
        file_type=file_type,
        created="Monday, January 01, 2024 00:00:00",
    )


class StubApiClient:
    """In-memory ApiClient for unit-testing the Deposition facade.
    Satisfies the ApiClient Protocol structurally — no import of ApiClient needed.
    """

    def __init__(self) -> None:
        self.deposited_files: list[str] = []
        self.processed: list[str] = []

    def create_deposition(self, email, users, country, experiments, password="") -> WwPDBDeposition:
        return _stub_deposit(email=email)

    def get_all_depositions(self) -> list[WwPDBDeposition]:
        return []

    def get_deposition(self, dep_id: str) -> WwPDBDeposition:
        return _stub_deposit(dep_id=dep_id)

    def upload_file(self, dep_id, file_path, file_type, overwrite=False) -> DepositedFile:
        self.deposited_files.append(file_path)
        return _stub_file(file_type=file_type)

    def update_metadata(self, dep_id, file_id, spacing_x, spacing_y, spacing_z, contour, description) -> DepositedFile:
        return _stub_file(file_id=file_id)

    def get_files(self, dep_id) -> list[DepositedFile]:
        return []

    def remove_file(self, dep_id, file_id) -> bool:
        return True

    def get_status(self, dep_id) -> Union[DepositStatus, DepositError]:
        return DepositStatus(
            status="DEP",
            action="deposit",
            step="1",
            details="deposited",
            date="2024-01-01T00:00:00",
        )

    def process(self, dep_id) -> Union[DepositStatus, DepositError]:
        self.processed.append(dep_id)
        return DepositStatus(
            status="PROC",
            action="process",
            step="1",
            details="processing",
            date="2024-01-01T00:00:00",
        )


def test_stub_api_client_is_structurally_compatible():
    from onedep_lib.apis.deposit.types import ApiClient

    stub = StubApiClient()
    required = [
        "create_deposition",
        "get_all_depositions",
        "get_deposition",
        "upload_file",
        "update_metadata",
        "get_files",
        "remove_file",
        "get_status",
        "process",
    ]
    for method in required:
        assert callable(getattr(stub, method, None)), f"Missing: {method}"
