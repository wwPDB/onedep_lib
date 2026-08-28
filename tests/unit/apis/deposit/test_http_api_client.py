import json
from importlib.metadata import PackageNotFoundError

import pytest
import requests
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Response

from onedep_lib.apis.deposit import client as client_module
from onedep_lib.apis.deposit.client import HttpApiClient
from onedep_lib.apis.deposit.models import DepositedFile, DepositStatus, Experiment, WwPDBDeposition
from onedep_lib.config import DepositConfig
from onedep_lib.enums import Country, ExperimentType, FileType
from onedep_lib.exceptions import ApiError, ApiUnreachableError


class StubAuthProvider:
    def __init__(self) -> None:
        self.calls = 0

    def get_access_token(self) -> str:
        self.calls += 1
        return f"token-{self.calls}"


_DEPOSIT_RESPONSE = {
    "id": "D_800001",
    "email": "test@example.com",
    "pdb_id": "?",
    "emdb_id": "?",
    "bmrb_id": "?",
    "title": "Test",
    "hold_exp_date": None,
    "created": "2024-01-01T00:00:00",
    "last_login": "2024-01-01T00:00:00",
    "site": "pdbe",
    "status": "DEP",
    "experiments": [],
    "errors": [],
}

_FILE_RESPONSE = {
    "id": 1,
    "name": "test.cif",
    "type": "co-cif",
    "created": "Monday, January 01, 2024 00:00:00",
    "errors": [],
    "warnings": [],
}

_STATUS_RESPONSE = {
    "status": "DEP",
    "action": "deposit",
    "step": "1",
    "details": "deposited",
    "date": "2024-01-01T00:00:00",
}


def test_client_derives_api_base_from_site_root(api_config):
    client = HttpApiClient(api_config)

    assert client.site_base_url == api_config.hostname.rstrip("/")
    assert client.api_base_url == f"{api_config.hostname.rstrip('/')}/api/v1/"


def test_client_normalizes_accidental_api_base_url(api_config):
    config = DepositConfig(
        hostname=f"{api_config.hostname.rstrip('/')}/api/v1/",
        ssl_verify=False,
        redirect=True,
    )
    client = HttpApiClient(config)

    assert client.site_base_url == api_config.hostname.rstrip("/")
    assert client.api_base_url == f"{api_config.hostname.rstrip('/')}/api/v1/"


def test_create_deposition(httpserver: HTTPServer, client: HttpApiClient):
    httpserver.expect_request("/api/v1/depositions/new", method="POST").respond_with_json(_DEPOSIT_RESPONSE)
    dep = client.create_deposition(
        email="test@example.com",
        users=["0000-0001-2345-6789"],
        country=Country.USA,
        experiments=[Experiment(exp_type=ExperimentType.XRAY)],
    )
    assert isinstance(dep, WwPDBDeposition)
    assert dep.dep_id == "D_800001"


def test_auth_provider_sets_bearer_token_before_request(httpserver: HTTPServer, api_config):
    auth = StubAuthProvider()
    httpserver.expect_request(
        "/api/v1/depositions/D_800001/status",
        method="GET",
        headers={"Authorization": "Bearer token-1"},
    ).respond_with_json(_STATUS_RESPONSE)
    client = HttpApiClient(api_config, auth_provider=auth)
    status = client.get_status("D_800001")
    assert isinstance(status, DepositStatus)
    assert auth.calls == 1


def test_user_agent_identifies_the_library(httpserver: HTTPServer, client: HttpApiClient):
    seen = []
    httpserver.expect_request("/api/v1/depositions/D_800001/status", method="GET").respond_with_handler(
        lambda request: (
            seen.append(request.headers.get("User-Agent"))
            or Response(json.dumps(_STATUS_RESPONSE), content_type="application/json")
        )
    )

    client.get_status("D_800001")

    assert seen[0] == client_module._USER_AGENT
    assert seen[0].startswith("onedep_lib/")


def test_user_agent_version_falls_back_when_package_metadata_is_missing(monkeypatch):
    # Running from a source checkout must still produce a well-formed header
    # rather than raising at client construction time.
    def _missing(_name):
        raise PackageNotFoundError(_name)

    monkeypatch.setattr(client_module, "version", _missing)

    assert client_module._package_version() == "unknown"
    assert client_module._user_agent().startswith("onedep_lib/unknown ")


def test_get_status(httpserver: HTTPServer, client: HttpApiClient):
    httpserver.expect_request("/api/v1/depositions/D_800001/status", method="GET").respond_with_json(_STATUS_RESPONSE)
    status = client.get_status("D_800001")
    assert isinstance(status, DepositStatus)
    assert status.status == "DEP"


def test_upload_file(httpserver: HTTPServer, client: HttpApiClient, tmp_path):
    test_file = tmp_path / "test.cif"
    test_file.write_text("data_test")
    httpserver.expect_request("/api/v1/depositions/D_800001/files/", method="POST").respond_with_json(_FILE_RESPONSE)
    deposited = client.upload_file("D_800001", str(test_file), FileType.MMCIF_COORD)
    assert isinstance(deposited, DepositedFile)
    assert deposited.file_id == 1
    assert deposited.file_type is FileType.MMCIF_COORD


def test_upload_file_missing_raises(client: HttpApiClient):
    with pytest.raises(ApiError):
        client.upload_file("D_800001", "/nonexistent/path.cif", FileType.MMCIF_COORD)


def test_non_2xx_raises_api_error(httpserver: HTTPServer, client: HttpApiClient):
    httpserver.expect_request("/api/v1/depositions/D_999/status").respond_with_data("Not Found", status=404)
    with pytest.raises(ApiError):
        client.get_status("D_999")


def test_unreachable_api_raises_api_unreachable_error():
    # Port 1 on loopback: nothing listens, so requests fails at the transport
    # layer and no HTTP response ever exists.
    config = DepositConfig(hostname="http://127.0.0.1:1", ssl_verify=False, redirect=True)
    client = HttpApiClient(config)
    with pytest.raises(ApiUnreachableError) as excinfo:
        client.get_all_depositions()
    assert excinfo.value.status_code is None
    # The underlying transport failure stays attached for diagnosis.
    assert isinstance(excinfo.value.__cause__, requests.exceptions.RequestException)


def test_unreachable_api_is_not_reported_as_an_auth_failure(httpserver: HTTPServer, client: HttpApiClient):
    # A genuine 403 from the server and an unreachable server must not look the
    # same: callers use this to tell "your token was rejected" from "we could
    # not reach OneDep", and telling a user their credentials are bad because
    # their wifi is off is worse than saying nothing.
    httpserver.expect_request("/api/v1/depositions/").respond_with_data("Forbidden", status=403)
    with pytest.raises(ApiError) as denied:
        client.get_all_depositions()
    assert denied.value.status_code == 403
    assert not isinstance(denied.value, ApiUnreachableError)

    offline = HttpApiClient(DepositConfig(hostname="http://127.0.0.1:1", ssl_verify=False, redirect=True))
    with pytest.raises(ApiUnreachableError) as unreachable:
        offline.get_all_depositions()
    assert unreachable.value.status_code is None


def test_redirect_updates_base_url_and_retries(httpserver: HTTPServer, api_config):
    correct_base = httpserver.url_for("").rstrip("/")
    httpserver.expect_ordered_request("/api/v1/depositions/", method="GET").respond_with_json(
        {
            "code": "invalid_location",
            "extras": {"base_url": f"{correct_base}/api/v1/"},
        }
    )
    httpserver.expect_ordered_request("/api/v1/depositions/", method="GET").respond_with_json({"items": []})
    client = HttpApiClient(api_config)
    result = client.get_all_depositions()
    assert result == []
    assert client.site_base_url == correct_base
    assert client.api_base_url == f"{correct_base}/api/v1/"


def test_redirect_retry_malformed_redirect_raises_api_error(httpserver: HTTPServer, api_config):
    correct_base = httpserver.url_for("").rstrip("/")
    httpserver.expect_ordered_request("/api/v1/depositions/", method="GET").respond_with_json(
        {
            "code": "invalid_location",
            "extras": {"base_url": f"{correct_base}/api/v1/"},
        }
    )
    httpserver.expect_ordered_request("/api/v1/depositions/", method="GET").respond_with_json(
        {"code": "invalid_location", "extras": {}}
    )
    client = HttpApiClient(api_config)

    with pytest.raises(ApiError, match="missing base_url"):
        client.get_all_depositions()


def test_upload_file_redirect_normalizes_base_url(httpserver: HTTPServer, api_config, tmp_path):
    test_file = tmp_path / "test.cif"
    test_file.write_bytes(b"X" * 8)
    correct_base = httpserver.url_for("").rstrip("/")

    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
    ).respond_with_json(
        {
            "code": "invalid_location",
            "extras": {"base_url": f"{correct_base}/api/v1/"},
        }
    )
    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
    ).respond_with_json({**_FILE_RESPONSE, "uploadedBytes": 8})

    client = HttpApiClient(api_config)
    deposited = client.upload_file("D_800001", str(test_file), FileType.MMCIF_COORD, _chunk_size=8)

    assert deposited.file_id == 1
    assert client.site_base_url == correct_base
    assert client.api_base_url == f"{correct_base}/api/v1/"


def test_json_malformed_redirect_raises_api_error(httpserver: HTTPServer, client: HttpApiClient):
    httpserver.expect_request("/api/v1/depositions/", method="GET").respond_with_json(
        {"code": "invalid_location", "extras": {}}
    )

    with pytest.raises(ApiError, match="missing base_url"):
        client.get_all_depositions()


def test_upload_file_malformed_redirect_raises_api_error(httpserver: HTTPServer, client: HttpApiClient, tmp_path):
    test_file = tmp_path / "test.cif"
    test_file.write_bytes(b"X" * 8)
    httpserver.expect_request("/api/v1/depositions/D_800001/files/", method="POST").respond_with_json(
        {"code": "invalid_location", "extras": {"base_url": " "}}
    )

    with pytest.raises(ApiError, match="missing base_url"):
        client.upload_file("D_800001", str(test_file), FileType.MMCIF_COORD, _chunk_size=8)


def test_redirect_disabled_does_not_mutate_base_url(httpserver: HTTPServer, api_config):
    original_site_base_url = api_config.hostname.rstrip("/")
    redirected_site_base_url = "https://other.example.org/deposition"
    redirected_base = f"{redirected_site_base_url}/api/v1/"
    config = DepositConfig(
        hostname=original_site_base_url,
        ssl_verify=False,
        redirect=False,
    )
    httpserver.expect_request("/api/v1/depositions/", method="GET").respond_with_json(
        {
            "code": "invalid_location",
            "extras": {"base_url": redirected_base},
        }
    )
    client = HttpApiClient(config)

    with pytest.raises(ApiError, match=redirected_site_base_url):
        client.get_all_depositions()

    assert client.site_base_url == original_site_base_url
    assert client.api_base_url == f"{original_site_base_url}/api/v1/"


def test_204_returns_empty(httpserver: HTTPServer, client: HttpApiClient):
    httpserver.expect_request("/api/v1/depositions/D_1/files/1", method="DELETE").respond_with_data("", status=204)
    result = client.remove_file("D_1", 1)
    assert result is True


def test_upload_file_chunked_sends_content_range(httpserver: HTTPServer, client: HttpApiClient, tmp_path):
    test_file = tmp_path / "test.cif"
    test_file.write_bytes(b"X" * 20)
    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
        headers={"Content-Range": "bytes 0-7/20"},
    ).respond_with_json({"uploadedBytes": 8})
    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
        headers={"Content-Range": "bytes 8-15/20"},
    ).respond_with_json({"uploadedBytes": 16})
    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
        headers={"Content-Range": "bytes 16-19/20"},
    ).respond_with_json(_FILE_RESPONSE)
    deposited = client.upload_file("D_800001", str(test_file), FileType.MMCIF_COORD, _chunk_size=8)
    assert deposited.file_id == 1
    assert deposited.file_type is FileType.MMCIF_COORD


def test_upload_file_seeks_to_server_uploaded_bytes(httpserver: HTTPServer, client: HttpApiClient, tmp_path):
    test_file = tmp_path / "test.cif"
    test_file.write_bytes(b"abcdefghijklmnopqrst")
    uploaded_chunks = []

    def uploaded_bytes_response(uploaded_bytes: int):
        def handler(request):
            uploaded_chunks.append(request.files["file"].read())
            return Response(json.dumps({"uploadedBytes": uploaded_bytes}), content_type="application/json")

        return handler

    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
        headers={"Content-Range": "bytes 0-7/20"},
    ).respond_with_handler(uploaded_bytes_response(4))
    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
        headers={"Content-Range": "bytes 4-11/20"},
    ).respond_with_handler(uploaded_bytes_response(12))
    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
        headers={"Content-Range": "bytes 12-19/20"},
    ).respond_with_handler(
        lambda request: (
            uploaded_chunks.append(request.files["file"].read())
            or Response(json.dumps({**_FILE_RESPONSE, "uploadedBytes": 20}), content_type="application/json")
        )
    )

    deposited = client.upload_file("D_800001", str(test_file), FileType.MMCIF_COORD, _chunk_size=8)

    assert deposited.file_id == 1
    assert uploaded_chunks == [b"abcdefgh", b"efghijkl", b"mnopqrst"]


def test_upload_file_chunked_final_response_includes_uploaded_bytes(
    httpserver: HTTPServer, client: HttpApiClient, tmp_path
):
    test_file = tmp_path / "test.cif"
    test_file.write_bytes(b"X" * 20)
    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
        headers={"Content-Range": "bytes 0-7/20"},
    ).respond_with_json({"uploadedBytes": 8})
    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
        headers={"Content-Range": "bytes 8-15/20"},
    ).respond_with_json({"uploadedBytes": 16})
    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
        headers={"Content-Range": "bytes 16-19/20"},
    ).respond_with_json({**_FILE_RESPONSE, "uploadedBytes": 20})
    deposited = client.upload_file("D_800001", str(test_file), FileType.MMCIF_COORD, _chunk_size=8)
    assert deposited.file_id == 1
    assert deposited.file_type is FileType.MMCIF_COORD


def test_upload_file_resumes_from_uploaded_bytes(httpserver: HTTPServer, client: HttpApiClient, tmp_path):
    test_file = tmp_path / "test.cif"
    test_file.write_bytes(b"X" * 16)
    httpserver.expect_ordered_request(
        "/api/v1/depositions/D_800001/files/",
        method="POST",
        headers={"Content-Range": "bytes 8-15/16"},
    ).respond_with_json(_FILE_RESPONSE)
    deposited = client.upload_file("D_800001", str(test_file), FileType.MMCIF_COORD, uploaded_bytes=8, _chunk_size=8)
    assert deposited.file_id == 1


class RedirectSwitchingAuthProvider:
    def __init__(self) -> None:
        self.token = "default-access"
        self.activated_sites: list[str] = []

    def get_access_token(self) -> str:
        return self.token

    def activate_site(self, site_base_url: str) -> str:
        self.activated_sites.append(site_base_url)
        self.token = "new-site-access"
        return self.token


def test_redirect_switches_auth_provider_before_retry(httpserver: HTTPServer, api_config):
    correct_base = httpserver.url_for("").rstrip("/")
    auth = RedirectSwitchingAuthProvider()
    httpserver.expect_ordered_request(
        "/api/v1/depositions/",
        method="GET",
        headers={"Authorization": "Bearer default-access"},
    ).respond_with_json(
        {
            "code": "invalid_location",
            "extras": {"base_url": f"{correct_base}/api/v1/"},
        }
    )
    httpserver.expect_ordered_request(
        "/api/v1/depositions/",
        method="GET",
        headers={"Authorization": "Bearer new-site-access"},
    ).respond_with_json({"items": []})

    client = HttpApiClient(api_config, auth_provider=auth)

    assert client.get_all_depositions() == []
    assert auth.activated_sites == [correct_base]


class FailingRedirectAuthProvider(RedirectSwitchingAuthProvider):
    def activate_site(self, site_base_url: str) -> str:
        self.activated_sites.append(site_base_url)
        raise RuntimeError("token exchange failed")


def test_redirect_activation_failure_does_not_switch_base_url(httpserver: HTTPServer, api_config):
    original_base = api_config.hostname.rstrip("/")
    redirected_base = f"{original_base}/alternate-deposition"
    auth = FailingRedirectAuthProvider()
    httpserver.expect_request("/api/v1/depositions/", method="GET").respond_with_json(
        {
            "code": "invalid_location",
            "extras": {"base_url": f"{redirected_base}/api/v1/"},
        }
    )
    client = HttpApiClient(api_config, auth_provider=auth)

    with pytest.raises(RuntimeError, match="token exchange failed"):
        client.get_all_depositions()

    assert client.site_base_url == original_base
    assert auth.activated_sites == [redirected_base]


def test_redirect_rejects_same_host_https_downgrade():
    config = DepositConfig(hostname="https://deposit.wwpdb.org/deposition", redirect=True)
    auth = RedirectSwitchingAuthProvider()
    client = HttpApiClient(config, auth_provider=auth)

    with pytest.raises(ApiError, match="not allowed"):
        client._handle_redirect(
            {
                "code": "invalid_location",
                "extras": {"base_url": "http://deposit.wwpdb.org/deposition/api/v1/"},
            }
        )

    assert client.site_base_url == "https://deposit.wwpdb.org/deposition"
    assert auth.activated_sites == []


def test_redirect_rejects_untrusted_site_before_retry(httpserver: HTTPServer, api_config):
    auth = RedirectSwitchingAuthProvider()
    httpserver.expect_request("/api/v1/depositions/", method="GET").respond_with_json(
        {
            "code": "invalid_location",
            "extras": {"base_url": "https://deposit.wwpdb.org.evil.example/deposition"},
        }
    )
    client = HttpApiClient(api_config, auth_provider=auth)

    with pytest.raises(ApiError, match="not allowed"):
        client.get_all_depositions()

    assert auth.activated_sites == []
