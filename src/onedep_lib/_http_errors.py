"""Read only the established message and diagnostic fields of HTTP errors."""

from __future__ import annotations

from typing import Any

import requests


def error_arguments(response: requests.Response, fallback: str | None) -> dict[str, Any]:
    """Return exception arguments without displaying arbitrary response bodies."""
    arguments: dict[str, Any] = {
        "message": fallback,
        "code": None,
        "extras": None,
        "details": None,
        "request_id": response.headers.get("X-Request-ID") or None,
    }
    try:
        body = response.json()
    except ValueError:
        return arguments
    if not isinstance(body, dict):
        return arguments
    for field in ("message", "error"):
        message = body.get(field)
        if isinstance(message, str) and message.strip():
            arguments["message"] = message
            break
    code = body.get("code")
    if isinstance(code, str):
        arguments["code"] = code
    for field in ("extras", "details"):
        value = body.get(field)
        if isinstance(value, (dict, list)):
            arguments[field] = value
    return arguments
