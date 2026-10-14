import json

import pytest
from requests import Response

from onedep_lib._http_errors import error_arguments


@pytest.mark.parametrize(
    "body",
    [None, [], 42, "body text", {}, {"message": ""}, {"message": False, "error": ["private value"]}],
)
def test_reader_preserves_exact_fallback(body):
    response = Response()
    response._content = json.dumps(body).encode()
    assert error_arguments(response, "Original HTTP reason") == {
        "message": "Original HTTP reason",
        "code": None,
        "extras": None,
        "details": None,
        "request_id": None,
    }


def test_reader_ignores_malformed_diagnostics_and_prefers_message():
    response = Response()
    response._content = json.dumps(
        {
            "message": "Server message",
            "error": "Legacy message",
            "code": ["invalid"],
            "extras": "private data",
            "details": 12,
        }
    ).encode()
    arguments = error_arguments(response, "fallback")
    assert arguments["message"] == "Server message"
    assert arguments["code"] is None
    assert arguments["extras"] is None
    assert arguments["details"] is None
