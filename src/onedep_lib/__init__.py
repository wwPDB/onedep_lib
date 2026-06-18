"""onedep_lib — Deposition Software Provider library for OneDep."""

from onedep_lib.apis.deposit.enums import Status
from onedep_lib.apis.deposit.models import DepositError, DepositStatus
from onedep_lib.apis.deposit.types import ApiClient
from onedep_lib.auths.token import TokenStore
from onedep_lib.auths.types import AuthProvider
from onedep_lib.checks.report import CheckIssue, CheckReport, CheckSeverity, CifLocation
from onedep_lib.dsp import (
    Deposition,
    deposit_init,
    deposit_resume,
    get_session,
    get_session_metadata,
    list_session_metadata,
    list_sessions,
)
from onedep_lib.enums import Country, EMSubType, ExperimentType, FileType
from onedep_lib.exceptions import ApiError, DepositApiException, OneDepError
from onedep_lib.session.models import SessionSummary

__all__ = [
    # factories / facade
    "deposit_init",
    "deposit_resume",
    "get_session",
    "get_session_metadata",
    "list_session_metadata",
    "list_sessions",
    "SessionSummary",
    "Deposition",
    # check result types
    "CheckReport",
    "CheckIssue",
    "CheckSeverity",
    "CifLocation",
    # domain enums
    "Country",
    "EMSubType",
    "ExperimentType",
    "FileType",
    # API response models
    "DepositStatus",
    "DepositError",
    "Status",
    # exceptions
    "OneDepError",
    "ApiError",
    "DepositApiException",
    # auth
    "TokenStore",
    "AuthProvider",
    # protocols
    "ApiClient",
]
