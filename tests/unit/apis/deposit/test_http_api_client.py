import pytest
from pytest_httpserver import HTTPServer
from onedep_lib.apis.deposit.client import HttpApiClient
from onedep_lib.apis.deposit.models import WwPDBDeposition, DepositedFile, DepositStatus
from onedep_lib.enums import Country, ExperimentType, FileType
from onedep_lib.apis.deposit.models import Experiment
from onedep_lib.exceptions import ApiError


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


def test_create_deposition(httpserver: HTTPServer, client: HttpApiClient):
    httpserver.expect_request("/api/v1/depositions/new", method="POST").respond_with_json(
        _DEPOSIT_RESPONSE
    )
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


def test_get_status(httpserver: HTTPServer, client: HttpApiClient):
    httpserver.expect_request("/api/v1/depositions/D_800001/status", method="GET").respond_with_json(
        _STATUS_RESPONSE
    )
    status = client.get_status("D_800001")
    assert isinstance(status, DepositStatus)
    assert status.status == "DEP"


def test_upload_file(httpserver: HTTPServer, client: HttpApiClient, tmp_path):
    test_file = tmp_path / "test.cif"
    test_file.write_text("data_test")
    httpserver.expect_request("/api/v1/depositions/D_800001/files/", method="POST").respond_with_json(
        _FILE_RESPONSE
    )
    deposited = client.upload_file("D_800001", str(test_file), FileType.MMCIF_COORD)
    assert isinstance(deposited, DepositedFile)
    assert deposited.file_id == 1
    assert deposited.file_type is FileType.MMCIF_COORD


def test_upload_file_missing_raises(client: HttpApiClient):
    with pytest.raises(ApiError):
        client.upload_file("D_800001", "/nonexistent/path.cif", FileType.MMCIF_COORD)


def test_non_2xx_raises_api_error(httpserver: HTTPServer, client: HttpApiClient):
    httpserver.expect_request("/api/v1/depositions/D_999/status").respond_with_data(
        "Not Found", status=404
    )
    with pytest.raises(ApiError):
        client.get_status("D_999")


def test_redirect_updates_base_url_and_retries(httpserver: HTTPServer, api_config):
    correct_base = httpserver.url_for("").rstrip("/")
    httpserver.expect_ordered_request("/api/v1/depositions/", method="GET").respond_with_json({
        "code": "invalid_location",
        "extras": {"base_url": correct_base},
    })
    httpserver.expect_ordered_request("/api/v1/depositions/", method="GET").respond_with_json({
        "items": []
    })
    client = HttpApiClient(api_config)
    result = client.get_all_depositions()
    assert result == []


def test_204_returns_empty(httpserver: HTTPServer, client: HttpApiClient):
    httpserver.expect_request("/api/v1/depositions/D_1/files/1", method="DELETE").respond_with_data(
        "", status=204
    )
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
    deposited = client.upload_file(
        "D_800001", str(test_file), FileType.MMCIF_COORD, uploaded_bytes=8, _chunk_size=8
    )
    assert deposited.file_id == 1
