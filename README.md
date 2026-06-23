# DSP Mock Package

![PyPI version](https://img.shields.io/pypi/v/onedep_lib.svg)

Prepares data to be deposited into OneDep system through the Deposition API. JSON schemas served remotelly will be used to check metadata in mmCIF files and also check if the local deposition/session has all required files for the chosen experiment type. Checks will be carried before deposition is created on the server and files are uploaded. 

## Configuration

`DepositConfig.load()` resolves settings from three sources in order of increasing priority:

1. `~/.config/onedep/config.toml` (lowest, persistent defaults)
2. Environment variables (override file values)
3. Keyword arguments passed to `DepositConfig.load()` (highest priority)

After resolving the above, `load()` also reads the `[auths.<fqdn>]` section matching the resolved hostname and populates the `access_token` and `refresh_token` fields automatically (see [Authentication](#authentication)).

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

- **`[default]`** — general settings (hostname, SSL, API key, etc.)
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

The `[auths.<fqdn>]` section is written by `TokenStore.store_tokens()` — see [Authentication](#authentication). `access_token` and `refresh_token` are never read from `[default]`; they always come from the per-host `[auths]` table. Multiple hosts can coexist in the same file under separate `[auths.<fqdn>]` tables without interfering with each other.

`DepositConfig.load()` reads `[default]` first, then reads the `[auths.<fqdn>]` section that matches the resolved hostname and populates `access_token` and `refresh_token` into the returned config object.

Unknown keys in `[default]` are ignored. An empty `hostname` is ignored so the default remains in effect. Invalid TOML raises `ConfigError`.

Load configuration with no arguments once the file is in place:

```python
from onedep_lib.config import DepositConfig

cfg = DepositConfig.load()
print(cfg.access_token)   # populated from [auths.deposit_wwpdb_org] if present
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

```bash
export ONEDEP_ACCESS_TOKEN="your.jwt.token"
export ONEDEP_REFRESH_TOKEN="opaque-token"
export ONEDEP_HOSTNAME="https://onedep-depui-test.wwpdb.org/deposition"
export ONEDEP_SSL_VERIFY="false"
export ONEDEP_SCHEMA_URL="http://localhost:8080/schemas"
```

Environment variables override config-file values. An empty `ONEDEP_HOSTNAME` is ignored so the default hostname remains in effect. Invalid boolean values raise `ConfigError`.

### Runtime overrides

Pass keyword arguments to `DepositConfig.load()` to override both the config file and environment:

```python
from pathlib import Path

from onedep_lib.config import DepositConfig

cfg = DepositConfig.load(
    api_key="your.jwt.token",
    ssl_verify=False,
    schema_cache_dir=Path("/tmp/onedep-schemas"),
    session_dir=Path("/tmp/onedep-sessions"),
)
```

To inject tokens directly (e.g. in tests or embedded applications):

```python
cfg = DepositConfig.load(
    access_token="eyJ...",
    refresh_token="opaque-string",
)
```

When either token is supplied as an override, `load()` skips reading the `[auths.<fqdn>]` section entirely.

## Authentication

OneDep uses short-lived JWT access tokens (30-minute TTL) paired with long-lived opaque refresh tokens (30-day TTL). The `TokenStore` class manages the full token lifecycle: storage, expiry detection, automatic refresh, and revocation.

### Getting a token pair

Tokens are generated manually by a logged-in user in the OneDep web UI. There is no programmatic login flow in this library.

### Storing tokens

Once you have a token pair from the web UI, store it with `TokenStore.store_tokens()`:

```python
from onedep_lib.auths.token import TokenStore
from onedep_lib.config import DepositConfig

cfg = DepositConfig.load()
store = TokenStore(config=cfg)
store.store_tokens(
    access_token="eyJ...",
    refresh_token="opaque-string",
)
```

This writes the tokens to the `[auths.<fqdn>]` section of the config file and updates the in-memory config fields. The FQDN key is derived from the resolved hostname by stripping the URL scheme, port, and path, and replacing `.` and `-` with `_`. For example, `https://deposit.wwpdb.org/deposition` becomes `deposit_wwpdb_org`.

The resulting config file looks like:

```toml
[default]
hostname = "https://deposit.wwpdb.org/deposition"

[auths.deposit_wwpdb_org]
access_token = "eyJ..."
refresh_token = "opaque-string"
```

Multiple hosts can coexist in the same file under separate `[auths.<fqdn>]` tables.

### Accessing tokens

Call `get_access_token()` to retrieve a valid access token. If the stored token is expired or about to expire (within 60 seconds), `TokenStore` automatically calls the refresh endpoint and persists the rotated token pair before returning:

```python
token = store.get_access_token()   # refreshes transparently if needed
```

On next startup, `DepositConfig.load()` reads the `[auths.<fqdn>]` section matching the hostname and populates `access_token` and `refresh_token` into the config object. No extra call is needed:

```python
cfg = DepositConfig.load()
print(cfg.access_token)   # populated from [auths.deposit_wwpdb_org]
```

### Token refresh

Refresh is automatic inside `get_access_token()`, or can be triggered explicitly:

```python
new_access_token = store.refresh()
```

Refresh token rotation is mandatory: each successful refresh call invalidates the old refresh token and issues a new one. Both tokens are persisted to the config file immediately. If the refresh token is expired, revoked, or otherwise invalid, the server returns `401` and `TokenStore` raises `AuthError` with a message prompting the user to generate and paste a new token pair.

### Revocation

```python
store.revoke()
```

This posts the current refresh token to the server's revoke endpoint and, on success (`204 No Content`), removes the `[auths.<fqdn>]` entry from the config file and clears the in-memory fields. After revocation, `get_access_token()` raises `AuthError`.

To clear tokens locally without contacting the server:

```python
store.clear_tokens()
```

### Error handling

All auth errors raise `onedep_lib.exceptions.AuthError`. Config file parse errors raise `ConfigError` (surfaced as `AuthError` when they originate inside `TokenStore`).

| Situation | Error |
|---|---|
| No tokens stored | `AuthError("No access token stored...")` |
| Refresh token expired / revoked | `AuthError("...generate and paste a new token pair")` |
| Network failure during refresh/revoke | `AuthError("Token refresh/revoke failed: ...")` |
| Malformed token values in config file | `AuthError("Malformed token data...")` or `ConfigError` from `load()` |

## DSP API

The DSP (Deposition Software Provider) API is the high-level interface for third-party suites (CCP4, Phenix, GlobalPhasing) to stage files locally, run pre-submission checks, and submit depositions to OneDep. It persists session state in a local JSON file so workflows can be interrupted and resumed.

### New deposition

```python
import onedep_lib as dsp

with dsp.deposit_init(
    email="depositor@example.org",
    users=["0000-0002-5109-8728"],   # ORCID IDs
    country=dsp.Country.USA,
    experiment_type=dsp.ExperimentType.XRAY,
) as dep:
    print(dep.session_id)           # save this to resume later

    coord_id = dep.add_file("model.cif",   dsp.FileType.MMCIF_COORD)
    sf_id    = dep.add_file("data-sf.cif", dsp.FileType.CRYSTAL_STRUC_FACTORS)

    report = dep.check_required_files()
    if not report.ok:
        for issue in report.errors():
            print(issue.message)

    dep_id = dep.deposit()          # non-blocking; triggers upload + process
    print(dep.get_status())
```

See [`examples/xray_deposition.py`](examples/xray_deposition.py) for a complete walkthrough including per-file checks.

```python
with dsp.deposit_init(
    email="depositor@example.org",
    users=["0000-0002-5109-8728"],   # ORCID IDs
    country=dsp.Country.USA,
) as dep:
    dep.set_experiment_type(dsp.ExperimentType.EM)
    dep.set_em_params(em_subtype=dsp.EMSubType.SPA, coordinates=True)

    coord_id = dep.add_file('emd_33233.cif',   dsp.FileType.MMCIF_COORD)
    map_id = dep.add_file('emd_33233.map.gz',     dsp.FileType.EM_MAP)
    half1_id = dep.add_file('emd_33233_half_map_1.map.gz',   dsp.FileType.EM_HALF_MAP)
    half2_id = dep.add_file('emd_33233_half_map_2.map.gz',   dsp.FileType.EM_HALF_MAP)
    dep.add_file('emd_33233.png',   dsp.FileType.ENTRY_IMAGE)
    dep.check_required_files()

    dep.set_voxel_values(map_id,   spacing_x=1.0825, spacing_y=1.0825, spacing_z=1.0825, contour=0.01)
    dep.set_voxel_values(half1_id, spacing_x=1.0825, spacing_y=1.0825, spacing_z=1.0825, contour=0.01)
    dep.set_voxel_values(half2_id, spacing_x=1.0825, spacing_y=1.0825, spacing_z=1.0825, contour=0.01)
    dep.check_file_type(fsc_xml_id, dsp.FileType.FSC_XML)
    dep.deposit()
    dep.get_status()
```

See [`examples/em_deposition.py`](examples/em_deposition.py) for a complete walkthrough including per-file checks.

### Resume an existing session

Sessions are identified by a UUID printed at creation time. Pass it to `deposit_resume()` to reload the full session state — registered files and remote deposition ID included.

```python
dep = dsp.deposit_resume("your-session-uuid")

dep.add_file("extra.cif", dsp.FileType.CRYSTAL_STRUC_FACTORS)
dep.deposit()   # reuses the existing remote deposition if already submitted
```

See [`examples/resume_deposition.py`](examples/resume_deposition.py) for a complete example.

### List all sessions

Use `list_sessions()` to retrieve all local sessions with their registered files, ordered newest first:

```python
import onedep_lib as dsp
from pathlib import Path

sessions = dsp.list_sessions()

for session, files in sessions:
    print(session.session_id, session.created_at, session.email)
    print("  experiment:", session.experiment_type)
    print("  remote dep:", session.remote_dep_id or "(not submitted)")
    for f in files:
        print("  file:", f.file_path, f.file_type)
```

Sessions where `remote_dep_id` is `None` have not been submitted yet. To inspect sessions stored in a non-default location, pass a `base_dir`:

```python
sessions = dsp.list_sessions(base_dir=Path("/custom/session/dir"))
```

## Documentation

Documentation is built with [Zensical](https://zensical.org/) and deployed to GitHub Pages.

<!-- * **Live site:** https://wwPDB.github.io/onedep_lib/ -->
* **Preview locally:** `just docs-serve` (serves at http://localhost:8000)
* **Build:** `just docs-build`

API documentation is auto-generated from docstrings using [mkdocstrings](https://mkdocstrings.github.io/).

Docs deploy automatically on push to `main` via GitHub Actions. To enable this, go to your repo's Settings > Pages and set the source to **GitHub Actions**.

## Development

Run tests:

```bash
uv run pytest
```

Run quality checks (format, lint, type check, test):

```bash
just qa
```
