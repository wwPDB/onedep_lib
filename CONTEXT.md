# OneDep deposition and mmCIF validation

OneDep deposition prepares and submits experimental data. Local checks describe missing deposition inputs and inconsistencies in mmCIF data.

## Language

**Required-file check**:
A check that a deposition contains the file types required for its experiment method and subtype. It does not establish whether the contents of an mmCIF file are valid.
_Avoid_: mmCIF validation, dictionary check

**Dictionary check**:
A check of mmCIF data against the definitions and relationships in a supplied dictionary, including the declared PDBx validation profile.
_Avoid_: cross-check, required-file check

**Cross-check**:
A domain consistency check between mmCIF values, rows, or categories, defined by a versioned cross-check rule resource. A cross-check may use dictionary metadata without becoming a dictionary check.
_Avoid_: dictionary check, required-file check

**Cross-check rule bundle**:
A set of domain consistency rules with a shared identity and version. Different deposition file types can use different bundles.
_Avoid_: dictionary, required-file schema

**Validation finding**:
A reported violation or validation execution problem, with severity and available attribution to an mmCIF location and check definition. A finding does not itself prevent deposition submission.
_Avoid_: submission rejection

**Validation coverage**:
The record of which requested dictionary checks and cross-checks finished, were inapplicable, or could not run. Complete coverage does not mean the data are valid.

**Validation context**:
The experiment modes, entry subtypes, and requested archive codes used to determine which cross-check rules apply to a data block.

**Metadata report**:
The collection of local mmCIF validation reports for a deposition, with each report associated with its registered file. It describes validation findings and coverage rather than metadata completeness percentages.
_Avoid_: metadata completeness score

