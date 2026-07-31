from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
from importlib.metadata import PackageNotFoundError, version
from json import JSONDecodeError
from typing import Union
from urllib.parse import urlsplit, urlunsplit

import requests
import urllib3

from onedep_lib.apis.deposit.models import (
    DepositedFile,
    DepositError,
    DepositStatus,
    Experiment,
    WwPDBDeposition,
)
from onedep_lib.auths.types import AuthProvider
from onedep_lib.config import DepositConfig
from onedep_lib.enums import Country, FileType
from onedep_lib.exceptions import ApiError, ApiUnreachableError

_API_SUFFIX_RE = re.compile(r"/api/v[0-9]+/?$")


def _package_version() -> str:
    try:
        return version("onedep_lib")
    except PackageNotFoundError:
        return "unknown"


def _user_agent() -> str:
    """Identify the library to the API so the server can attribute deposition traffic."""
    return (
        f"onedep_lib/{_package_version()} "
        f"python-requests/{requests.__version__} "
        f"(Python/{platform.python_version()}; {platform.system()}/{platform.release()})"
    )


_USER_AGENT = _user_agent()


def _normalize_site_base_url(url: str) -> str:
    stripped = url.rstrip("/")
    split = urlsplit(stripped)
    path = _API_SUFFIX_RE.sub("", split.path.rstrip("/"))
    return urlunsplit((split.scheme, split.netloc, path, "", ""))


def _api_base_url(site_base_url: str, version: str) -> str:
    return f"{site_base_url.rstrip('/')}/api/{version}/"


def _is_allowed_redirect_url(site_base_url: str, current_site_base_url: str, allowed_domain: str) -> bool:
    parsed = urlsplit(site_base_url)
    current = urlsplit(current_site_base_url)
    host = parsed.hostname.rstrip(".").lower() if parsed.hostname else None
    current_host = current.hostname.rstrip(".").lower() if current.hostname else None
    allowed = allowed_domain.rstrip(".").lower()
    if host is None:
        return False
    if current_host is not None and host == current_host:
        return parsed.scheme == current.scheme
    if parsed.scheme != "https":
        return False
    return host == allowed or host.endswith("." + allowed)


class HttpApiClient:
    def __init__(
        self,
        config: DepositConfig,
        auth_provider: AuthProvider | None = None,
        ver: str = "v1",
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._auth_provider = auth_provider
        self._ver = ver
        self._logger = logger or logging.getLogger(__name__)
        self._site_base_url = _normalize_site_base_url(config.hostname)
        self._base_url = _api_base_url(self._site_base_url, ver)
        if not config.ssl_verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._session = requests.Session()
        self._session.verify = config.ssl_verify
        self._session.headers["User-Agent"] = _USER_AGENT

    @property
    def site_base_url(self) -> str:
        return self._site_base_url

    @property
    def api_base_url(self) -> str:
        return self._base_url

    def _set_site_base_url(self, site_base_url: str) -> None:
        self._site_base_url = _normalize_site_base_url(site_base_url)
        self._base_url = _api_base_url(self._site_base_url, self._ver)

    def _refresh_auth_header(self) -> None:
        if self._auth_provider is not None:
            token = self._auth_provider.get_access_token()
        else:
            token = self._config.access_token or ""
        self._session.headers["Authorization"] = f"Bearer {token}"

    def _redirect_site_base_url(self, data_out: dict) -> str | None:
        if data_out.get("code") != "invalid_location":
            return None
        extras = data_out.get("extras", {})
        if not isinstance(extras, dict):
            raise ApiError("Invalid deposit site response missing base_url", 502)
        base_url = extras.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ApiError("Invalid deposit site response missing base_url", 502)
        return _normalize_site_base_url(base_url)

    def _handle_redirect(self, data_out: dict) -> bool:
        site_base_url = self._redirect_site_base_url(data_out)
        if site_base_url is None:
            return False
        self._logger.warning("Invalid deposit site, redirecting to %s", site_base_url)
        if not self._config.redirect:
            raise ApiError(f"Invalid deposit site; correct site is {site_base_url}", 400)
        if not _is_allowed_redirect_url(
            site_base_url,
            self._site_base_url,
            self._config.allowed_redirect_domain,
        ):
            raise ApiError(f"Redirect site is not allowed: {site_base_url}", 400)
        activate_site = getattr(self._auth_provider, "activate_site", None)
        if callable(activate_site):
            token = activate_site(site_base_url)
            self._session.headers["Authorization"] = f"Bearer {token}"
        else:
            self._refresh_auth_header()
        self._set_site_base_url(site_base_url)
        return True

    def _check_response(self, response: requests.Response) -> dict:
        if response.status_code == 204:
            return {}
        if not (200 <= response.status_code <= 299):
            self._logger.error("status=%s reason=%s", response.status_code, response.reason)
            raise ApiError(response.reason, response.status_code)
        try:
            return response.json()
        except (ValueError, JSONDecodeError) as e:
            raise ApiError("Bad JSON in response", 502) from e

    def _do(
        self,
        http_method: str,
        endpoint: str,
        params: dict | None = None,
        data: Union[dict, list, None] = None,
        files: dict | None = None,
        content_type: str = "application/json",
    ) -> dict:
        full_url = self._base_url + endpoint
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type

        self._refresh_auth_header()

        try:
            self._logger.debug("method=%s url=%s", http_method, full_url)
            response = self._session.request(
                method=http_method,
                url=full_url,
                headers=headers,
                params=params,
                json=data if content_type == "application/json" else None,
                data=data if content_type != "application/json" else None,
                files=files,
                timeout=300,
            )
        except requests.exceptions.RequestException as e:
            self._logger.error(str(e))
            raise ApiUnreachableError() from e

        data_out = self._check_response(response)

        if isinstance(data_out, dict) and self._handle_redirect(data_out):
            full_url = self._base_url + endpoint
            try:
                response = self._session.request(
                    method=http_method,
                    url=full_url,
                    headers=headers,
                    params=params,
                    json=data if content_type == "application/json" else None,
                    data=data if content_type != "application/json" else None,
                    files=files,
                    timeout=300,
                )
            except requests.exceptions.RequestException as e:
                raise ApiUnreachableError("Retry after redirect failed") from e
            data_out = self._check_response(response)
            if isinstance(data_out, dict):
                retry_site_base_url = self._redirect_site_base_url(data_out)
                if retry_site_base_url is not None:
                    raise ApiError("Redirect retry returned another invalid_location", 502)

        return data_out

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        return self._do("GET", endpoint, params=params)

    def _post(
        self,
        endpoint: str,
        data: Union[dict, list, None] = None,
        files: dict | None = None,
        content_type: str = "application/json",
    ) -> dict:
        return self._do("POST", endpoint, data=data, files=files, content_type=content_type)

    def _delete(self, endpoint: str) -> None:
        self._do("DELETE", endpoint)

    @staticmethod
    def _compute_chunk_size(file_size: int) -> int:
        """Compute chunk size targeting ~100 chunks, clamped to [5 MB, 128 MB]."""
        _min = 5 * 1024 * 1024
        _max = 128 * 1024 * 1024
        return max(_min, min(_max, file_size // 100))

    def _compute_md5(self, file_path: str, _chunk_size: int = 8 * 1024 * 1024) -> str:
        h = hashlib.md5()
        with open(file_path, "rb") as fp:
            for chunk in iter(lambda: fp.read(_chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()

    # --- ApiClient Protocol implementation ---

    def create_deposition(
        self,
        email: str,
        users: list[str],
        country: Country,
        experiments: list[Experiment],
        password: str = "",
    ) -> WwPDBDeposition:
        body: dict = {
            "email": email,
            "users": users,
            "country": country.value,
            "experiments": [exp.to_dict() for exp in experiments],
        }
        if password:
            body["password"] = password
        data = self._post("depositions/new", data=body)
        data["dep_id"] = data.pop("id")
        return WwPDBDeposition(**data)

    def get_deposition(self, dep_id: str) -> WwPDBDeposition:
        data = self._get(f"depositions/{dep_id}")
        data["dep_id"] = data.pop("id")
        return WwPDBDeposition(**data)

    def get_all_depositions(self) -> list[WwPDBDeposition]:
        data = self._get("depositions/")
        depositions = []
        for item in data.get("items", []):
            item["dep_id"] = item.pop("id")
            depositions.append(WwPDBDeposition(**item))
        return depositions

    def upload_file(
        self,
        dep_id: str,
        file_path: str,
        file_type: FileType,
        overwrite: bool = False,
        uploaded_bytes: int = 0,
        _chunk_size: int | None = None,
    ) -> DepositedFile:
        if not os.path.exists(file_path):
            raise ApiError("Invalid input file", 404)

        file_type_str = file_type.value if isinstance(file_type, FileType) else file_type
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        chunk_size = _chunk_size if _chunk_size is not None else self._compute_chunk_size(file_size)
        checksum = self._compute_md5(file_path, chunk_size)
        form = {"name": file_name, "type": file_type_str, "md5": checksum}

        if overwrite:
            for existing in self.get_files(dep_id):
                if existing.file_type.value == file_type_str:
                    self.remove_file(dep_id, existing.file_id)
        if uploaded_bytes >= file_size:
            raise ApiError("uploaded_bytes is already >= file size", 400)

        endpoint = f"depositions/{dep_id}/files/"
        last_data: dict | None = None

        self._refresh_auth_header()
        self._logger.info("Uploading %s (%d bytes)", file_name, file_size)

        with open(file_path, "rb") as fp:
            fp.seek(uploaded_bytes)
            while uploaded_bytes < file_size:
                self._refresh_auth_header()

                chunk_start = uploaded_bytes
                chunk = fp.read(chunk_size)
                if not chunk:
                    break
                chunk_end = chunk_start + len(chunk) - 1
                try:
                    response = self._session.request(
                        method="POST",
                        url=self._base_url + endpoint,
                        headers={"Content-Range": f"bytes {chunk_start}-{chunk_end}/{file_size}"},
                        data=form,
                        files={"file": (file_name, chunk, "application/octet-stream")},
                        timeout=300,
                    )
                except requests.exceptions.RequestException as e:
                    raise ApiUnreachableError() from e

                data_out = self._check_response(response)

                if isinstance(data_out, dict) and self._handle_redirect(data_out):
                    fp.seek(chunk_start)
                    continue

                last_data = data_out
                next_uploaded_bytes = data_out.get("uploadedBytes", chunk_end + 1)
                if not isinstance(next_uploaded_bytes, int) or isinstance(next_uploaded_bytes, bool):
                    raise ApiError("Invalid uploadedBytes in response", 502)
                if not chunk_start < next_uploaded_bytes <= file_size:
                    raise ApiError("Invalid uploadedBytes in response", 502)
                uploaded_bytes = next_uploaded_bytes
                if uploaded_bytes < file_size:
                    fp.seek(uploaded_bytes)

        self._logger.info("Uploaded %d/%d bytes", uploaded_bytes, file_size)

        if last_data is None:
            raise ApiError("No response received during upload", 500)

        last_data["file_type"] = last_data.pop("type")
        last_data["file_id"] = last_data.pop("id")
        return DepositedFile(**last_data)

    def update_metadata(
        self,
        dep_id: str,
        file_id: int,
        spacing_x: float,
        spacing_y: float,
        spacing_z: float,
        contour: float,
        description: str,
    ) -> DepositedFile:
        body = {
            "voxel": {
                "spacing": {"x": spacing_x, "y": spacing_y, "z": spacing_z},
                "contour": contour,
            },
            "description": description,
        }
        data = self._post(f"depositions/{dep_id}/files/{file_id}/metadata", data=body)
        data["file_type"] = data.pop("type")
        data["file_id"] = data.pop("id")
        return DepositedFile(**data)

    def get_files(self, dep_id: str) -> list[DepositedFile]:
        data = self._get(f"depositions/{dep_id}/files/")
        result = []
        for f in data.get("files", []):
            f = dict(f)
            f["file_type"] = f.pop("type", f.get("file_type"))
            f["file_id"] = f.pop("id", f.get("file_id"))
            result.append(DepositedFile(**f))
        return result

    def remove_file(self, dep_id: str, file_id: int) -> bool:
        self._delete(f"depositions/{dep_id}/files/{file_id}")
        return True

    def get_status(self, dep_id: str) -> Union[DepositStatus, DepositError]:
        data = self._get(f"depositions/{dep_id}/status")
        if "action" in data:
            return DepositStatus(**data)
        return DepositError(**data)

    def process(self, dep_id: str) -> Union[DepositStatus, DepositError]:
        data = self._post(f"depositions/{dep_id}/process", data={})
        if "action" in data:
            return DepositStatus(**data)
        return DepositError(**data)
