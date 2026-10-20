# Gemmi validation integration: verified APIs and limits

Research date: 2026-10-12. This note supplies primary-source evidence and executable observations for the proposed `onedep_lib` validation engine. It distinguishes backend facts from recommendations. It does not specify the cross-check rule language; Gemmi's DDL validator and the repository's `cross_checks.cif` interpreter are separate check providers.

## Versions and evidence

The system `python` imports **Gemmi 0.7.4**, from `/home/wbueno/.local/lib/python3.12/site-packages/gemmi/`. The target `/home/wbueno/repos/onedep/onedep_lib/.venv/bin/python` currently raises `ModuleNotFoundError` for Gemmi. These are observations of the environments inspected, not dependency requirements for the finished engine.

Authoritative source checkouts inspected:

| Version | Commit | Local research checkout |
| --- | --- | --- |
| 0.7.4 | `31d7fe7322c74fbbe357f2d0b81d393ff83b1d79` | `/tmp/mmcif-validator-gemmi-research` |
| 0.7.5 | `5cc1c23c6007e0e6cbd69289c6f7c0bff50e943e` | `/tmp/mmcif-validator-gemmi-075-research` |

The installed-version probes below ran against 0.7.4. Source comparisons establish that the important Boolean and integer limitations described below persist in 0.7.5. No assertion is made about an uninspected future release. The older generated Python API documentation lists `read_ddl_file() -> str` and `validate_cif() -> str`; it must not determine this implementation's interface. [Older generated API](https://project-gemmi.github.io/python-api/gemmi.cif.Ddl.html), [release history](https://github.com/project-gemmi/gemmi/releases).

## Actual dictionary API

Verified 0.7.4 signatures, also represented in the 0.7.5 binding:

```python
gemmi.cif.Ddl(
    logger,                       # required Python argument
    print_unknown_tags=True,
    use_regex=True,
    use_context=True,
    use_linked_groups=True,
    use_mandatory=True,
    use_unique_keys=True,
)
ddl.read_ddl(doc: gemmi.cif.Document) -> None
ddl.validate_cif(doc: gemmi.cif.Document) -> bool
ddl.set_logger(logger) -> None
ddl.use_deposition_checks: bool
```

`read_ddl()` **moves the dictionary document into the validator and empties the supplied object**. Subsequent calls add dictionaries/extensions. Python exposes document validation, not C++ `validate_block()`. The misleading Python constructor name `use_linked_groups` sets C++ `use_parents`, enabling both parent mechanisms. Python defaults context and parent checking to true, although C++ defaults both to false. Set every option explicitly, and set deposition mode before loading dictionaries. [Python bindings, 0.7.5](https://github.com/project-gemmi/gemmi/blob/v0.7.5/python/cif.cpp#L441-L466), [DDL declaration, 0.7.5](https://github.com/project-gemmi/gemmi/blob/v0.7.5/include/gemmi/ddl.hpp).

Recommended initialization for a deliberately selected policy:

```python
events = []
ddl = gemmi.cif.Ddl(
    logger=(events.append, 6),
    print_unknown_tags=True,
    use_regex=True,
    use_context=False,
    use_linked_groups=False,
    use_mandatory=True,
    use_unique_keys=True,
)
ddl.use_deposition_checks = False
for dictionary_path in configured_dictionary_paths:
    ddl.read_ddl(gemmi.cif.read_file(str(dictionary_path), check_level=2))
```

Those false options are an example standard policy, not a recommendation to ignore a caller's explicit deposition/context/parent requirements. Capture dictionary-load events independently of validation events. Cache prepared backends by Gemmi version, ordered dictionary content hashes, and the complete option fingerprint. A cache must own the moved documents indirectly through `Ddl`; it must not rely on the original document remaining usable.

## Diagnostics and Boolean results are different contracts

Gemmi sends **a string to each callback invocation**, without a trailing newline. A stream logger adds a newline. A callback does not receive severity, rule identifiers, row indices, tags, or a structured diagnostic object. The threshold filters internal message levels before callback execution; it does not convey the level. Preserve multiline messages as one event. `(None, 0)` suppresses messages. General Logger errors can throw when no callback is installed; an exception is therefore a distinct outcome from a negative validation Boolean. [Logger documentation](https://gemmi.readthedocs.io/en/stable/misc.html#logger), [Logger implementation](https://github.com/project-gemmi/gemmi/blob/v0.7.5/include/gemmi/logger.hpp), [Python Logger conversion](https://github.com/project-gemmi/gemmi/blob/v0.7.5/python/common.h).

The installed backend produced these exact observations for a tiny dictionary with `_cat.id` typed `int`, `_cat.required` typed `float`, both mandatory, and `_cat.id` the category key:

| Input defect | Backend Boolean | Captured events |
| --- | --- | --- |
| Missing `_cat.required` | `True` | `[x] missing mandatory tag: _cat.required` |
| Repeated `_cat.id` key | `True` | `[x] category cat has 1 duplicated key:` plus an example on the next line |
| Unknown `_cat.unknown` | `True` | `[x] unknown tag _cat.unknown` |
| Integer value `not_an_integer` | `True` | None |
| Float value `not_a_number` | `False` | `string:3 [x] expected number, got: not_a_number` |
| Three invalid float rows in one loop column | `False` | One event for the first bad value only |
| Missing parent with lowercase `_cat.id` | `True` | A missing-parent diagnostic |
| Identical missing parent with uppercase `_CAT.ID` | `True` | None |

The source sets the Boolean to false for item value/context failures. Mandatory, key, and parent checks emit warnings without changing it. It stops checking a loop column after its first invalid value. Dictionary-load regex failures can substitute an always-matching regex. A failing nested save-frame validation is not propagated through the outer Boolean. [DDL implementation, 0.7.4](https://github.com/project-gemmi/gemmi/blob/v0.7.4/src/ddl.cpp#L572-L660), [DDL implementation, 0.7.5](https://github.com/project-gemmi/gemmi/blob/v0.7.5/src/ddl.cpp#L572-L660).

**Important correctness defect:** in both inspected versions, the invalid-integer branch sets an error-message string but omits `return false`. An unrestricted `int` item with no range or enumeration therefore accepts non-integer text silently. The probe confirms this in 0.7.4. [Integer branch, 0.7.5](https://github.com/project-gemmi/gemmi/blob/v0.7.5/src/ddl.cpp#L190-L196).

**Parent tag-case defect:** the inspected parent checker collects the original spelling of input tags into its `present` set, then uses exact-case dictionary relation keys. A 0.7.4 probe enabled parent checks and declared `_cat.id` linked to missing `_parent.id`: lowercase `_cat.id` emits a missing-parent diagnostic; uppercase `_CAT.ID` emits none. The same logic remains in 0.7.5. Test case invariance for both ordinary and composite links. A profile promising parent coverage must address this with a proven patched backend or narrow dictionary-driven supplement. [Parent implementation](https://github.com/project-gemmi/gemmi/blob/v0.7.5/src/ddl.cpp#L414-L451).

For the integer gap specifically, derive the **effective item type** from its save-frame `_item_type.code`. Only when deposition mode is enabled and `_pdbx_item_type.code` exists does that alternate replace it. Gemmi's problematic shortcut is selected for exact decoded code `int`, not every type whose `_item_type_list.primitive_code` is `numb`. Other codes use their declared regex; do not infer inherited integer semantics from arbitrary regexes. For effective `int`, test non-null **raw tokens** with full-match ASCII `[+-]?[0-9]+`, without stripping or unquoting; this matches the backend's intended lexical check and avoids integer coercion limits. Separate derived-type supplements require separately demonstrated gaps.

Implementation requirements inferred from these observations:

1. Treat `backend_ok` as backend evidence, not the engine's authoritative `dictionary_passed` field. Compute the latter from the Boolean, normalized blocking diagnostics, supplements, and backend/configuration failures.
2. Introduce a versioned diagnostic adapter. Its stable fallback contains `check_type="dictionary"`, a generic code such as `dictionary.backend_diagnostic`, `backend="gemmi"`, exact raw message, dictionary identities, and nullable location fields. Parse recognized formats conservatively; an unrecognized event remains reportable and cannot become an invented row or rule ID.
3. Define an explicit severity policy for unknown tags, missing mandatory tags/keys, duplicates, missing parents, and context findings. Do not infer that all string events are warnings simply because Gemmi names the helper `warn`.
4. Preserve backend-load diagnostics. A malformed or unsupported regex is a dictionary preparation failure or reported coverage degradation according to policy; it must not silently advertise complete enforcement.
5. Require a startup/CI capability fixture for invalid integers. Use a backend version proven to reject them, a narrowly maintained patch, or a **dictionary-driven integer supplement**. The supplement reads effective `int` types from the configured dictionaries, including deposition overrides, handles only that demonstrated gap, and reports dictionary findings. Do not hardcode tag names.
6. Make diagnostic coverage explicit: Gemmi does not enumerate every erroneous cell. If exhaustive dictionary-cell reporting is required, additional provider work is needed; the default contract must promise the findings emitted by the configured providers.
7. Track blocking-event state even after the report's storage limit is reached. Truncation must never turn a failing dictionary stage into a passing one.

Recommended initial severity mapping (an engine policy, not severity information returned by Gemmi):

| Recognized event family | Initial severity and behavior |
| --- | --- |
| Invalid type, enum, range, or enabled item-context constraint | Error; block cross-checks |
| Missing mandatory tag or category key | Error; block cross-checks |
| Duplicated category key | Error; block cross-checks |
| Missing parent/tag/group, when parent checking is explicitly enabled | Error; block cross-checks |
| Unknown tag | Error by default; caller may select warning policy, with incomplete dictionary coverage recorded |
| `Bad DDL2:`, malformed regex, undefined type, inconsistent dictionary | Configuration error; record dictionary coverage failure, block cross-checks |
| Category-context informational message (`category indicated as ...`) | Info; does not independently block |
| Unknown message format | Preserve raw event; error by conservative default until the adapter recognizes its meaning |

Use threshold 6 during both dictionary preparation and validation, because some coverage failures are emitted as ordinary messages. Threshold 3 would silently drop them. Recognition tests must target the pinned backend's actual formats; these patterns are not a stable Gemmi diagnostic schema. Any false backend Boolean with no recognized error produces an additional generic blocking `dictionary.backend_failed` event.

Pass callbacks through a bounded collector rather than the example's unbounded `events` list in production. Maintain independent `total_event_count`, `blocking_event_seen`, retained-event count, and omitted count. On each event, first classify and update the authoritative stage state, then decide whether to retain it. Set finite retained-issue and message-byte limits; preserve truncation metadata. The callback should not write or flush application logs per failing cell. Send only aggregate operational metrics to the application logger unless detailed diagnostics are requested.

The logger callback has no supported early-stop return value. Once retention is full, return normally after classifying/counting and let the native dictionary call finish. Raising a callback exception interrupts the call and cannot yield a completed dictionary result; an intentionally aborted call must be marked incomplete. Cancellation can be checked before and after a native call, but a long valid input may produce no callbacks, so the engine cannot guarantee cancellation polling during that call. A hard timeout requires process isolation rather than a promised per-row callback interval.

## Parsing and column access

`cif.read_file(path, check_level=2)` and `cif.read_string(data, check_level=2)` return a document; use this object for both stages. Level 1 rejects missing pair values and duplicates; level 2 additionally rejects empty loops and empty block names. Gemmi supports CIF 1.1 and DDL2; CIF2/DDLm are unsupported. The standard PDBx dictionary permits categories to be absent and mandatory item values to be null; such domain completeness requirements belong in cross-check rules. Parent checking has known schema mismatches, and deposition checks select alternate `_pdbx_item_*` criteria. [CIF parsing and DDL2 notes](https://gemmi.readthedocs.io/en/stable/cif.html).

Probe of a loop with values `?`, `"?"`, `.`, `"."`, and `""`:

```text
raw value   is_null(raw)   as_string(raw)
?           True          ''
"?"         False         '?'
.           True          ''
"."         False         '.'
""          False         ''
```

Classify nullness on the **raw token before unquoting**. Otherwise quoted literal `?`/`.` and a quoted empty string collapse into missing values. Preserve separate states for absent tag, unknown null, not-applicable null, and present text. `find_values(tag)` gives a `Column` over either a pair or loop, but searching for a tag scans items. `get_mmcif_category()` creates Python lists; its default conversion maps `?` to `None` and `.` to `False`. `loop.values` likewise copies all values. Tags are matched case-insensitively. [DOM source](https://github.com/project-gemmi/gemmi/blob/v0.7.5/include/gemmi/cifdoc.hpp#L71-L83), [column search](https://github.com/project-gemmi/gemmi/blob/v0.7.5/include/gemmi/cifdoc.hpp#L850-L869), [category Python binding](https://github.com/project-gemmi/gemmi/blob/v0.7.5/python/cif.cpp#L283-L308).

The engine should build a block-local tag/category index once. Retain lightweight Gemmi item/loop descriptors, with tag-to-column offsets, and access only the rule's projected columns. Do not materialize `_atom_site` into lists of row dictionaries just to inspect a handful of fields. Cache a decoded or numeric column only when a query plan actually reuses it, under a memory budget.

Do not assume a category is always a loop: single-row categories may be pairs. Do not assume an entire category occupies the first matching loop either: inventory physical items and reject or explicitly handle categories split across incompatible loops/pairs. `find_mmcif_category()` is a convenient view for normal layout, not an automatic relational merge. Ambiguous row alignment must yield a structural diagnostic, not an invented zipped table.

## Locations, ownership, and concurrency

Gemmi retains `Item.line_number`: a pair's item start, or a loop's `loop_` start. A loop's individual values do not carry original line/column offsets. `Table.Row.row_index` is a logical row index, not a source line. This was verified by a five-row multiline input whose sole loop item reports line 2. Report exact logical row indices and key values, with `location_precision="item"` for a loop start; leave per-cell physical positions null. The engine cannot obtain exact cell locations by serializing the document, since original whitespace is not preserved. [Item/loop representation](https://github.com/project-gemmi/gemmi/blob/v0.7.5/include/gemmi/cifdoc.hpp), [Python Item and Row bindings](https://github.com/project-gemmi/gemmi/blob/v0.7.5/python/cif.cpp).

Recommendations:

- Validation owns or borrows a document **without mutating it**. The caller must not modify a borrowed document concurrently or during validation. Keep the document alive for the lifetime of all retained Gemmi views.
- Define whether the public API accepts multiple blocks. The provider's Python API validates a whole document. A simple initial policy gates cross-checks for the entire document after any blocking dictionary failure; finer per-block gates require reliable block attribution or a different backend bridge. `Document.add_copied_block()` is a copy, so repeatedly constructing per-block documents increases memory.
- Do not treat a cached `Ddl` with a replaceable callback as concurrently reusable. Give each worker an owned validator/logger or serialize access. A shared immutable rule plan is different from a shared mutable prepared backend.
- The inspected Python read and DDL bindings have no `gil_scoped_release`/release call guard. Do not promise CPU scaling from threads. Start sequentially; if profiling justifies parallel independent-file validation, use worker processes and prepare dictionaries once per process. [Read bindings](https://github.com/project-gemmi/gemmi/blob/v0.7.5/python/read.cpp), [DDL bindings](https://github.com/project-gemmi/gemmi/blob/v0.7.5/python/cif.cpp).
- Pin/test the supported Gemmi version range. Process transport should not assume Gemmi's private binary serialization is stable across releases. Use owned documents in-process; budget deliberate copies and worker memory.

## Reproducing the dictionary probe

This is a diagnostic fixture, not engine implementation code. It exercises the backend behavior that the integration's acceptance tests must cover.

```python
import gemmi

dictionary_text = """data_minimal
_dictionary.title minimal
_dictionary.version 1
save_cat
_category.id cat
_category_key.name '_cat.id'
save_
save_cat_id
_item.name '_cat.id'
_item.mandatory_code yes
_item_type.code int
save_
save_cat_required
_item.name '_cat.required'
_item.mandatory_code yes
_item_type.code float
save_
"""
cases = {
    "missing_mandatory": "data_x\n_cat.id 1\n",
    "duplicate_keys": "data_x\nloop_\n_cat.id\n_cat.required\n1 1.0\n1 2.0\n",
    "unknown_tag": "data_x\n_cat.id 1\n_cat.required 1.0\n_cat.unknown hi\n",
    "invalid_int": "data_x\n_cat.id not_an_integer\n_cat.required 1.0\n",
    "invalid_float": "data_x\n_cat.id 1\n_cat.required not_a_number\n",
    "multiple_invalid_rows": "data_x\nloop_\n_cat.id\n_cat.required\n1 A\n2 B\n3 C\n",
}
events = []
ddl = gemmi.cif.Ddl(logger=(events.append, 6), use_context=False,
                    use_linked_groups=False)
dictionary = gemmi.cif.read_string(dictionary_text)
ddl.read_ddl(dictionary)
assert len(dictionary) == 0
for name, text in cases.items():
    events.clear()
    backend_ok = ddl.validate_cif(gemmi.cif.read_string(text))
    print(gemmi.__version__, name, backend_ok, events)
```

The final integration tests should cover these cases through the engine API and assert its authoritative stage result, structured finding context, preserved raw backend diagnostic, dictionary identity, and dictionary-before-cross-check gate. A provider test that only asserts `validate_cif()` returns a Boolean cannot establish dictionary correctness.
