"""Explicit, per-user native Codex provisioning for the existing OAuth login.

Only an audited native executable is extracted from OpenAI's pinned npm
artifact. Node/npm, provider inference, credential writes and execution of
downloaded code are deliberately outside this installer.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import platform
import stat
import sys
import tarfile
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.http_pool import SyncHttpClientPool
from jarvis.core.paths import user_data_dir

_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_BINARY_BYTES = 512 * 1024 * 1024
_MAX_INFLATED_BYTES = 768 * 1024 * 1024
_INSTALL_TIMEOUT_S = 180.0
_CHUNK_BYTES = 64 * 1024
_INSTALL_LOCK = threading.Lock()


class LoginHelperError(RuntimeError):
    """A sanitized provisioning failure suitable for the settings surface."""

    def __init__(self, code: str, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class NativeArtifact:
    version: str
    tag: str
    target: str
    executable: str
    sha256: str

    @property
    def url(self) -> str:
        return f"https://registry.npmjs.org/@openai/codex/-/codex-{self.version}-{self.tag}.tgz"

    @property
    def member(self) -> str:
        return f"package/vendor/{self.target}/bin/{self.executable}"


def native_artifact() -> NativeArtifact:
    """Reuse the audited native release pins; never select an unpinned latest."""
    from jarvis.codex_app_server import _SUPPORTED_CODEX_VERSION, _TRUSTED_CODEX_TARGETS

    machine = platform.machine().lower()
    architecture = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    operating_system = "linux" if sys.platform.startswith("linux") else sys.platform
    target = _TRUSTED_CODEX_TARGETS.get((operating_system, architecture))
    if target is None:
        raise LoginHelperError(
            "unsupported_platform",
            "The native login helper is unavailable for this platform.",
            status=409,
        )
    tag, triple, executable, digest = target
    return NativeArtifact(
        _SUPPORTED_CODEX_VERSION.removeprefix("codex-cli "),
        tag,
        triple,
        executable,
        digest,
    )


def _check(cancel: threading.Event, deadline: float) -> None:
    if cancel.is_set():
        raise LoginHelperError("cancelled", "Login helper installation was cancelled.", status=499)
    if time.monotonic() >= deadline:
        raise LoginHelperError("timeout", "Login helper installation timed out. Try again later.")


def _safe_path(path: Path, *, directory: bool = False, missing: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return
        raise LoginHelperError(
            "unsafe_location", "The login helper location is unavailable."
        ) from None
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or (directory and not stat.S_ISDIR(info.st_mode))
        or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
    ):
        raise LoginHelperError("unsafe_location", "The login helper location is unsafe.")


def _prepare_directory(root: Path) -> Path:
    root = root.absolute()
    for ancestor in reversed((root, *root.parents)):
        _safe_path(ancestor, directory=True, missing=True)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = root / "login-helpers" / "codex"
    for directory in (destination.parent, destination):
        _safe_path(directory, directory=True, missing=True)
        directory.mkdir(mode=0o700, exist_ok=True)
    _safe_path(destination, directory=True)
    return destination


def _existing_binary() -> str | None:
    from jarvis.codex_auth import CodexAuthService

    # Resolution is stat/PATH only; no --version or other subprocess is run.
    return CodexAuthService()._resolve_binary()


def _file_identity(path: Path) -> tuple[int, int, int, int, int] | None:
    if not path.exists():
        return None
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def find_installed_helper() -> str | None:
    """Return only an intact private login binary, never advertise a coding CLI."""
    try:
        artifact = native_artifact()
        directory = user_data_dir().absolute() / "login-helpers" / "codex"
        destination = directory / artifact.executable
        if not destination.exists():
            return None
        for ancestor in reversed((directory, *directory.parents)):
            _safe_path(ancestor, directory=True)
        _safe_path(destination)
        if not 0 < destination.stat().st_size <= _MAX_BINARY_BYTES:
            return None
        digest = hashlib.sha256()
        with destination.open("rb") as binary:
            while chunk := binary.read(_CHUNK_BYTES):
                digest.update(chunk)
        return str(destination) if digest.hexdigest() == artifact.sha256 else None
    except (LoginHelperError, OSError):
        # Availability is a local capability answer; invalid/absent files are
        # never executed and never reported as an installed helper.
        return None


def login_helper_status() -> dict[str, Any]:
    """Local-only login availability; call off the event loop because hashing is I/O."""
    if _existing_binary():
        return {"status": "ready", "installed": True, "source": "existing", "version": ""}
    if find_installed_helper():
        return {
            "status": "ready",
            "installed": True,
            "source": "private",
            "version": native_artifact().version,
        }
    return {"status": "missing", "installed": False, "source": "missing", "version": ""}


class _InflatedReader:
    def __init__(self, stream: Any, cancel: threading.Event, deadline: float) -> None:
        self.stream, self.cancel, self.deadline = stream, cancel, deadline
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        _check(self.cancel, self.deadline)
        data = self.stream.read(min(size, _CHUNK_BYTES) if size >= 0 else _CHUNK_BYTES)
        self.total += len(data)
        if self.total > _MAX_INFLATED_BYTES:
            raise LoginHelperError("archive_too_large", "The login helper archive is too large.")
        _check(self.cancel, self.deadline)
        return data


def _extract_binary(
    archive: Path,
    output: Any,
    artifact: NativeArtifact,
    cancel: threading.Event,
    deadline: float,
) -> None:
    digest = hashlib.sha256()
    found = False
    with archive.open("rb") as packed, gzip.GzipFile(fileobj=packed) as unpacked:
        reader = _InflatedReader(unpacked, cancel, deadline)
        with tarfile.open(fileobj=reader, mode="r|") as package:
            advertised_bytes = 0
            for member in package:
                _check(cancel, deadline)
                advertised_bytes += member.size
                if member.size < 0 or advertised_bytes > _MAX_INFLATED_BYTES:
                    raise LoginHelperError(
                        "archive_too_large", "The login helper archive is too large."
                    )
                if member.name != artifact.member:
                    continue  # No archive path other than the exact binary is ever written.
                if (
                    found
                    or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                    or not 0 < member.size <= _MAX_BINARY_BYTES
                ):
                    raise LoginHelperError(
                        "invalid_archive", "The login helper archive is invalid."
                    )
                source = package.extractfile(member)
                if source is None:
                    raise LoginHelperError(
                        "invalid_archive", "The login helper archive is invalid."
                    )
                total = 0
                with source:
                    while chunk := source.read(_CHUNK_BYTES):
                        _check(cancel, deadline)
                        total += len(chunk)
                        if total > _MAX_BINARY_BYTES:
                            raise LoginHelperError(
                                "archive_too_large", "The login helper is too large."
                            )
                        output.write(chunk)
                        digest.update(chunk)
                if total != member.size:
                    raise LoginHelperError(
                        "invalid_archive", "The login helper archive is incomplete."
                    )
                found = True
    if not found or digest.hexdigest() != artifact.sha256:
        raise LoginHelperError(
            "verification_failed", "The login helper failed integrity verification."
        )


def install_login_helper(
    cancel_event: threading.Event,
    *,
    root: Path | None = None,
    artifact: NativeArtifact | None = None,
    http_pool: Any = None,
) -> dict[str, Any]:
    """Install once after an explicit settings action; cancellation cleans all staging files."""
    import httpx
    from filelock import FileLock, Timeout

    deadline = time.monotonic() + _INSTALL_TIMEOUT_S
    _check(cancel_event, deadline)
    selected = artifact or native_artifact()
    if not _INSTALL_LOCK.acquire(blocking=False):
        raise LoginHelperError("busy", "The login helper is already being installed.", status=409)
    pool = http_pool or SyncHttpClientPool(timeout_s=5.0)
    temporary_paths: list[Path] = []
    try:
        if _existing_binary():
            return {"status": "ready", "installed": False, "version": ""}
        directory = _prepare_directory(root or user_data_dir())
        identity = (directory.stat().st_dev, directory.stat().st_ino)
        destination = directory / selected.executable
        _safe_path(destination, missing=True)
        lock_path = directory / ".codex-install.lock"
        _safe_path(lock_path, missing=True)
        with FileLock(str(lock_path), timeout=0):
            _safe_path(lock_path)
            _check(cancel_event, deadline)
            if _existing_binary():
                return {"status": "ready", "installed": False, "version": ""}
            previous_binary = _file_identity(destination)
            if destination.exists():
                digest = hashlib.sha256()
                if destination.stat().st_size <= _MAX_BINARY_BYTES:
                    with destination.open("rb") as binary:
                        while chunk := binary.read(_CHUNK_BYTES):
                            _check(cancel_event, deadline)
                            digest.update(chunk)
                if digest.hexdigest() == selected.sha256:
                    return {"status": "ready", "installed": False, "version": selected.version}
                # A damaged private helper can be repaired by the same explicit
                # install action. Preserve it until a replacement is verified.
            archive_fd, archive_name = tempfile.mkstemp(
                prefix=".codex-", suffix=".tgz", dir=directory
            )
            archive_path = Path(archive_name)
            temporary_paths.append(archive_path)
            with os.fdopen(archive_fd, "wb") as archive:
                with pool.client().stream(
                    "GET",
                    selected.url,
                    follow_redirects=False,
                    headers={"Accept-Encoding": "identity"},
                ) as response:
                    if response.status_code != 200:
                        raise LoginHelperError(
                            "download_failed", "The login helper could not be downloaded."
                        )
                    length = response.headers.get("content-length")
                    if length and (not length.isdecimal() or int(length) > _MAX_ARCHIVE_BYTES):
                        raise LoginHelperError(
                            "archive_too_large", "The login helper archive is too large."
                        )
                    total = 0
                    for chunk in response.iter_raw():
                        _check(cancel_event, deadline)
                        total += len(chunk)
                        if total > _MAX_ARCHIVE_BYTES:
                            raise LoginHelperError(
                                "archive_too_large", "The login helper archive is too large."
                            )
                        archive.write(chunk)
            binary_fd, binary_name = tempfile.mkstemp(
                prefix=".codex-", suffix=".tmp", dir=directory
            )
            binary_path = Path(binary_name)
            temporary_paths.append(binary_path)
            with os.fdopen(binary_fd, "wb") as output:
                _extract_binary(archive_path, output, selected, cancel_event, deadline)
                output.flush()
                os.fsync(output.fileno())
            _check(cancel_event, deadline)
            _safe_path(directory, directory=True)
            if (directory.stat().st_dev, directory.stat().st_ino) != identity:
                raise LoginHelperError(
                    "unsafe_location", "The login helper location changed during installation."
                )
            _safe_path(destination, missing=True)
            if _file_identity(destination) != previous_binary:
                raise LoginHelperError(
                    "install_changed", "Another login helper was installed. Try again.", status=409
                )
            os.chmod(binary_path, 0o700)
            _check(cancel_event, deadline)
            os.replace(binary_path, destination)
        return {"status": "ready", "installed": True, "version": selected.version}
    except Timeout:
        raise LoginHelperError(
            "busy", "The login helper is already being installed.", status=409
        ) from None
    except LoginHelperError:
        raise
    except (httpx.HTTPError, OSError, tarfile.TarError, zlib.error, EOFError, ValueError):
        raise LoginHelperError(
            "installation_failed", "The login helper could not be installed. Try again."
        ) from None
    finally:
        for path in temporary_paths:
            try:
                _safe_path(path.parent, directory=True)
                if (path.parent.stat().st_dev, path.parent.stat().st_ino) != identity:
                    raise LoginHelperError("unsafe_location", "The staging location changed.")
                path.unlink(missing_ok=True)
            except (OSError, LoginHelperError):
                # A locked staging file is safe to leave; it is never on PATH
                # and is never considered an installed executable.
                import logging

                logging.getLogger(__name__).warning("Login helper staging cleanup was deferred.")
        try:
            pool.close()
        finally:
            _INSTALL_LOCK.release()
