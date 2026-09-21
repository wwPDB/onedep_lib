"""onedep_lib — Deposition Software Provider library for OneDep."""

import logging

from onedep_lib.apis.deposit.enums import Status
from onedep_lib.apis.deposit.models import DepositError, DepositStatus
from onedep_lib.apis.deposit.types import ApiClient
from onedep_lib.auths.token import TokenStore
from onedep_lib.auths.types import AuthProvider
from onedep_lib.checks.report import CheckIssue, CheckReport, CheckSeverity, CifLocation
from onedep_lib.dsp import Deposition, check_auth_key, deposit_init, deposit_resume, list_sessions, validate_json_file, validate_mmcif_file
from onedep_lib.enums import Country, EMSubType, ExperimentType, FileType
from onedep_lib.exceptions import (
    ApiError,
    ApiUnreachableError,
    DepositApiException,
    OneDepError,
)
from onedep_lib.schemas.cif_to_json import cif2json

# Library logging is opt-in: a NullHandler on the top-level logger means nothing
# is emitted unless the embedding application configures a handler for it.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    # factories / facade
    "deposit_init",
    "deposit_resume",
    "list_sessions",
    "check_auth_key",
    "validate_json_file",
    "validate_mmcif_file",
    "cif2json",
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
    "ApiUnreachableError",
    "DepositApiException",
    # auth
    "TokenStore",
    "AuthProvider",
    # protocols
    "ApiClient",
]
