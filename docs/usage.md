# Usage

## Configuration

`DepositConfig.load()` resolves settings from three sources, in order of increasing priority:

1. `~/.config/onedep/config.toml` (lowest, persistent defaults)
2. Environment variables (override file values)
3. Keyword arguments passed to `DepositConfig.load()` (highest priority)

After resolving the above, `load()` also reads the `[auths.<fqdn>]` section matching the resolved hostname and populates the `access_token` and `refresh_token` fields automatically. See [Authentication Flow](auth_flow.md) and `TokenStore` in the [API Reference](api.md) for how those tokens get there in the first place.

The default values are:

| Field | Default | Source |
|---|---|---|
| `access_token` | `None` | `[auths.<fqdn>]` / override |
| `refresh_token` | `None` | `[auths.<fqdn>]` / override |
| `hostname` | `https://deposit.wwpdb.org/deposition` | `[default]` / env / override |
| `ssl_verify` | `true` | `[default]` / env / override |
| `redirect` | `true` | `[default]` / env / override |
| `schema_base_url` | `https://schemas.wwpdb.org/nextdep` | `[default]` / env / override |
| `schema_cache_dir` | `~/.onedep/schemas` | `[default]` / override |
| `session_dir` | `~/.onedep/sessions` | `[default]` / override |
| `config_path` | `~/.config/onedep/config.toml` | override only |

### Config file

The config file has two distinct sections:

- **`[default]`** — general settings (hostname, SSL, schema URLs, etc.)
- **`[auths.<fqdn>]`** — auth tokens for a specific host, keyed by hostname FQDN

The FQDN key is derived from the hostname by stripping the URL scheme, port, and path, and replacing `.` and `-` with `_`. For example, `https://deposit.wwpdb.org/deposition` → `deposit_wwpdb_org`.

A complete config file looks like:

```toml
[default]
hostname = "https://deposit.wwpdb.org/deposition"
ssl_verify = true
redirect = true
schema_base_url = "https://schemas.wwpdb.org/nextdep"
schema_cache_dir = "/home/you/.onedep/schemas"
session_dir = "/home/you/.onedep/sessions"

[auths.deposit_wwpdb_org]
access_token = "eyJ..."
refresh_token = "opaque-string"
```

`access_token` and `refresh_token` are never read from `[default]`; they always come from the per-host `[auths]` table, written by `TokenStore.store_tokens()` (see [Authentication Flow](auth_flow.md)). Multiple hosts can coexist in the same file under separate `[auths.<fqdn>]` tables without interfering with each other.

If no `[auths.<fqdn>]` entry matches the resolved hostname exactly (for example, tokens were registered while authenticated against a different, e.g. country-specific, site), `TokenStore.get_access_token()` falls back to the most recently registered entry under a different key and exchanges its refresh token for one scoped to the resolved hostname via `/auth/tokens/exchange`. The result is then stored under the resolved hostname's own key, alongside the original entry.

Unknown keys in `[default]` are ignored. An empty `hostname` is ignored so the default remains in effect. Invalid TOML raises `ConfigError`.

Load configuration with no arguments once the file is in place:

```python
from onedep_lib.config import DepositConfig

config = DepositConfig.load()
print(config.access_token)  # populated from [auths.deposit_wwpdb_org] if present
```

### Environment variables

| Variable | Field | Type |
|---|---|---|
| `ONEDEP_ACCESS_TOKEN` | `access_token` | str |
| `ONEDEP_REFRESH_TOKEN` | `refresh_token` | str |
| `ONEDEP_HOSTNAME` | `hostname` | str |
| `ONEDEP_SSL_VERIFY` | `ssl_verify` | `true`, `false`, `1`, or `0` |
| `ONEDEP_REDIRECT` | `redirect` | `true`, `false`, `1`, or `0` |
| `ONEDEP_SCHEMA_URL` | `schema_base_url` | str |

Environment variables override config-file values. An empty `ONEDEP_HOSTNAME` is ignored so the default hostname remains in effect. Invalid boolean values raise `ConfigError`.

### Runtime overrides

Pass keyword arguments to `DepositConfig.load()` to override both the config file and environment:

```python
from pathlib import Path
from onedep_lib.config import DepositConfig

config = DepositConfig.load(
    ssl_verify=False,
    schema_cache_dir=Path("/tmp/onedep-schemas"),
    session_dir=Path("/tmp/onedep-sessions"),
)
```

To inject tokens directly (e.g. in tests or embedded applications), skipping `[auths.<fqdn>]` entirely:

```python
config = DepositConfig.load(
    access_token="eyJ...",
    refresh_token="opaque-string",
)
```

## The DSP deposition workflow

The DSP (Deposition Software Provider) API is the high-level interface for third-party suites to stage files locally, run pre-submission checks, and submit depositions to OneDep. It persists session state in a local JSON file so workflows can be interrupted and resumed.

### Start a new deposition

```python
import onedep_lib as dsp
from onedep_lib.config import DepositConfig

config = DepositConfig.load()

with dsp.deposit_init(
    email="depositor@example.org",
    users=["0000-0002-5109-8728"],  # ORCID IDs
    country=dsp.Country.USA,
    experiment_type=dsp.ExperimentType.XRAY,
    config=config,
) as dep:
    print(dep.session_id)  # save this to resume later

    dep.add_file("model.cif", dsp.FileType.MMCIF_COORD)
    dep.add_file("data-sf.cif", dsp.FileType.CRYSTAL_STRUC_FACTORS)

    report = dep.check_required_files()
    if not report.ok:
        for issue in report.errors():
            print(issue.message)

    dep_id = dep.deposit()  # non-blocking; triggers upload + process
    print(dep.get_status())
```

See [`examples/xray_deposition.py`](https://github.com/wwPDB/onedep_lib/blob/main/examples/xray_deposition.py) for a complete walkthrough including per-file checks. [`examples/em_deposition.py`](https://github.com/wwPDB/onedep_lib/blob/main/examples/em_deposition.py) shows the equivalent flow for EM depositions, including `set_em_params()` and `set_voxel_values()`.

### Resume an existing session

Sessions are identified by a UUID printed at creation time (`dep.session_id`). Pass it to `deposit_resume()` to reload the full session state — registered files and remote deposition ID included.

```python
dep = dsp.deposit_resume("your-session-uuid", config=config)

dep.add_file("extra.cif", dsp.FileType.CRYSTAL_STRUC_FACTORS)
dep.deposit()  # reuses the existing remote deposition if already submitted
```

When a deposition is assigned to a non-default OneDep site, the local session stores both `site_base_url` (the canonical site root used for later API calls and host-scoped token lookup) and `site_url` (the server-provided view URL, opaque display metadata).

### List all sessions

```python
import onedep_lib as dsp

for session, files in dsp.list_sessions():
    print(session.session_id, session.created_at, session.email)
    print("  experiment:", session.experiment_type)
    print("  remote dep:", session.remote_dep_id or "(not submitted)")
    for f in files:
        print("  file:", f.file_path, f.file_type)
```

Sessions where `remote_dep_id` is `None` have not been submitted yet. To inspect sessions stored in a non-default location, pass `base_dir`:

```python
from pathlib import Path

dsp.list_sessions(base_dir=Path("/custom/session/dir"))
```

## Error handling

API, authentication, schema and configuration exceptions inherit from `onedep_lib.exceptions.OneDepError`. Direct validation preparation raises `ValidationConfigurationError`, a `ValueError` subclass. See the [API Reference](api.md#exceptions) for the full hierarchy, or [Authentication Flow](auth_flow.md) for auth-specific error handling.


## Local mmCIF validation

Gemmi is installed as a normal dependency. Configure local dictionary paths to enable dictionary-backed validation; the library does not download dictionaries. Relative resource paths resolve against the directory containing `config_path`.

```toml
[default]
mmcif_dictionary_paths = ["/path/mmcif_pdbx.dic"]

[default.mmcif_rule_paths]
co-cif = "/path/coordinates.cif"
md-cif = "/path/metadata.cif"
xs-cif = "/path/structure_factors.cif"
```

When `co-cif` has no configured rule path, coordinate files use the packaged 149-rule bundle. Other unmapped mmCIF types receive dictionary checks and an explicit unavailable-cross-check-coverage finding. Their full validation result is incomplete. A custom file replaces that type's cross-check policy; bundles are not merged implicitly.

Both new and resumed depositions prepare engines once per distinct resource profile. An explicit full-file check returns its report and updates `metadata_report`:

```python
report = dep.check_mmcif_file(coordinate_file_id)
for issue in report.issues:
    print(issue.severity.value, issue.rule_id, issue.message)

dep_id = dep.deposit()
for file_id, report in dep.metadata_report.items():
    print(file_id, report.validation.complete, report.validation.valid)
    payload = report.to_dict()  # also supports report.to_json()
```

`deposit()` checks all registered coordinate, metadata-CIF, and structure-factor-CIF files before upload. It reparses current contents, replaces the automatic report collection, logs an issue summary, and still returns the deposition ID. Findings and unavailable validation resources do not prevent submission; independent API or upload failures retain their existing behavior. Reports remain available if a later API operation fails. Category and field presence checks do not replace a full-file report.

`report.ok` keeps its existing meaning: no ERROR/FATAL findings. Inspect `report.validation.complete` as well, because a skipped or partial check is not complete validation. `validation.valid` requires both completion and absence of ERROR/FATAL. Dictionary errors normally block cross-checks for that block, while submission remains advisory.

### Direct validation and prepared-engine injection

```python
from pathlib import Path
import gemmi
from onedep_lib.validation import MmCifValidator, ValidationContext, ValidationOptions

engine = MmCifValidator.load([Path("/path/mmcif_pdbx.dic")])
report = engine.validate_path(Path("model.cif"), source="coordinates")

document = gemmi.cif.read_file("model.cif")
report = engine.validate_document(document, source="coordinates")

# Explicit archive context; requested codes are never guessed from an entry ID.
report = engine.validate_path(
    Path("model.cif"),
    context=ValidationContext(requested_codes=("PDB",)),
)

# Small fixtures can request cross-only scope without a dictionary.
cross_only = MmCifValidator.load(
    [], options=ValidationOptions(phases=("cross",)),
)
```

A keyword-only `mmcif_validator=engine` on `deposit_init()` or `deposit_resume()` overrides the coordinate route. To override several routes, supply `{FileType.MMCIF_COORD: coordinate_engine, FileType.CRYSTAL_STRUC_FACTORS: reflection_engine}`. Config prepares the remaining routes. Each injected engine retains its own resource snapshot and provenance.

Resource preparation raises `ValidationConfigurationError` for malformed or incompatible resources. The deposition adapter converts such preparation failures to advisory unavailable-validation reports. Unexpected engine errors propagate through direct calls; the deposition adapter logs them and records an internal-error finding.

The supported dictionary profile is PDBx/DDL2 with canonical mmCIF tags. Historical alias declarations and shared child-item references are accepted in dictionaries. Actual use of an alias tag requires normalization and produces an incomplete-validation finding. Unresolved alternative simple parents are retained as resource warnings; populated affected items are blocked and marked incomplete. These limits do not reject otherwise usable canonical definitions.

### Selecting categories through the Python API

Pass `categories=` on either validation method while reusing the complete loaded
dictionary and rule bundle:

```python
report = engine.validate_path(
    Path("model.cif"),
    categories=["atom_site", "entity_poly"],
)
report = engine.validate_document(
    document,
    source="coordinates",
    categories=["atom_site"],
)

# Omitting categories, passing None, or passing [] runs every configured check.
report = engine.validate_path(Path("model.cif"))
```

Category names are case-insensitive; `_atom_site.` and `atom_site` select the
same category. Cross-check selection uses the rule's checked/source category,
available as `RuleDescriptor.check_category`. A target-only category does not
select rules owned by another source category. The atom/sequence mismatch check
belongs to `atom_site` and still reads `entity_poly_seq`.

Dictionary type, enumeration, mandatory and key checks apply to the requested
categories. Parent-link supplements validate selected child categories against
the original full document. Other categories remain available to selected rules
as lookup targets; their unrelated checks and findings are excluded. Input
syntax errors and structural errors without a category remain global.

Reports record `validation.requested_categories`. Out-of-scope rules have
`status="excluded"` and `skip_reasons.category_not_selected`, with no rows examined.
A complete/valid report describes the requested category scope rather than the
entire file. Unknown names produce `CIF_CATEGORY_SELECTION`; malformed selectors
raise `ValidationConfigurationError`. Preparation still compiles/verifies the
entire bundle according to the chosen resource policy.

Scoped dictionary execution lazily prepares a reusable Gemmi backend from the
engine's saved resource bytes. It validates an in-memory view of selected
categories, using the full document for supplemental parent joins. Input is
never rewritten or reparsed, and no per-call category state is retained.

### Limits, scope, and resource updates

Options are immutable. The default retains at most 10,000 substantive findings plus one terminal FATAL when the limit is exceeded. Context values and related locations have separate caps. Native Gemmi parsing/DDL calls cannot be interrupted by the Python cancellation callback; cancellation is observed at the next supported transition.

A prepared engine serializes concurrent validations because Gemmi DDL state and logger callbacks are mutable. Use separate engines per worker for parallel processing. Caller-owned documents must remain unchanged during validation, and the engine never mutates them.

Reload with `MmCifValidator.load(...)` after changing a resource. Existing engines keep their immutable content-digest snapshots. The package supplies additive schema-v2 messages and accession formats, plus a CIF defaults bridge for original schema-v1 bundles. Rule algorithms stay in code; messages, formats, branches and thresholds stay in CIF. See the [implementation specification](crosschecks/validation-engine-specification.md) for the resource schema and [Gemmi capability evidence](crosschecks/gemmi-validation-research.md) for backend limitations.


### Comparing validation engines from the CLI

Run both implementations on the same input:

```bash
uv run python scripts/validate_mmcif.py dictionary.dic input.cif \
  --rules src/onedep_lib/validation/data/cross_checks.cif -o comparison.json
```

The script prints preparation, validation and total seconds for each engine.
JSON reports are now under `engines.onedep` and `engines.legacy`, each containing
`status`, timings, `scope` and a `report`. Total time includes adapter setup,
resource preparation and validation, excluding JSON conversion/writing.
The new engine prepares the dictionary separately. The legacy `validate(dictionary,
mmcif)` call parses the dictionary inside its validation timing. Compare total
times and inspect scope/coverage when interpreting the results.

The legacy adapter uses an installed `validate_mmcif` module or the adjacent
`../mmcif-validator/vscode-extension/python-script` checkout. `--legacy-path`
can select a module file, Python-script directory or repository root. The loader
avoids this CLI's matching module name. Legacy runs its own built-in rules;
`--rules`, `--cross-only` and `--native` affect the new adapter only.

Use `--engines onedep` or `--engines legacy` to select one implementation.
By default, every factory in `ENGINE_FACTORIES` runs sequentially.
A third implementation needs one factory returning `PreparedEngine` and one
registry entry; timing, failures, console output and JSON use the shared runner:

```python
def prepare_third(args):
    from third_package import validate

    return PreparedEngine(
        lambda: validate(args.dictionary, args.mmcif),
        lambda errors: {"errors": errors},
        "third engine checks",
        "dictionary parsing, input parsing and validation",
    )

ENGINE_FACTORIES["third"] = prepare_third
```

One engine's setup, validation or serialization failure is recorded without
preventing the remaining engines from running. The CLI writes their records and
returns exit code 2 if an engine fails. Validation findings do not themselves
count as execution failures. Each engine retains its own report schema and
coverage; a completed call does not imply complete or valid validation.

The new adapter's `--cross-only` flag runs rules without dictionary input checks,
while retaining dictionary metadata needed by cross-checks. The CLI selects
`ValidationOptions(rule_dictionary_policy="block_rule")`, so incompatible rules
are explicitly unavailable and coverage is partial. Direct engine calls retain
the default `"error"` resource policy.

`--native` selects Gemmi dictionary checks in the new adapter. It cannot be
combined with `--rules` or `--cross-only`. Native reports retain preparation and
validation diagnostics and mark `dictionary_checks_complete` false for `Bad DDL`
messages. Dictionaries need an `_item_type_list` table for named-type checks;
use the [complete wwPDB ASCII download](https://mmcif.wwpdb.org/dictionaries/ascii/mmcif_pdbx_v50.dic).
