# crosscheck

Runs the cross-checks defined in `onedep_lib/schemas/cif/cross_checks.cif` on mmCIF files.
[gemmi](https://gemmi.readthedocs.io/) parses both the rule store and the input
file, so this package has no CIF parser of its own. Dictionary validation
(types, enumerations, mandatory items, foreign keys) is not done here.

| File | Purpose |
|---|---|
| `rules.py` | Reads `cross_checks.cif` with `gemmi.cif` into one dict per rule, with child rows (keys, conditions, branches, formats…) attached and ordered by `ordinal`. |
| `checks.py` | `Category` wraps a gemmi mmCIF table (`Block.find_mmcif_category`) and reads values column by column; `CrossChecker` runs every rule family; `validate()` is the entry point. |
| `__main__.py` | Command line interface. |

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

Each finding names the item, its location, and the id of the rule in
`cross_checks.cif` that produced it. gemmi keeps line numbers per item, not per
value: for a key-value item the line is that item's line; for a loop it is the
line of the loop's `loop_` and `row` is the 1-based row in the loop.

Exit status: `0` no errors (warnings allowed), `1` at least one error,
`2` a file or the rule store could not be read.

### Options

| Option | Effect |
|---|---|
| `--json` | Print findings as JSON (`severity`, `item`, `message`, `rule_id`, `line`, `row`). |
| `--errors-only` | Drop warnings. |
| `--dict FILE` | PDBx/mmCIF dictionary; enables the `_validator_rule_dictionary_enum` rules, which pair enumerations through `_item_enumeration.detail`. Skipped without it. |
| `--expt MODE` | Experiment mode for `_validator_rule_cross_reference` rules gated on `_validator_rule_cross_reference_expt` (`xray`, `nd`, `fd`, `ec`, `em`, `nmr`, `ssnmr`). Repeatable. |
| `--subtype NAME` | Entry subtype for `makeMandatorySubtypes` rules (`EC`, `EM-helical`, `EM-single_part`, `EM-subtomogram`). Repeatable. |
| `--code CODE` | Requested accession code for rules with `expt` = `coded` (e.g. `PDB`). Repeatable. |
| `--detect-context` | Also derive experiment modes from `_exptl.method` and EM subtypes from `_em_experiment.reconstruction_method`. |
| `--lenient` | Accept duplicate tags (last value wins) instead of rejecting the file. Not available for `.gz` files. |
| `--rules FILE` | Use another rule store instead of the packaged `schemas/cif/cross_checks.cif`. |

Without `--expt`, `--subtype`, `--code` or `--detect-context`, gated rules are
skipped, which matches the existing validator (it never supplies that context).

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

rules = load_rules()  # parse cross_checks.cif once, reuse for many files
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

- Only the first data block is checked, as in the existing validator.
- Unquoted `?` and `.` are missing values (`gemmi.cif.is_null`); quoted `'?'` is a value.
- Item names are matched case-insensitively.
- gemmi rejects files with syntax errors (unterminated text fields, wrong number of values in a loop, and duplicate tags unless `--lenient`). These are reported as unreadable (exit status 2) rather than checked.
- The poly-ALA sequence checks ignore whitespace in `_entity_poly.pdbx_seq_one_letter_code`, so a run of alanines wrapped across lines is still found.

## Origin and tests

Copied from the `gemmi_crosscheck` package in
[mmcif-validator](https://github.com/PDBeurope/mmcif-validator), where a parity
test checks it against that repository's own rule engine. The tests here
(`tests/checks/test_crosscheck.py`) cover the packaged rule store and a
small set of inline mmCIF blocks.
