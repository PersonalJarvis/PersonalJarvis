"""Cancelable, verified model acquisition with immutable package identities.

Downloads/copies weights only. It neither starts an inference engine nor changes
the user's active voice. Failed changes therefore leave the previous model usable.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlsplit

from jarvis.core.http_pool import SyncHttpClientPool

from .models import FrozenModel, LocalModelManifest, ModelArtifact
from .packages import _linked, _verify_artifact, verify_package

_HTTP = SyncHttpClientPool(timeout_s=30.0)
_CHUNK_BYTES = 1024 * 1024
_DISK_HEADROOM_BYTES = 64 * 1024 * 1024


class ModelAcquisitionError(RuntimeError):
    """A package was not published; existing complete packages remain intact."""


class ModelAcquisitionCancelled(ModelAcquisitionError):
    """Cancellation stopped this acquisition before publishing its receipt."""


class AcquisitionProgress(FrozenModel):
    model_id: str
    artifact: str
    completed_bytes: int
    total_bytes: int


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise ModelAcquisitionCancelled("Model download cancelled.")


def _directory(root: Path, relative: str) -> Path:
    """Create only real directories beneath the app-owned store root."""
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.exists() or current.is_symlink():
            if _linked(current.lstat()) or not current.is_dir():
                raise ModelAcquisitionError("The model storage directory is unsafe.")
        else:
            current.mkdir()
    return current


def _download(url: str, cancel: threading.Event) -> Iterator[bytes]:
    # Model URLs are constructed from immutable Hub identities. Validate each
    # redirect BEFORE following it, including Hub's public artifact CDN.
    client = _HTTP.client()
    for _ in range(8):
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or not (
                host == "huggingface.co"
                or host.endswith(".huggingface.co")
                or host == "hf.co"
                or host.endswith(".hf.co")
            )
        ):
            raise ModelAcquisitionError("The model download redirected outside its artifact host.")
        _check_cancel(cancel)
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                from urllib.parse import urljoin

                location = response.headers.get("location")
                if not location:
                    raise ModelAcquisitionError("The model download returned an invalid redirect.")
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                raise ModelAcquisitionError(f"Model download failed (HTTP {response.status_code}).")
            for chunk in response.iter_bytes(chunk_size=_CHUNK_BYTES):
                _check_cancel(cancel)
                yield chunk
            return
    raise ModelAcquisitionError("The model download exceeded its redirect limit.")


def _copy(path: Path, cancel: threading.Event) -> Iterator[bytes]:
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_BYTES):
            _check_cancel(cancel)
            yield chunk


def _write_verified(
    chunks: Iterator[bytes],
    target: Path,
    artifact: ModelArtifact,
    cancel: threading.Event,
    report: Callable[[int], None],
) -> None:
    digest = hashlib.sha256()
    count = 0
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".download-", delete=False
        ) as file:
            temporary = Path(file.name)
            for chunk in chunks:
                _check_cancel(cancel)
                count += len(chunk)
                if count > artifact.size_bytes:
                    raise ModelAcquisitionError("Downloaded file exceeds the expected size.")
                file.write(chunk)
                digest.update(chunk)
                report(count)
            if count != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
                raise ModelAcquisitionError("Downloaded model did not match its size and checksum.")
            _check_cancel(cancel)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)
    finally:
        close = getattr(chunks, "close", None)
        try:
            if callable(close):
                close()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def acquire_model(
    manifest: LocalModelManifest,
    store_root: Path,
    *,
    local_directory: Path | None = None,
    cancel: threading.Event | None = None,
    progress: Callable[[AcquisitionProgress], None] | None = None,
) -> Path:
    """Acquire a package off the audio/request loop and return its verified weights.

    Reuses already-verified files after interruption. A separate runtime owner
    must qualify audio/tools and transactionally activate the returned package.
    """
    import httpx
    from filelock import FileLock, Timeout

    cancel = cancel if cancel is not None else threading.Event()
    _check_cancel(cancel)
    if manifest.source.kind == "local":
        if local_directory is None:
            raise ModelAcquisitionError("Choose the directory containing your model files.")
        if not verify_package(manifest, local_directory).verified:
            raise ModelAcquisitionError("The custom model files failed verification.")
    elif local_directory is not None:
        raise ModelAcquisitionError("A downloaded model cannot use a local source override.")
    store_root.mkdir(parents=True, exist_ok=True)
    root = store_root.resolve()
    identity = _directory(root, manifest.id)
    lock_path = identity / ".acquire.lock"
    if lock_path.exists() or lock_path.is_symlink():
        metadata = lock_path.lstat()
        if _linked(metadata) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ModelAcquisitionError("The model storage lock is unsafe.")
    try:
        with FileLock(lock_path, timeout=0):
            package = _directory(identity, manifest.fingerprint)
            weights = _directory(package, "weights")
            existing = verify_package(manifest, weights)
            remaining = sum(
                item.size_bytes
                for item, check in zip(manifest.artifacts, existing.artifacts, strict=True)
                if check.state != "verified"
            )
            if remaining and shutil.disk_usage(root).free < remaining + _DISK_HEADROOM_BYTES:
                raise ModelAcquisitionError("Not enough free disk space for this model download.")
            completed = 0
            for artifact, check in zip(manifest.artifacts, existing.artifacts, strict=True):
                _check_cancel(cancel)
                parent_parts = artifact.path.rpartition("/")[0]
                parent = _directory(weights, parent_parts) if parent_parts else weights
                target = parent / artifact.path.rpartition("/")[2]
                if check.state == "unsafe":
                    raise ModelAcquisitionError("An existing model file is unsafe.")
                if check.state != "verified":
                    if local_directory is not None:
                        # Recheck containment after preflight, just before opening.
                        if (
                            _verify_artifact(local_directory.resolve(), artifact).state
                            != "verified"
                        ):
                            raise ModelAcquisitionError("The custom model changed during import.")
                        chunks = _copy(local_directory / artifact.path, cancel)
                    else:
                        chunks = _download(manifest.artifact_url(artifact), cancel)

                    def report(
                        count: int, *, artifact_path: str = artifact.path, offset: int = completed
                    ) -> None:
                        if progress is not None:
                            progress(
                                AcquisitionProgress(
                                    model_id=manifest.id,
                                    artifact=artifact_path,
                                    completed_bytes=offset + count,
                                    total_bytes=manifest.download_bytes,
                                )
                            )

                    _write_verified(chunks, target, artifact, cancel, report)
                completed += artifact.size_bytes
            _check_cancel(cancel)
            # Publish the manifest only after every file was verified. There is
            # intentionally no "ready" marker: inference is a separate proof.
            data = manifest.model_dump_json(indent=2).encode("utf-8")
            receipt = ModelArtifact(
                path="manifest.json", size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
            )
            _write_verified(
                iter([data]), package / "manifest.json", receipt, cancel, lambda _: None
            )
            return weights
    except Timeout as exc:
        raise ModelAcquisitionError("This model is already being downloaded or imported.") from exc
    except httpx.HTTPError as exc:
        raise ModelAcquisitionError(
            "The model download connection failed; retry the download."
        ) from exc
