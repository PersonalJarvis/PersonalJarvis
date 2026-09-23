"""Read-only verification of local model packages before an engine sees them."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import Literal

from .models import FrozenModel, LocalModelManifest, ModelArtifact

MAX_MANIFEST_BYTES = 1_048_576
_READ_CHUNK_BYTES = 1_048_576


class ArtifactCheck(FrozenModel):
    path: str
    state: Literal["verified", "missing", "unsafe", "size_mismatch", "hash_mismatch", "unreadable"]


class PackageCheck(FrozenModel):
    manifest_fingerprint: str
    verified: bool
    artifacts: tuple[ArtifactCheck, ...]


def load_manifest(path: Path) -> LocalModelManifest:
    """Load bounded declarative JSON; nothing in it is imported or executed."""
    with path.open("rb") as source:
        payload = source.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("model manifest exceeds the size limit")
    return LocalModelManifest.model_validate_json(payload)


def _linked(metadata: object) -> bool:
    # st_file_attributes is absent on POSIX. Reparse points include Windows
    # directory junctions, which Path.is_symlink() alone does not reject.
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400) or stat.S_ISLNK(
        getattr(metadata, "st_mode", 0)
    )


def _verify_artifact(root: Path, artifact: ModelArtifact) -> ArtifactCheck:
    def result(state: str) -> ArtifactCheck:
        return ArtifactCheck.model_validate({"path": artifact.path, "state": state})

    candidate = root
    try:
        for part in artifact.path.split("/"):
            candidate = candidate / part
            if _linked(candidate.lstat()):
                return result("unsafe")
        if not candidate.resolve().is_relative_to(root):
            return result("unsafe")
        metadata = candidate.stat()
        if not stat.S_ISREG(metadata.st_mode):
            return result("unsafe")
        if metadata.st_size != artifact.size_bytes:
            return result("size_mismatch")
        digest = hashlib.sha256()
        count = 0
        with candidate.open("rb") as source:
            while chunk := source.read(_READ_CHUNK_BYTES):
                count += len(chunk)
                if count > artifact.size_bytes:
                    return result("size_mismatch")
                digest.update(chunk)
        if count != artifact.size_bytes:
            return result("size_mismatch")
        if digest.hexdigest() != artifact.sha256:
            return result("hash_mismatch")
        return result("verified")
    except FileNotFoundError:
        # Missing weights are an expected pre-download state, reported in the result.
        return result("missing")
    except OSError:
        # The structured result is the failure report; native paths and raw
        # filesystem errors are intentionally not included in the public API.
        return result("unreadable")


def verify_package(manifest: LocalModelManifest, directory: Path) -> PackageCheck:
    """Hash every required file; a verified package still needs a live engine probe.

    Call off the request/audio loop: multi-gigabyte verification is I/O work.
    This verifies a snapshot, not an adversarially mutable external directory.
    The runtime must consume an app-owned immutable copy under its model lease.
    """
    root = directory.resolve()
    checks = tuple(_verify_artifact(root, artifact) for artifact in manifest.artifacts)
    return PackageCheck(
        manifest_fingerprint=manifest.fingerprint,
        verified=all(check.state == "verified" for check in checks),
        artifacts=checks,
    )
