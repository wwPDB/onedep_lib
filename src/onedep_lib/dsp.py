from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from onedep_lib.apis.deposit.client import HttpApiClient
from onedep_lib.apis.deposit.models import DepositError, DepositStatus, Experiment, DepositedFile
from onedep_lib.apis.deposit.types import ApiClient
from onedep_lib.checks.report import CheckReport
from onedep_lib.checks.runner import CheckRunner
from onedep_lib.checks.types import CheckRunner as CheckRunnerProtocol
from onedep_lib.config import DepositConfig
from onedep_lib.enums import Country, EMSubType, ExperimentType, FileType
from onedep_lib.schemas.local import LocalSchemaProvider
from onedep_lib.schemas.remote import RemoteSchemaProvider
from onedep_lib.session.json_store import JsonSessionStore
from onedep_lib.session.models import LocalFile, LocalSession
from onedep_lib.session.types import SessionStore
from onedep_lib.auths.token import TokenStore
from onedep_lib.exceptions import OneDepError

import logging
logging.basicConfig(level=logging.INFO)


def _md5_of_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def list_sessions(base_dir: Path | None = None) -> list[tuple[LocalSession, list[LocalFile]]]:
    """Return all local sessions with their registered files, newest first."""
    config = DepositConfig.load()
    _base = base_dir or config.session_dir
    if not _base.exists():
        return []

    results: list[tuple[LocalSession, list[LocalFile]]] = []
    for entry in sorted(_base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        json_path = entry / "session.json"
        if not json_path.exists():
            continue
        try:
            store = JsonSessionStore(entry.name, base_dir=_base)
            session = store.get_session()
            files = store.get_all_files()
            store.close()
            results.append((session, files))
        except Exception:  # noqa: BLE001
            continue

    return results


def check_auth_key(config: DepositConfig = None) -> bool:
    """Return True if the configured credentials are valid, False otherwise."""
    config = config or DepositConfig.load()

    try:
        api_client = HttpApiClient(config, auth_provider=TokenStore(config))
        api_client.get_all_depositions()
        return True
    except Exception:  # noqa: BLE001
        return False


def deposit_init(
    email: str,
    users: list[str],
    country: Country,
    experiment_type: ExperimentType | None = None,
    em_subtype: EMSubType | None = None,
    coordinates: bool | None = None,
    config: DepositConfig | None = None,
    _base_dir: Path | None = None,
    _api_client: ApiClient | None = None,
    _check_runner: CheckRunnerProtocol | None = None,
) -> Deposition:
    """Create a new local deposition session.

    Args:
        email: Depositor e-mail address.
        users: List of ORCID IDs granted access to this deposition.
        country: Depositor country (use the Country enum).
        experiment_type: Experiment type (can be set later via set_experiment_type).
        em_subtype: EM experiment subtype (can be set later via set_em_params).
        coordinates: Whether coordinates are being deposited (can be set later).
        config: Optional pre-built DepositConfig; loaded from default sources if None.
        _base_dir: Override session storage directory (for testing only).
        _api_client: Override API client (for testing only).
        _check_runner: Override check runner (for testing only).

    Returns:
        A Deposition object representing the local session.
    """
    config = config or DepositConfig.load()
    session_id = str(uuid.uuid4())
    base_dir = _base_dir or config.session_dir
    store: SessionStore = JsonSessionStore(session_id, base_dir=base_dir)
    api_client: ApiClient = _api_client or HttpApiClient(config, auth_provider=TokenStore(config))
    check_runner: CheckRunnerProtocol = _check_runner or CheckRunner(
        LocalSchemaProvider(config.local_schema_cache_dir)
        if config.fetch_local_schema
        else RemoteSchemaProvider(config.schema_base_url, config.schema_cache_dir)
    )
    session = LocalSession(
        session_id=session_id,
        email=email,
        users=users,
        country=country,
        experiment_type=experiment_type,
        created_at=datetime.now(),
        em_subtype=em_subtype,
        coordinates=coordinates,
    )
    store.create_session(session)
    return Deposition(store=store, api_client=api_client, check_runner=check_runner)


def deposit_resume(
    session_id: str,
    config: DepositConfig | None = None,
    _base_dir: Path | None = None,
    _api_client: ApiClient | None = None,
    _check_runner: CheckRunnerProtocol | None = None,
) -> Deposition:
    """Resume an existing local deposition session.

    Args:
        session_id: The session_id returned by a previous deposit_init() call.
        config: Optional pre-built DepositConfig; loaded from default sources if None.
        _base_dir: Override session storage directory (for testing only).
        _api_client: Override API client (for testing only).
        _check_runner: Override check runner (for testing only).

    Returns:
        A Deposition object for the existing session.

    Raises:
        KeyError: If no session with the given session_id exists.
    """
    config = config or DepositConfig.load()
    base_dir = _base_dir or config.session_dir
    store: SessionStore = JsonSessionStore(session_id, base_dir=base_dir)
    store.get_session()  # raises KeyError if not found
    api_client: ApiClient = _api_client or HttpApiClient(config, auth_provider=TokenStore(config))
    check_runner: CheckRunnerProtocol = _check_runner or CheckRunner(
        LocalSchemaProvider(config.local_schema_cache_dir)
        if config.fetch_local_schema
        else RemoteSchemaProvider(config.schema_base_url, config.schema_cache_dir)
    )
    return Deposition(store=store, api_client=api_client, check_runner=check_runner)


class Deposition:
    """Local deposition session. Created via deposit_init() or deposit_resume()."""

    def __init__(
        self,
        store: SessionStore,
        api_client: ApiClient,
        check_runner: CheckRunnerProtocol,
    ) -> None:
        self._store = store
        self._api_client = api_client
        self._check_runner = check_runner
        self._session = store.get_session()

    @property
    def session_id(self) -> str:
        """Unique ID of the local session."""
        return self._session.session_id

    @property
    def remote_dep_id(self) -> str | None:
        """Remote deposition ID, populated after deposit() is called."""
        return self._session.remote_dep_id

    @property
    def site_url(self) -> str | None:
        """Remote deposition site URL, populated after deposit() is called."""
        return self._session.site_url

    def set_experiment_type(self, experiment_type: ExperimentType) -> None:
        """Set or update the experiment type for this deposition."""
        self._store.update_experiment_type(experiment_type)
        self._session.experiment_type = experiment_type

    def set_em_params(
        self,
        em_subtype: EMSubType | None = None,
        coordinates: bool | None = None,
    ) -> None:
        """Set EM-specific parameters for this deposition."""
        self._store.update_em_params(em_subtype, coordinates)
        self._session.em_subtype = em_subtype
        self._session.coordinates = coordinates

    def add_file(self, file_path: str, file_type: FileType) -> str:
        """Register a local file for this deposition.

        Returns:
            A file_id (UUID string) to reference this file in check methods.

        Raises:
            FileNotFoundError: If file_path does not exist.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        stat = path.stat()
        file_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        md5 = _md5_of_file(path)
        file_id = str(uuid.uuid4())
        local_file = LocalFile(
            file_id=file_id,
            session_id=self._session.session_id,
            file_path=str(path.resolve()),
            file_type=file_type,
            md5=md5,
            file_mtime=file_mtime,
        )
        self._store.add_file(local_file)
        return file_id

    def has_file(self, file_path: str) -> bool:
        """Check if file has already been registered for this deposition."""
        return self._store.has_file(file_path)

    def _upload_file(self, file_path: str, file_type: FileType) -> DepositedFile:
        """Register and upload a file after the deposit function has already run.
        For testing only.
        """
        if self.remote_dep_id is None:
            raise OneDepError("processing not yet started for this deposition")
        if not Path(file_path).exists():
            raise OneDepError("file not found")
        if not self.has_file(file_path):
            self.add_file(file_path, file_type)
        result = self._api_client.upload_file(self.remote_dep_id, file_path, file_type)
        if result is None:
            raise OneDepError("failed to upload file")
        return result

    def _process_file(self, file_id: str) -> DepositStatus:
        """Process a file after the deposit function has already run.
        For testing only.
        """
        if self.remote_dep_id is None:
            raise OneDepError("processing not yet started for this deposition")
        processed = self._api_client.process(self.remote_dep_id)
        if processed is None:
            raise OneDepError("failed to process deposition")
        if isinstance(processed, DepositError):
            raise OneDepError(processed.message)
        elif isinstance(processed, DepositStatus):
            logging.info("processing status %s action %s step %s details %s", processed.status, processed.action, processed.step, processed.details)
        return processed

    def remove_file(self, file_id: str) -> None:
        """Remove a file from this local session by its file_id."""
        self._store.remove_file(file_id)

    def _remove_remote_file(self, file_id: str) -> None:
        """Remove both local file and remote file, but not if file has already been processed.
        For testing only.
        """
        if self.remote_dep_id is not None:
            try:
                status = self.get_status()
                if isinstance(status, DepositError):
                    raise OneDepError(status.message)
                if status.status not in ["error", "running"]:
                    logging.warning(
                        f"Cannot remove file {file_id} from session {self.session_id} because deposition is already processed: {status.status}"
                    )
                    return
            except RuntimeError as e:
                logging.error("Deposit has not been started yet.")
                return
            filename = os.path.basename(self._store.get_file(file_id).file_path)
            depositedfiles = self._api_client.get_files(self.remote_dep_id)
            for f in depositedfiles:
                if f.name == filename:
                    remote_file_id = f.file_id
                    self._api_client.remove_file(self.remote_dep_id, remote_file_id)
                    logging.info(f"Removed remote file {remote_file_id} from deposition {self.remote_dep_id}")
                    self._store.remove_file(file_id)
                    logging.info(f"Removed local file {file_id} from session {self.session_id}")
                    return
            logging.warning(f"File {file_id} not found in deposition {self.remote_dep_id}")

    def set_voxel_values(
        self,
        file_id: str,
        spacing_x: float,
        spacing_y: float,
        spacing_z: float,
        contour: float,
    ) -> None:
        """Set voxel spacing and contour level for a map file."""
        self._store.set_voxel_values(file_id, spacing_x, spacing_y, spacing_z, contour)

    def check_required_files(self) -> CheckReport:
        """Check that the session contains all required files for the experiment type."""
        files = self._store.get_all_files()
        return self._check_runner.check_required_files(files, self._session.experiment_type, self._session.em_subtype)

    def check_mmcif_file(self, file_id: str) -> CheckReport:
        """Check that the file identified by file_id is a valid mmCIF."""
        file = self._store.get_file(file_id)
        return self._check_runner.check_mmcif_file(file)

    def check_mmcif_category(self, file_id: str, category: str) -> CheckReport:
        """Check that the mmCIF file contains the given category."""
        file = self._store.get_file(file_id)
        return self._check_runner.check_mmcif_category(file, category)

    def check_mmcif_field(self, file_id: str, category: str, field: str) -> CheckReport:
        """Check that the mmCIF file contains the given field in the given category."""
        file = self._store.get_file(file_id)
        return self._check_runner.check_mmcif_field(file, category, field)

    def check_file_type(self, file_id: str, file_type: FileType) -> CheckReport:
        """Check that the file matches the expected FileType."""
        file = self._store.get_file(file_id)
        return self._check_runner.check_file_type(file, file_type)

    def deposit(self) -> str:
        """Submit this deposition to the OneDep API.

        Creates a remote deposition, uploads all registered files, and triggers
        processing. Returns immediately without waiting for processing to finish.

        Returns:
            The remote deposition ID (e.g. "D_8000000001").

        Raises:
            ValueError: If experiment_type has not been set.
            ApiError: If any API call fails.
        """
        if self._session.experiment_type is None:
            raise ValueError(
                "experiment_type must be set before calling deposit(). "
                "Use set_experiment_type() or pass experiment_type to deposit_init()."
            )
        if self._session.remote_dep_id is None:
            experiment = Experiment(
                exp_type=self._session.experiment_type,
                coordinates=self._session.coordinates if self._session.coordinates is not None else True,
                subtype=self._session.em_subtype,
            )
            remote_dep = self._api_client.create_deposition(
                email=self._session.email,
                users=self._session.users,
                country=self._session.country,
                experiments=[experiment],
            )
            dep_id = remote_dep.dep_id
            self._store.set_remote_dep_id(dep_id, site_url=remote_dep.site_url)
            self._session.remote_dep_id = dep_id
            self._session.site_url = remote_dep.site_url
        else:
            dep_id = self._session.remote_dep_id

        for file in self._store.get_all_files():
            deposited = self._api_client.upload_file(dep_id, file.file_path, file.file_type)
            if file.voxel:
                v = file.voxel
                self._api_client.update_metadata(
                    dep_id,
                    deposited.file_id,
                    spacing_x=v["spacing_x"],
                    spacing_y=v["spacing_y"],
                    spacing_z=v["spacing_z"],
                    contour=v["contour"],
                    description="",
                )

        self._api_client.process(dep_id)
        return dep_id

    def get_status(self) -> DepositStatus | DepositError:
        """Return the current processing status of the remote deposition.

        Raises:
            RuntimeError: If deposit() has not been called yet.
        """
        if self._session.remote_dep_id is None:
            raise RuntimeError(
                "deposit() has not been called yet for this session. "
                "Call deposit() first to obtain a remote deposition ID."
            )
        return self._api_client.get_status(self._session.remote_dep_id)

    def get_experiment_file_types(self) -> list[FileType]:
        """Return the accepted file types for the current experiment type."""
        session = self._session
        if not session or not session.experiment_type:
            return []
        exptype = session.experiment_type.value
        if exptype is None:
            return []
        subschemas = CheckRunner.subschemas
        if exptype not in subschemas:
            return []
        provider = LocalSchemaProvider(DepositConfig().local_schema_cache_dir)
        schema = provider.get_schema(exptype)
        filetypes = schema.get("enum", [])
        return filetypes

    def close(self) -> None:
        """Close the underlying session store connection."""
        self._store.close()

    def __enter__(self) -> Deposition:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
