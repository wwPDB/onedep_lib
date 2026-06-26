import pytest
import tempfile
from datetime import datetime
from pathlib import Path
from onedep_lib.session.json_store import JsonSessionStore
from onedep_lib.apis.deposit.client import HttpApiClient
from onedep_lib.checks.runner import CheckRunner
from onedep_lib.config import DepositConfig
from onedep_lib.schemas.local import LocalSchemaProvider
from onedep_lib.dsp import Deposition
from onedep_lib.session.models import LocalSession
from onedep_lib.enums import Country, ExperimentType, FileType


@pytest.fixture
def exptypes():
    return [ExperimentType.XRAY, ExperimentType.FIBER, ExperimentType.NEUTRON, ExperimentType.EM, ExperimentType.EC, ExperimentType.NMR, ExperimentType.SSNMR]


def test_get_file_types(exptypes: list[ExperimentType]):
    with tempfile.TemporaryDirectory() as tmp:
        for exp in exptypes:
            tmpath = Path(tmp)
            store = JsonSessionStore(session_id="test-session", base_dir=tmpath)
            session = LocalSession(session_id="test-session", email="", users=[], country=Country.USA, experiment_type=exp, created_at=datetime.now())
            store.create_session(session)
            config = DepositConfig()
            client = HttpApiClient(config)
            provider = LocalSchemaProvider(config.local_schema_cache_dir)
            runner = CheckRunner(provider)
            dep = Deposition(store=store, api_client=client, check_runner=runner)
            filetypes = dep.get_experiment_file_types()
            assert isinstance(filetypes, list), "non-list for experiment type: " + exp.value + ""
            assert len(filetypes) > 0, "empty list for experiment type: " + exp.value + ""
