# onedep_lib

`onedep_lib` prepares data for deposition into the [OneDep](https://deposit.wwpdb.org/) system through the Deposition API. It's used by third-party deposition software providers (CCP4, Phenix, GlobalPhasing) to stage files locally, run pre-submission checks against JSON schemas served by OneDep, and submit depositions once all required files are present.

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

See [Installation](installation.md) to get set up, [Usage](usage.md) for configuration and the full deposition workflow, [Authentication Flow](auth_flow.md) for token handling, and [API Reference](api.md) for complete details on every public class and function.
