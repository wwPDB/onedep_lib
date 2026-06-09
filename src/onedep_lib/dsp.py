from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from onedep_lib.apis.deposit.client import HttpApiClient
from onedep_lib.apis.deposit.models import DepositError, DepositStatus, Experiment
from onedep_lib.apis.deposit.types import ApiClient
from onedep_lib.checks.report import CheckReport
from onedep_lib.checks.runner import CheckRunner
from onedep_lib.checks.types import CheckRunner as CheckRunnerProtocol
from onedep_lib.config import DepositConfig
from onedep_lib.enums import Country, EMSubType, ExperimentType, FileType
from onedep_lib.schemas.remote import RemoteSchemaProvider
from onedep_lib.session.json_store import JsonSessionStore
from onedep_lib.session.models import LocalFile, LocalSession
from onedep_lib.session.types import SessionStore
from onedep_lib.auths.token import TokenStore


def _md5_of_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def list_sessions(base_dir: Path | None = None) -> list[tuple[LocalSession, list[LocalFile]]]:
    """Return all local sessions with their registered files, newest first."""
    _base = base_dir or (Path.home() / ".onedep" / "sessions")
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
        RemoteSchemaProvider(config.schema_base_url, config.schema_cache_dir)
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
        RemoteSchemaProvider(config.schema_base_url, config.schema_cache_dir)
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

    def check_auth_key(self) -> bool:
        """Return True if the configured credentials are valid, False otherwise."""
        try:
            self._api_client.get_all_depositions()
            return True
        except Exception:  # noqa: BLE001
            return False

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

    def remove_file(self, file_id: str) -> None:
        """Remove a file from this local session by its file_id."""
        self._store.remove_file(file_id)

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
            self._store.set_remote_dep_id(dep_id)
            self._session.remote_dep_id = dep_id
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
        """Return the accepted file types for the current experiment type. (stub)"""
        return []

    def close(self) -> None:
        """Close the underlying session store connection."""
        self._store.close()

    def __enter__(self) -> Deposition:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
