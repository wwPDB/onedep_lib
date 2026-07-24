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

All exceptions inherit from `onedep_lib.exceptions.OneDepError`. See the [API Reference](api.md#exceptions) for the full hierarchy, or [Authentication Flow](auth_flow.md) for auth-specific error handling.
