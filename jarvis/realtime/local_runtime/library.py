"""Application-owned model library and one cancelable package operation.

Receipts describe stored packages, not live inference. No active voice setting
is changed here. Download threads never run native inference or model code.
"""

from __future__ import annotations

import asyncio
import logging
import stat
import threading
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field

from .catalog import native_model_catalog
from .models import FrozenModel, LocalModelManifest
from .packages import _linked, load_manifest
from .store import AcquisitionProgress, ModelAcquisitionCancelled, acquire_model

log = logging.getLogger(__name__)


class PackageJob(FrozenModel):
    id: str
    fingerprint: str
    phase: Literal["acquiring", "cancelling", "stored", "cancelled", "failed"]
    completed_bytes: int = 0
    total_bytes: int
    error: str = ""


class VoiceLibraryEntry(FrozenModel):
    model_id: str
    fingerprint: str
    label: str
    family: str
    languages: tuple[str, ...]
    download_bytes: int
    declared_capabilities: tuple[str, ...]
    package_present: bool
    # Reserved false until the actual adapter passes audio/tool acceptance.
    runtime_qualified: Literal[False] = False
    job: PackageJob | None = None


class VoiceLibrary(FrozenModel):
    entries: tuple[VoiceLibraryEntry, ...]
    active_job: PackageJob | None = None
    custom_manifest_schema: dict = Field(default_factory=dict)


class LocalVoiceLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._models: dict[str, LocalModelManifest] = {}
        self._jobs: dict[str, PackageJob] = {}
        self._task: asyncio.Task[None] | None = None
        self._cancel = threading.Event()
        self._active = ""
        self._closed = False

    def _stored(self) -> dict[str, LocalModelManifest]:
        stored: dict[str, LocalModelManifest] = {}
        if not self.root.is_dir() or _linked(self.root.lstat()):
            return stored
        # Only two app-owned directory levels; never follow linked directories.
        for identity in self.root.iterdir():
            if not identity.is_dir() or _linked(identity.lstat()):
                continue
            for package in identity.iterdir():
                if not package.is_dir() or _linked(package.lstat()):
                    continue
                receipt = package / "manifest.json"
                if not receipt.is_file() or _linked(receipt.lstat()):
                    continue
                try:
                    model = load_manifest(receipt)
                    if model.id == identity.name and model.fingerprint == package.name:
                        stored[model.fingerprint] = model
                except (OSError, ValueError):
                    log.warning("Ignoring an unreadable local voice package receipt")
        return stored

    def snapshot(self) -> VoiceLibrary:
        stored = self._stored()
        models = {model.fingerprint: model for model in native_model_catalog()}
        models.update(stored)
        with self._lock:
            models.update(self._models)
            jobs = dict(self._jobs)
            active = jobs.get(self._active)
        return VoiceLibrary(
            entries=tuple(
                VoiceLibraryEntry(
                    model_id=model.id,
                    fingerprint=key,
                    label=model.label,
                    family=model.family,
                    languages=model.languages,
                    download_bytes=model.download_bytes,
                    declared_capabilities=tuple(sorted(model.capabilities)),
                    package_present=key in stored and self._files_present(model),
                    job=jobs.get(key),
                )
                for key, model in models.items()
            ),
            active_job=active if active and active.phase in {"acquiring", "cancelling"} else None,
            custom_manifest_schema=LocalModelManifest.model_json_schema(),
        )

    def _files_present(self, model: LocalModelManifest) -> bool:
        """Cheap inventory only; full checksums are verified by acquisition/launch."""
        root = self.root / model.id / model.fingerprint / "weights"
        try:
            if _linked(root.lstat()):
                return False
            for artifact in model.artifacts:
                current = root
                for part in artifact.path.split("/"):
                    current = current / part
                    metadata = current.lstat()
                    if _linked(metadata):
                        return False
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != artifact.size_bytes:
                    return False
            return True
        except FileNotFoundError:
            # Removed/incomplete weights are a normal inventory state.
            return False
        except OSError:
            log.warning("Local voice package files cannot be inspected")
            return False

    def start(self, model: LocalModelManifest, source: Path | None = None) -> PackageJob:
        if self._closed:
            raise ValueError("The local voice library is shutting down.")
        if self._task is not None and not self._task.done():
            raise ValueError("Wait for the current model operation to finish or cancel it.")
        if model.source.kind == "local" and source is None:
            # A stored custom package can be reverified without exposing or
            # persisting the user's original source folder in its manifest.
            source = self.root / model.id / model.fingerprint / "weights"
        if source is not None and not source.is_absolute():
            raise ValueError("The custom model folder must be an absolute path.")
        job = PackageJob(
            id=uuid.uuid4().hex,
            fingerprint=model.fingerprint,
            phase="acquiring",
            total_bytes=model.download_bytes,
        )
        with self._lock:
            # Keep bounded transient history; completed packages live on disk.
            if len(self._jobs) >= 64:
                self._jobs.clear()
                self._models.clear()
            self._models[model.fingerprint] = model
            self._jobs[model.fingerprint] = job
            self._active = model.fingerprint
        self._cancel = threading.Event()
        self._task = asyncio.create_task(self._acquire(model, source, self._cancel))
        return job

    async def _acquire(
        self, model: LocalModelManifest, source: Path | None, cancel: threading.Event
    ) -> None:
        def update(**changes: object) -> None:
            with self._lock:
                job = self._jobs[model.fingerprint]
                self._jobs[model.fingerprint] = job.model_copy(update=changes)

        def progress(value: AcquisitionProgress) -> None:
            update(completed_bytes=value.completed_bytes)

        try:
            await asyncio.to_thread(
                acquire_model,
                model,
                self.root,
                local_directory=source,
                cancel=cancel,
                progress=progress,
            )
            update(phase="stored", completed_bytes=model.download_bytes)
        except ModelAcquisitionCancelled:
            log.debug("Local voice package operation cancelled")
            update(phase="cancelled")
        except Exception:
            # Do not publish exception bodies: third-party errors can contain
            # local paths or signed download URLs. Retry reuses verified files.
            log.warning("Local voice package acquisition failed")
            update(
                phase="failed",
                error="Model files could not be verified or stored. Retry the operation.",
            )

    def cancel(self, job_id: str) -> PackageJob:
        with self._lock:
            job = self._jobs.get(self._active)
            if job is None or job.id != job_id:
                raise ValueError("This model operation is no longer active.")
            if job.phase in {"acquiring", "cancelling"}:
                self._cancel.set()
                job = job.model_copy(update={"phase": "cancelling"})
                self._jobs[self._active] = job
            return job

    def resolve(self, fingerprint: str) -> LocalModelManifest | None:
        models = {model.fingerprint: model for model in native_model_catalog()}
        models.update(self._stored())
        with self._lock:
            models.update(self._models)
        return models.get(fingerprint)

    async def close(self) -> None:
        self._closed = True
        self._cancel.set()
        if self._task is not None:
            # Preserve the worker's cleanup; cancelling a to_thread await does
            # not cancel its filesystem operation.
            await asyncio.shield(self._task)
