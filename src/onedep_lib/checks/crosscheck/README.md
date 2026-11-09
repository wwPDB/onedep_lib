# crosscheck

Runs the deposition rules defined in
`onedep_lib/schemas/cif/cross_checks_deposition_rules.cif` on mmCIF files.
[gemmi](https://gemmi.readthedocs.io/) parses both the rule store and the input
file, so this package has no CIF parser of its own. Dictionary validation
(types, enumerations, mandatory items, foreign keys) is not done here.

| File | Purpose |
|---|---|
| `rules.py` | Reads the rule store with `gemmi.cif` into one `Rule` per `_pdbx_deposition_rule` row, with its WHEN / THEN / UNLESS `Condition`s (ordered by `ordinal`) and its parameters attached. |
| `checks.py` | `Category` wraps a gemmi mmCIF table (`Block.find_mmcif_category`) and reads values column by column; `CrossChecker` runs each enabled rule by family; `validate()` is the entry point. |
| `__main__.py` | Command line interface. |

## The rule store

The store's three categories are defined in
`schemas/cif/mmcif_pdbx_deposition_rules_ext.dic`:

- `_pdbx_deposition_rule`: id, family, severity (`ERROR`, `WARNING`, `INFO`),
  message (with `[{placeholder}]` fields), `enabled`, and `procedural_method`
  for procedural rules.
- `_pdbx_deposition_rule_condition`: the clauses. A rule is checked on the
  rows where its WHEN conditions hold (all of them, or any for a `group_operator`
  OR group) and its UNLESS conditions do not, and it reports the THEN
  conditions that fail. A WHEN condition on another category is matched through
  the rule's `join_item` / `join_other_item` parameters when it has them, and
  against any row of that category otherwise.
- `_pdbx_deposition_rule_param`: name/value settings, listed below.

How each family is checked:

| Family | Check |
|---|---|
| `pairwise_order` | THEN `item OP_ITEM other_item`, comparing numbers, or dates when both values are dates. Joined rows with `join_item` / `join_other_item` (plus `fallback`, `absolute`); otherwise the same row in one category, or every row of the other category. |
| `uniqueness` | The THEN items marked `UNIQUE` form a key that may not repeat. |
| `conditional_presence` | THEN items must be non-empty where the rule applies. Without WHEN: `mandatory_scope` rules (`makeMandatory`, or `makeMandatorySubtypes` with `subtype`) require them in any row that has a value outside `exclude_items`; an OR group requires one of them somewhere in the category. `skip_if_any_category_present` and `break_flag` (report at most one such rule per item and row) are honoured. |
| `conditional_value`, `format` | THEN values that are present must satisfy their operator (`EQ`, `IN`, `GE`, `MATCHES`, `NOT_MATCHES`, …). |
| `cross_reference` | THEN `item IN_COLUMN_OF other_item`. Gated by `experiment_scope` (`all`, or `coded` with `experiment_code`) or `experiment_modes`. With `other_item_domain` = `DICTIONARY_ENUMERATION`, the allowed values come from the dictionary's `_item_enumeration.detail` instead (needs `--dict`). |
| `aggregate` | Sums the THEN item over `group_by` and compares the total with `threshold`. |
| `procedural` | A built-in check named by `procedural_method`: `wavelength_protocol_consistency`, `accession_format_rule` / `conditional_accession_format_rule` (named `format`s, `format_match`), `sequence_predicate` (`homopolymer_ala`), `seq_model_mismatch`. |

An unknown family, operator or procedural method raises `RulesError` rather
than being skipped.

## Install

gemmi is an optional dependency of onedep_lib:

```bash
pip install "onedep_lib[gemmi]"
```

Tested with gemmi 0.7.5 and Python 3.9+.

## Validate a file from the command line

```bash
python -m onedep_lib.checks.crosscheck path/to/entry.cif
```

Several files can be given at once. Example output:

```
8ozl.cif: 7 issue(s)
WARNING: _refine_ls_shell.R_factor_R_work (line 4509) [pairwise_refine_ls_shell_r_factor_r_work_1]
  Shell R_work is higher than shell R_free 0.000. (value=2.166, condition > _refine_ls_shell.R_factor_R_free)
ERROR: _em_software.version (line 8100, row 10) [condreq_em_software_5]
  RELION software version must be provided.
```

Each finding names the item, its location, and the id of the rule that
produced it. gemmi keeps line numbers per item, not per value: for a key-value
item the line is that item's line; for a loop it is the line of the loop's
`loop_` and `row` is the 1-based row in the loop.

Exit status: `0` no errors (warnings and info allowed), `1` at least one error,
`2` a file or the rule store could not be read.

### Options

| Option | Effect |
|---|---|
| `--json` | Print findings as JSON (`severity`, `item`, `message`, `rule_id`, `line`, `row`). |
| `--errors-only` | Drop warnings and info findings. |
| `--dict FILE` | PDBx/mmCIF dictionary; enables the `DICTIONARY_ENUMERATION` cross-reference rules. Skipped without it. |
| `--expt MODE` | Experiment mode for rules gated on `experiment_modes` (`xray`, `nd`, `fd`, `ec`, `em`, `nmr`, `ssnmr`). Repeatable. |
| `--subtype NAME` | Entry subtype for `makeMandatorySubtypes` rules (`EC`, `EM-helical`, `EM-single_part`, `EM-subtomogram`). Repeatable. |
| `--code CODE` | Requested accession code for rules with `experiment_scope` = `coded` (e.g. `PDB`). Repeatable. |
| `--detect-context` | Also derive experiment modes from `_exptl.method` and EM subtypes from `_em_experiment.reconstruction_method`. |
| `--lenient` | Accept duplicate tags (last value wins) instead of rejecting the file. Not available for `.gz` files. |
| `--rules FILE` | Use another rule store instead of the packaged `cross_checks_deposition_rules.cif`. |

Without `--expt`, `--subtype`, `--code` or `--detect-context`, gated rules are
skipped.

Examples:

```bash
# X-ray entry, gated rules on, JSON output
python -m onedep_lib.checks.crosscheck --expt xray --json entry.cif

# Let the file say which method it is, and check dictionary enum pairings
python -m onedep_lib.checks.crosscheck --detect-context --dict mmcif_pdbx_v50.dic entry.cif
```

## Use from Python

```python
from pathlib import Path
from onedep_lib.checks.crosscheck import Context, load_rules, validate

rules = load_rules()  # parse the rule store once, reuse for many files
for f in validate(Path("entry.cif"), rules=rules, context=Context({"xray"}, set(), {"PDB"})):
    print(f.severity, f.item, f.line, f.row, f.rule_id, f.message)
```

To run on a block already in memory, build a `CrossChecker` directly:

```python
from gemmi import cif
from onedep_lib.checks.crosscheck import Context, CrossChecker, load_rules

block = cif.read("entry.cif")[0]
findings = CrossChecker(block, load_rules(), Context.empty()).run()
```

## Behaviour notes

- Only the first data block is checked.
- Unquoted `?` and `.` are missing values (`gemmi.cif.is_null`); quoted `'?'` is a value. `EMPTY` / `NOT_EMPTY` treat a missing or blank value as empty.
- `EQ`, `NE`, `IN` and `NOT_IN` compare the stripped value, case-sensitively.
- Item names in the input file are matched case-insensitively.
- `MATCHES` / `NOT_MATCHES` join a text field wrapped over several lines before matching, so a run of alanines wrapped across lines of `_entity_poly.pdbx_seq_one_letter_code` is still found.
- An `auth_*` item in `group_by` falls back to its `label_*` counterpart when it has no value.
- gemmi rejects files with syntax errors (unterminated text fields, wrong number of values in a loop, and duplicate tags unless `--lenient`). These are reported as unreadable (exit status 2) rather than checked.
