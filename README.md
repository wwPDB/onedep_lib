# OneDep Remote API Library

![PyPI version](https://img.shields.io/pypi/v/onedep_lib.svg)

Prepares data for OneDep through the Deposition API. Required-file checks use experiment-specific JSON schemas. Local mmCIF validation uses Gemmi, caller-supplied dictionaries, and configurable cross-check rule files. Validation is advisory: explicit checks return structured reports, and submission exposes automatic checks through `dep.metadata_report` without rejecting deposition because of validation findings.

## Installation

```bash
uv add onedep_lib
```

## Quickstart

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
    dep.add_file("model.cif", dsp.FileType.MMCIF_COORD)
    dep.add_file("data-sf.cif", dsp.FileType.CRYSTAL_STRUC_FACTORS)

    report = dep.check_required_files()
    if not report.ok:
        for issue in report.errors():
            print(issue.message)

    dep_id = dep.deposit()
    print(dep.get_status())
```

## Documentation

Full documentation — configuration, authentication, the complete deposition workflow, and API reference — is at **https://wwPDB.github.io/onedep_lib/**.

* **Preview locally:** `just docs-serve` (serves at http://localhost:8000)
* **Build:** `just docs-build`

Docs deploy automatically on push to `main` via GitHub Actions.

## Development

Run tests:

```bash
uv run pytest
```

Run quality checks (format, lint, type check, test):

```bash
just qa
```
