"""
Cross-check validation of mmCIF files using gemmi.

Rules come from onedep_lib/schemas/cif/cross_checks_deposition_rules.cif (the
_pdbx_deposition_rule categories); both the rule store and the input file are
parsed with gemmi.cif. Dictionary validation is not done here.
"""

from onedep_lib.checks.crosscheck.checks import Context, CrossChecker, Finding, detect_context, validate
from onedep_lib.checks.crosscheck.rules import DEFAULT_RULES_PATH, Condition, Rule, RulesError, load_rules

__all__ = [
    "Condition",
    "Context",
    "CrossChecker",
    "DEFAULT_RULES_PATH",
    "Finding",
    "Rule",
    "RulesError",
    "detect_context",
    "load_rules",
    "validate",
]
