
import pytest

from onedep_lib import dsp
from onedep_lib.apis.deposit.models import WwPDBDeposition
from onedep_lib.checks.report import CheckReport
from onedep_lib.config import DepositConfig
from onedep_lib.dsp import deposit_init, deposit_resume
from onedep_lib.enums import Country, ExperimentType, FileType
from tests.unit.apis.deposit.test_stub_api_client import StubApiClient


class StubCheckRunner:
    """Structurally satisfies the CheckRunner Protocol."""

    def check_required_files(self, files, experiment_type, em_subtype) -> CheckReport:
        return CheckReport(source="stub", issues=[])

    def check_mmcif_file(self, file) -> CheckReport:
        return CheckReport(source="stub", issues=[])

    def check_mmcif_category(self, file, category) -> CheckReport:
        return CheckReport(source="stub", issues=[])

    def check_mmcif_field(self, file, category, field) -> CheckReport:
        return CheckReport(source="stub", issues=[])

    def check_file_type(self, file, file_type) -> CheckReport:
        return CheckReport(source="stub", issues=[])


@pytest.fixture
def stub_api():
    return StubApiClient()


@pytest.fixture
def dep(tmp_path, stub_api):
    return deposit_init(
        email="test@example.com",
        users=["0000-0001-2345-6789"],
        country=Country.USA,
        config=DepositConfig().load(),
        experiment_type=ExperimentType.XRAY,
        _base_dir=tmp_path,
        _api_client=stub_api,
        _check_runner=StubCheckRunner(),
    )


def test_session_id_is_set(dep):
    assert dep.session_id is not None


def test_remote_dep_id_initially_none(dep):
    assert dep.remote_dep_id is None


def test_site_url_initially_none(dep):
    assert dep.site_url is None


def test_site_base_url_initially_none(dep):
    assert dep.site_base_url is None


def test_deposit_returns_remote_id(dep, stub_api):
    remote_id = dep.deposit()
    assert remote_id == "D_999"
    assert dep.remote_dep_id == "D_999"


def test_deposit_exposes_site_url(dep, stub_api):
    dep.deposit()
    assert dep.site_url == "https://deposit-pdbe.wwpdb.org/deposition/D_999"


def test_deposit_exposes_site_base_url(dep):
    dep.deposit()

    assert dep.site_base_url == "https://deposit-pdbe.wwpdb.org/deposition"


def test_deposit_persists_site_base_url(dep, tmp_path, stub_api):
    session_id = dep.session_id
    dep.deposit()
    dep.close()

    resumed = deposit_resume(
        session_id,
        config=DepositConfig(),
        _base_dir=tmp_path,
        _api_client=stub_api,
        _check_runner=StubCheckRunner(),
    )

    assert resumed.site_base_url == "https://deposit-pdbe.wwpdb.org/deposition"
    resumed.close()


def test_deposit_resume_uses_session_site_base_url_for_client(monkeypatch, tmp_path):
    monkeypatch.setenv("ONEDEP_ACCESS_TOKEN", "env-access")
    monkeypatch.setenv("ONEDEP_REFRESH_TOKEN", "env-refresh")

    dep = deposit_init(
        email="test@example.com",
        users=["0000-0001-2345-6789"],
        country=Country.USA,
        config=DepositConfig(session_dir=tmp_path),
        experiment_type=ExperimentType.XRAY,
        _base_dir=tmp_path,
        _api_client=StubApiClient(),
        _check_runner=StubCheckRunner(),
    )
    dep._store.set_remote_dep_id(
        "D_999",
        site_url="https://deposit-pdbe.wwpdb.org/deposition/D_999",
        site_base_url="https://deposit-pdbe.wwpdb.org/deposition",
    )
    session_id = dep.session_id
    dep.close()

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[auths.default_wwpdb_org]
access_token = "default-access"
refresh_token = "default-refresh"

[auths.deposit_pdbe_wwpdb_org]
access_token = "pdbe-access"
refresh_token = "pdbe-refresh"
""",
        encoding="utf-8",
    )

    captured = {}

    class CapturingClient:
        def __init__(self, config, auth_provider, **kwargs):
            captured["hostname"] = config.hostname
            captured["access_token"] = config.access_token
            captured["auth_provider"] = auth_provider

    monkeypatch.setattr(dsp, "HttpApiClient", CapturingClient)
    monkeypatch.setattr(dsp, "TokenStore", lambda config: ("token-store", config.hostname))

    deposit_resume(
        session_id,
        config=DepositConfig.load(
            hostname="https://default.wwpdb.org/deposition",
            session_dir=tmp_path,
            config_path=config_path,
        ),
        _base_dir=tmp_path,
        _check_runner=StubCheckRunner(),
    )

    assert captured["hostname"] == "https://deposit-pdbe.wwpdb.org/deposition"
    assert captured["access_token"] == "pdbe-access"
    assert captured["auth_provider"] == ("token-store", "https://deposit-pdbe.wwpdb.org/deposition")


def test_deposit_uses_client_site_base_url_fallback(tmp_path):
    class ClientSiteBaseApi(StubApiClient):
        site_base_url = "https://client.example.org/deposition"

        def create_deposition(self, email, users, country, experiments, password="") -> WwPDBDeposition:
            return WwPDBDeposition(
                dep_id="D_999",
                email=email,
                pdb_id=None,
                emdb_id=None,
                bmrb_id=None,
                title="",
                hold_exp_date=None,
                created="2024-01-01T00:00:00",
                last_login="2024-01-01T00:00:00",
                site="pdbe",
                status="DEP",
                site_url="https://deposit-pdbe.wwpdb.org/deposition/D_999",
                site_base_url=None,
            )

    api_client = ClientSiteBaseApi()
    dep = deposit_init(
        config=DepositConfig(),
        email="test.com",
        users=["0000-0001-2345-6789"],
        country=Country.USA,
        experiment_type=ExperimentType.XRAY,
        _base_dir=tmp_path,
        _api_client=api_client,
        _check_runner=StubCheckRunner(),
    )

    dep.deposit()

    assert dep.site_base_url == "https://client.example.org/deposition"


def test_deposit_calls_process(dep, stub_api):
    dep.deposit()
    assert "D_999" in stub_api.processed


def test_get_status_after_deposit(dep):
    dep.deposit()
    status = dep.get_status()
    assert status.status == "DEP"


def test_get_status_before_deposit_raises(dep):
    with pytest.raises(RuntimeError, match="deposit\\(\\) has not been called"):
        dep.get_status()


def test_deposit_without_experiment_type_raises(tmp_path):
    dep = deposit_init(
        email="test@example.com",
        users=[],
        country=Country.USA,
        config=DepositConfig().load(),
        _base_dir=tmp_path,
        _api_client=StubApiClient(),
        _check_runner=StubCheckRunner(),
    )
    with pytest.raises(ValueError, match="experiment_type must be set"):
        dep.deposit()


def test_add_and_remove_file(dep, tmp_path):
    test_file = tmp_path / "coords.cif"
    test_file.write_text("data_test")
    file_id = dep.add_file(str(test_file), FileType.MMCIF_COORD)
    assert file_id is not None
    dep.remove_file(file_id)


def test_add_nonexistent_file_raises(dep):
    with pytest.raises(FileNotFoundError):
        dep.add_file("/nonexistent/path.cif", FileType.MMCIF_COORD)


def test_check_auth_key_returns_bool(monkeypatch):
    class StubAuthClient:
        def __init__(self, config, auth_provider, **kwargs):
            self.config = config
            self.auth_provider = auth_provider

        def get_all_depositions(self):
            return []

    monkeypatch.setattr(dsp, "HttpApiClient", StubAuthClient)

    result = dsp.check_auth_key(DepositConfig(access_token="access", refresh_token="refresh"))

    assert isinstance(result, bool)
    assert result is True


def test_deposit_resume_restores_session(dep, tmp_path, stub_api):
    session_id = dep.session_id
    dep.close()
    resumed = deposit_resume(
        session_id,
        config=DepositConfig().load(),
        _base_dir=tmp_path,
        _api_client=stub_api,
        _check_runner=StubCheckRunner(),
    )
    assert resumed.session_id == session_id
    resumed.close()


def test_context_manager(tmp_path, stub_api):
    with deposit_init(
        email="test@example.com",
        users=[],
        country=Country.USA,
        config=DepositConfig().load(),
        experiment_type=ExperimentType.XRAY,
        _base_dir=tmp_path,
        _api_client=stub_api,
        _check_runner=StubCheckRunner(),
    ) as dep:
        assert dep.session_id is not None


def test_created_at_is_timezone_aware(dep):
    # created_at must be tz-aware (UTC) to match the tz-aware file_mtime and
    # avoid naive/aware comparison errors.
    created = dep._session.created_at
    assert created.tzinfo is not None
