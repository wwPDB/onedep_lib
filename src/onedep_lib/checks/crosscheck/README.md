# crosscheck

Runs the deposition rules defined in
`onedep_lib/schemas/cif/cross_checks_deposition_rules.cif` on mmCIF files.
[gemmi](https://gemmi.readthedocs.io/) parses the rule store, both
dictionaries and the input file, so this package has no CIF parser of its own.
Dictionary validation of the input file (types, enumerations, mandatory items,
foreign keys) is not done here.

Three schema files in `onedep_lib/schemas/cif/` are used:

| File | Role |
|---|---|
| `cross_checks_deposition_rules.cif` | The rules. |
| `mmcif_pdbx_deposition_rules_ext.dic` | Defines the rule store's categories and how rules are evaluated. The store is checked against it when loaded. |
| `mmcif_pdbx_v50.dic` | The PDBx/mmCIF dictionary: types and enumerations of the items the rules name. |

| Code | Purpose |
|---|---|
| `dictionary.py` | Reads a DDL2 dictionary with `gemmi.cif`: categories and keys, item types, mandatory codes, defaults, enumerations (with details) and links. Converts values to numbers, dates or text by item type. |
| `rules.py` | Reads the rule store into a `RuleSet` of `Rule`s, each with its `Condition`s, parameters, `Join`s and `RowSelection`s, and checks it against both dictionaries. |
| `checks.py` | `Category` wraps a gemmi mmCIF table (`Block.find_mmcif_category`) and reads values column by column; `CrossChecker` evaluates each enabled rule; `validate()` is the entry point. |
| `__main__.py` | Command line interface. |

## The rule store

The store has five categories, all defined in the extension dictionary:

- `_pdbx_deposition_rule`: id, family, severity (`ERROR`, `WARNING`, `INFO`),
  scope category, message (with `[{placeholder}]` fields), `enabled`,
  `procedural_method`, and flags saying which of the other records it has.
- `_pdbx_deposition_rule_condition`: the WHEN, THEN and UNLESS conditions, each
  with an operator, a value or `compare_item_name`, a `group_operator`, a
  `quantifier` and a `value_transform`.
- `_pdbx_deposition_rule_join`: how scope rows pair with partner rows, and
  `on_no_match` (`SKIP`, `FAIL`, `SINGLE_ROW_FALLBACK`).
- `_pdbx_deposition_rule_row_selection`: restricts a category to its MIN or MAX
  row by an item, per `group_by` group, or to the rows that have a value outside
  `exclude_items` (`ANY_VALUE`).
- `_pdbx_deposition_rule_param`: name/value settings.

### Checks when loading

`load_rules()` collects every problem and raises them together as a
`RulesError`:

- **Against the extension dictionary:** every column is defined there,
  mandatory items have a value, enumerated items use an enumerated value
  (case-insensitively for `ucode`), category keys are unique and every
  `rule_id` names a rule. Defaults fill unset values.
- **Against the constraints the extension dictionary describes:**
  - every non-procedural rule has a THEN condition;
  - one group operator per role, and at most one partner category per condition;
  - a compare item is in the scope category only if `item_name` is too, and an
    `IN_ENUMERATION_DETAILS_OF` pair is in one category;
  - operators have the value or compare item they need, patterns compile, and
    `MATCHES_TYPE` names item types defined in either dictionary;
  - ordering operators compare numeric or date items, and `ABS` applies to numeric items;
  - joins and row selections refer to the right categories, with one join per partner
    category; `MIN` / `MAX` need a numeric or date `selector_item`;
  - `param_flag`, `join_flag` and `row_selection_flag` match the records present.

  A parameter name the extension dictionary does not describe is a warning.
- **Against the PDBx/mmCIF dictionary:** every category the rules name must
  exist. An item missing from it is only a warning (`RuleSet.warnings`), since
  dictionary versions differ.

### How a rule is evaluated

As the extension dictionary describes, separately for each row of the rule's
scope category:

1. **Row selection:** each category the rule refers to is restricted to its
   selected rows.
2. **Pairing:** each scope row is paired with rows of each partner category,
   through the rule's join, or with every row when it has none. A scope row
   with no match is skipped, reported (`FAIL`), or paired by the single-row
   fallback.
3. **Conditions:** the rule applies when its WHEN conditions hold and its
   UNLESS conditions do not, and fails when its THEN conditions do not hold.
   - Conditions with one role are ANDed, or ORed for an OR group.
   - A condition on a partner category is evaluated over the paired rows with
     its quantifier: by default ANY for WHEN and UNLESS, and ALL for THEN.
   - THEN items may be in a partner category too.

Each failing scope row is reported once, against its first failing THEN item:
in the scope row, or in the partner row it failed on.

Families with special handling:

| Family | Check |
|---|---|
| `uniqueness` | The THEN items marked `UNIQUE` form a key that may not repeat among the rows the rule applies to. |
| `aggregate` | The THEN item is aggregated (`aggregate` = `SUM` or `COUNT`) over the `group_by` groups, and the THEN condition is tested on each group's result. Every row of a failing group is reported. |
| `procedural` | A built-in check named by `procedural_method`. The only one is `seq_model_mismatch` (`source_category`, `target_category`, `missing_message`): modelled residues in `_atom_site` against `_entity_poly_seq`. |

The other families (`pairwise_order`, `conditional_presence`,
`conditional_value`, `format`, `cross_reference`) are evaluated from their
conditions alone.

A rule with an `experiment_code` parameter (e.g. `PDB`) applies only when that
accession code type is requested (`--code`, or `Context(requested_codes)`).
Everything else that decides whether a rule applies is in its conditions, for
example `WHEN _exptl.method IN ...` for rules that apply to some experiments.

An unknown family or procedural method raises `RulesError` rather than being
skipped.

### Values and types

- **Missing values:** a value is missing when its item or category is absent,
  when it is empty, `?` or `.` once stripped (quoted or not), or when it cannot
  be read as its type.
- **Types:** item types from the PDBx/mmCIF dictionary make values numbers
  (standard uncertainties such as `1.23(4)` dropped), dates (`yyyy-mm-dd`,
  optionally with `:hh:mm`) or text.
  - Two items are compared as numbers if either is numeric, and as dates if
    either is a date.
  - Text of `uchar` types (`ucode`, `uline`, ...) compares case-insensitively.
- **Skipped comparisons:** a comparison with a missing value is skipped. A
  skipped WHEN or UNLESS condition does not hold, and a skipped THEN condition
  is satisfied. Partner rows with a missing value are ignored.
- **Existence operators:** `EMPTY` / `NOT_EMPTY` test whether the value is
  missing; `EXISTS` / `NOT_EXISTS` test whether the item is in the file, whatever
  its value. Over partner rows they test every paired row; over no partner rows,
  ANY does not hold and ALL holds.
- **`IN_COLUMN_OF`:** the value must occur among the compare item's values
  (after row selection). When that item has no values at all, the condition is
  skipped.
- **`IN_ENUMERATION_DETAILS_OF`:** the value of `compare_item_name` in the same
  row selects its enumeration entry in the PDBx/mmCIF dictionary
  (`_item_enumeration` or `_pdbx_item_enumeration`); the entry's comma-separated
  detail lists the values allowed for `item_name`. A compare value that is not
  enumerated, or has no detail, is skipped.
- **`MATCHES_TYPE`:** the value must match, in full, the construct of at least
  one of the listed item types, from the extension dictionary (e.g.
  `pdb_id_dep`, `deposition_id`) or the PDBx/mmCIF dictionary.
- **Patterns:** `MATCHES` / `NOT_MATCHES` search the value for the regular
  expression (anchor it with `^...$` to match the whole value). A text field
  wrapped over several lines is joined first, so a run of alanines wrapped
  across lines of `_entity_poly.pdbx_seq_one_letter_code` is still found.
- **Lists:** `IN`, `NOT_IN` and `MATCHES_TYPE` take comma-separated values; for
  other operators a comma is part of the value.
- **`group_by`:** an `auth_*` item in an aggregate's `group_by` falls back to
  its `label_*` counterpart when it has no value.

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
WARNING: _refine_ls_shell.R_factor_R_work (line 4509) [R009]
  Shell R_work is higher than shell R_free 0.000. (value=2.166, condition > _refine_ls_shell.R_factor_R_free)
ERROR: _em_software.version (line 8100, row 10) [R076]
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
| `--code CODE` | Requested accession code type for rules with an `experiment_code` parameter (e.g. `PDB`). Repeatable. |
| `--lenient` | Accept duplicate tags (last value wins) instead of rejecting the file. Not available for `.gz` files. |
| `--rules FILE` | Use another rule store instead of the packaged `cross_checks_deposition_rules.cif`. |
| `--dict FILE` | Use another PDBx/mmCIF dictionary instead of the packaged `mmcif_pdbx_v50.dic`. |
| `--extension FILE` | Use another extension dictionary instead of the packaged `mmcif_pdbx_deposition_rules_ext.dic`. |
| `--rule-warnings` | Print rule-store warnings (e.g. items missing from the PDBx/mmCIF dictionary) to stderr. |

Without `--code`, `experiment_code` rules are skipped.

Example:

```bash
# PDB code requested, JSON output
python -m onedep_lib.checks.crosscheck --code PDB --json entry.cif
```

## Use from Python

```python
from pathlib import Path
from onedep_lib.checks.crosscheck import Context, load_rules, validate

rules = load_rules()  # read and check the rule store and dictionaries once, reuse for many files
for f in validate(Path("entry.cif"), rules=rules, context=Context({"PDB"})):
    print(f.severity, f.item, f.line, f.row, f.rule_id, f.message)
```

To run on a block already in memory, build a `CrossChecker` directly:

```python
from gemmi import cif
from onedep_lib.checks.crosscheck import Context, CrossChecker, load_rules

block = cif.read("entry.cif")[0]
findings = CrossChecker(block, load_rules(), Context.empty()).run()
```

`load_rules()` returns a `RuleSet`: iterate it for the rules, use
`rules.get("R056")` for one rule, `rules.subset("R056", "R062")` to run a few,
and `rules.dictionary`, `rules.extension` and `rules.warnings` for the two
dictionaries and the warnings.

## Behaviour notes

- Only the first data block is checked.
- Item names in the input file are matched case-insensitively.
- gemmi rejects files with syntax errors (unterminated text fields, wrong number of values in a loop, and duplicate tags unless `--lenient`). These are reported as unreadable (exit status 2) rather than checked.
- The dictionaries are cached per path, so loading rules again is cheap.
