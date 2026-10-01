"""Pinned in-memory package retrieval never runs or extracts host code."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile

import pytest

from jarvis.swarm.dependencies import DependencyDenied, DependencyInstaller
from jarvis.swarm.egress import FetchResult


def archive_bytes(entries: dict[str, str], *, link: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in entries.items():
            content = text.encode()
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        if link:
            member = tarfile.TarInfo("package/host-link.js")
            member.type = tarfile.SYMTYPE
            member.linkname = link
            archive.addfile(member)
    return buffer.getvalue()


def package_archive(**changes) -> tuple[dict, bytes]:
    manifest = {"name": "test-package", "version": "1.2.3", "main": "index.js", **changes}
    archive = archive_bytes(
        {
            "package/package.json": json.dumps(manifest),
            "package/index.js": "function add(a,b){return a+b}",
        }
    )
    return manifest, archive


class Registry:
    def __init__(self, manifest: dict, archive: bytes):
        self.archive = archive
        self.manifest = {
            **manifest,
            "dist": {
                "tarball": "https://registry.npmjs.org/test-package/-/test-package-1.2.3.tgz",
                "integrity": "sha512-"
                + base64.b64encode(hashlib.sha512(archive).digest()).decode(),
            },
        }
        self.calls = []

    async def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        data = self.archive if url.endswith(".tgz") else json.dumps(self.manifest).encode()
        return FetchResult(
            url, 200, "application/octet-stream", data, hashlib.sha256(data).hexdigest()
        )


@pytest.mark.asyncio
async def test_pinned_pure_js_returns_sources_without_host_install(tmp_path) -> None:
    manifest, archive = package_archive()
    registry = Registry(manifest, archive)
    result = await DependencyInstaller(registry).install("test-package", "1.2.3")
    assert result.sha256 == hashlib.sha256(archive).hexdigest()
    assert result.entrypoint == "index.js"
    assert "function add" in result.files["index.js"]
    assert len(registry.calls) == 2
    assert result.network_bytes > len(archive)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "version", ["latest", "^1.2.3", "~1.2", "1", "https://evil.test/package", "../1.2.3"]
)
async def test_dependency_version_must_be_exact(version: str) -> None:
    manifest, archive = package_archive()
    registry = Registry(manifest, archive)
    with pytest.raises(DependencyDenied):
        await DependencyInstaller(registry).install("test-package", version)
    assert not registry.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"scripts": {"postinstall": "steal host credentials"}},
        {"bin": "run.js"},
        {"gypfile": True},
        {"dependencies": {"other": "*"}},
        {"optionalDependencies": {"native": "1.0.0"}},
        {"version": "9.9.9"},
    ],
)
async def test_hooks_native_transitive_and_wrong_versions_are_denied(changes: dict) -> None:
    manifest, archive = package_archive(**changes)
    registry = Registry(manifest, archive)
    with pytest.raises(DependencyDenied):
        await DependencyInstaller(registry).install("test-package", "1.2.3")
    assert len(registry.calls) == 1


@pytest.mark.asyncio
async def test_tampered_archive_or_caller_hash_denied() -> None:
    manifest, archive = package_archive()
    registry = Registry(manifest, archive)
    registry.manifest["dist"]["integrity"] = "sha512-" + base64.b64encode(bytes(64)).decode()
    with pytest.raises(DependencyDenied, match="integrity"):
        await DependencyInstaller(registry).install("test-package", "1.2.3")
    registry = Registry(manifest, archive)
    with pytest.raises(DependencyDenied, match="SHA256"):
        await DependencyInstaller(registry).install("test-package", "1.2.3", sha256="0" * 64)


@pytest.mark.asyncio
async def test_archive_cannot_move_to_unapproved_registry() -> None:
    manifest, archive = package_archive()
    registry = Registry(manifest, archive)
    registry.manifest["dist"]["tarball"] = "https://evil.test/steal.tgz"
    with pytest.raises(ValueError):
        await DependencyInstaller(registry).install("test-package", "1.2.3")
    assert len(registry.calls) == 1


@pytest.mark.parametrize(
    "path",
    [
        "../escape.js",
        "package/../../escape.js",
        "/host.js",
        "package/C:/host.js",
        "package/a\\b.js",
    ],
)
def test_archive_path_traversal_denied(path: str) -> None:
    archive = archive_bytes({path: "bad"})
    with pytest.raises(DependencyDenied, match="path"):
        DependencyInstaller._inspect_archive(archive, "test-package", "1.2.3", 10000)


def test_archive_symlinks_and_bombs_denied() -> None:
    archive = archive_bytes({}, link="/private/secret")
    with pytest.raises(DependencyDenied, match="Links"):
        DependencyInstaller._inspect_archive(archive, "test-package", "1.2.3", 10000)
    archive = archive_bytes({"package/index.js": "x" * 20000})
    with pytest.raises(DependencyDenied, match="byte limit"):
        DependencyInstaller._inspect_archive(archive, "test-package", "1.2.3", 10000)


def test_archive_manifest_checked_independently_from_registry() -> None:
    manifest = {"name": "test-package", "version": "1.2.3", "scripts": {"postinstall": "bad"}}
    archive = archive_bytes({"package/package.json": json.dumps(manifest)})
    with pytest.raises(DependencyDenied, match="scripts"):
        DependencyInstaller._inspect_archive(archive, "test-package", "1.2.3", 10000)
