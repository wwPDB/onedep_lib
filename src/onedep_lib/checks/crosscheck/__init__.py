"""
Cross-check validation of mmCIF files using gemmi.

Rules come from onedep_lib/schemas/cif/cross_checks_deposition_rules.cif, whose
categories are defined in mmcif_pdbx_deposition_rules_ext.dic; item types and
enumerations come from the PDBx/mmCIF dictionary mmcif_pdbx_v50.dic. All are
parsed with gemmi.cif. Dictionary validation of the input file is not done here.
"""

from onedep_lib.checks.crosscheck.checks import Context, CrossChecker, Finding, validate
from onedep_lib.checks.crosscheck.dictionary import (
    DEFAULT_DICTIONARY_PATH,
    DEFAULT_EXTENSION_PATH,
    Dictionary,
    load_dictionary,
    load_extension,
)
from onedep_lib.checks.crosscheck.rules import (
    DEFAULT_RULES_PATH,
    Condition,
    ItemName,
    Join,
    RowSelection,
    Rule,
    RulesError,
    RuleSet,
    load_rules,
)

__all__ = [
    "Condition",
    "Context",
    "CrossChecker",
    "DEFAULT_DICTIONARY_PATH",
    "DEFAULT_EXTENSION_PATH",
    "DEFAULT_RULES_PATH",
    "Dictionary",
    "Finding",
    "ItemName",
    "Join",
    "RowSelection",
    "Rule",
    "RuleSet",
    "RulesError",
    "load_dictionary",
    "load_extension",
    "load_rules",
    "validate",
]
