from __future__ import annotations

import logging
import os
from json import JSONDecodeError
from typing import Union

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
from onedep_lib.exceptions import ApiError


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
        self._base_url = f"{config.hostname}/api/{ver}/"
        if not config.ssl_verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._session = requests.Session()
        self._session.verify = config.ssl_verify

    def _refresh_auth_header(self) -> None:
        if self._auth_provider is not None:
            token = self._auth_provider.get_access_token()
        else:
            token = self._config.access_token or ""
        self._session.headers["Authorization"] = f"Bearer {token}"

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
            raise ApiError("Failed to access the API", 403) from e

        data_out = self._check_response(response)

        if (
            isinstance(data_out, dict)
            and data_out.get("code") == "invalid_location"
            and "base_url" in data_out.get("extras", {})
        ):
            new_base = data_out["extras"]["base_url"]
            self._logger.warning("Invalid deposit site, redirecting to %s", new_base)
            if not self._config.redirect:
                raise ApiError(f"Invalid deposit site; correct site is {new_base}", 400)
            self._base_url = f"{new_base}/api/{self._ver}/"
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
                raise ApiError("Retry after redirect failed", 503) from e
            data_out = self._check_response(response)

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
        _chunk_size: int = 8 * 1024 * 1024,
    ) -> DepositedFile:
        if not os.path.exists(file_path):
            raise ApiError("Invalid input file", 404)

        file_type_str = file_type.value if isinstance(file_type, FileType) else file_type
        file_name = os.path.basename(file_path)
        form = {"name": file_name, "type": file_type_str}

        if overwrite:
            for existing in self.get_files(dep_id):
                if existing.file_type.value == file_type_str:
                    self.remove_file(dep_id, existing.file_id)

        file_size = os.path.getsize(file_path)
        if uploaded_bytes >= file_size:
            raise ApiError("uploaded_bytes is already >= file size", 400)

        endpoint = f"depositions/{dep_id}/files/"
        last_data: dict | None = None

        self._refresh_auth_header()
        self._logger.info("Uploading %s", file_name)

        with open(file_path, "rb") as fp:
            fp.seek(uploaded_bytes)
            while uploaded_bytes < file_size:
                chunk_start = uploaded_bytes
                chunk = fp.read(_chunk_size)
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
                    raise ApiError("Failed to access the API", 403) from e

                data_out = self._check_response(response)

                if (
                    isinstance(data_out, dict)
                    and data_out.get("code") == "invalid_location"
                    and "base_url" in data_out.get("extras", {})
                ):
                    new_base = data_out["extras"]["base_url"]
                    self._logger.warning("Invalid deposit site, redirecting to %s", new_base)
                    if not self._config.redirect:
                        raise ApiError(f"Invalid deposit site; correct site is {new_base}", 400)
                    self._base_url = f"{new_base}/api/{self._ver}/"
                    fp.seek(chunk_start)
                    continue

                last_data = data_out
                uploaded_bytes = data_out.get("uploadedBytes", chunk_end + 1)
                self._logger.info("Uploaded %d/%d bytes", uploaded_bytes, file_size)

        if last_data is None:
            raise ApiError("No response received during upload", 500)

        last_data.pop("uploadedBytes", None)
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
