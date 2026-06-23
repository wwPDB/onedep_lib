import pytest
from pytest_httpserver import HTTPServer
from onedep_lib.apis.deposit.client import HttpApiClient
from onedep_lib.config import DepositConfig


@pytest.fixture
def api_config(httpserver: HTTPServer) -> DepositConfig:
    return DepositConfig(
        hostname=httpserver.url_for("").rstrip("/"),
        ssl_verify=False,
        redirect=True,
    )


@pytest.fixture
def client(api_config: DepositConfig) -> HttpApiClient:
    return HttpApiClient(api_config)
