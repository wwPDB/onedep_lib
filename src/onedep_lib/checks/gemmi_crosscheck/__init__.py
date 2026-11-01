"""
Cross-check validation of mmCIF files using gemmi.

Rules come from onedep_lib/schemas/cif/cross_checks.cif; both the rule store and the
input file are parsed with gemmi.cif. Dictionary validation is not done here.
"""

from onedep_lib.checks.gemmi_crosscheck.checks import Context, CrossChecker, Finding, detect_context, validate
from onedep_lib.checks.gemmi_crosscheck.rules import DEFAULT_RULES_PATH, load_rules

__all__ = [
    "Context",
    "CrossChecker",
    "DEFAULT_RULES_PATH",
    "Finding",
    "detect_context",
    "load_rules",
    "validate",
]
