"""Fetch pinned pure-JavaScript dependencies without running an installer.

Archives are inspected in memory and never extracted onto the host. The caller
stores the resulting bounded source bundle in its authorized team scope and
passes selected source as sandbox inputs. Native extensions, lifecycle hooks
and implicit transitive resolution are deliberately unsupported capabilities.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import hmac
import io
import json
import re
import tarfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote

from .egress import EgressClient, validate_url

_PACKAGE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
_VERSION = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class DependencyDenied(ValueError):
    """The package cannot be installed inside the local sandbox policy."""


@dataclass(frozen=True, slots=True)
class DependencyBundle:
    package: str
    version: str
    sha256: str
    registry: str
    files: dict[str, str]
    entrypoint: str
    network_bytes: int


class DependencyInstaller:
    """Resolve one exact npm version into a reproducible source-only bundle."""

    def __init__(self, egress: EgressClient) -> None:
        self._egress = egress

    async def install(
        self,
        package: str,
        version: str,
        *,
        sha256: str | None = None,
        registries: Sequence[str] = ("registry.npmjs.org",),
        max_bytes: int = 2_000_000,
        cancel: Callable[[], bool] | None = None,
    ) -> DependencyBundle:
        if not _PACKAGE.fullmatch(package) or len(package) > 214 or not _VERSION.fullmatch(version):
            raise DependencyDenied("Provide a package name and exact semantic version")
        if not registries:
            raise DependencyDenied("No dependency registry is authorized")
        if sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise DependencyDenied("Dependency SHA256 must contain 64 hexadecimal characters")
        if not 1024 <= max_bytes <= 10_000_000:
            raise DependencyDenied("Dependency byte limit must be between 1024 and 10000000")
        registry = registries[0]
        # Registry configuration is hostname-only and HTTPS-only. No credentials
        # or arbitrary URL passed by a worker can widen the allowlist.
        validate_url("https://" + registry, registries)
        metadata = await self._egress.fetch(
            f"https://{registry}/{quote(package, safe='')}/{quote(version, safe='')}",
            allowed_domains=registries,
            max_bytes=min(max_bytes, 262_144),
            cancel=cancel,
        )
        if metadata.status != 200:
            raise DependencyDenied(f"Dependency registry returned HTTP {metadata.status}")
        try:
            manifest = json.loads(metadata.body)
        except (ValueError, UnicodeError) as exc:
            raise DependencyDenied("Registry metadata is invalid JSON") from exc
        self._validate_manifest(manifest, package, version)
        dist = manifest.get("dist", {})
        if not isinstance(dist, dict) or not isinstance(dist.get("tarball"), str):
            raise DependencyDenied("Registry did not provide a package archive")
        target = validate_url(dist["tarball"], registries)
        if target.scheme != "https" or target.raw_host not in registries:
            raise DependencyDenied(
                "Package archive must use an explicitly authorized HTTPS registry"
            )
        remaining = max_bytes - len(metadata.body)
        if remaining <= 0:
            raise DependencyDenied("Dependency metadata exhausted the network budget")
        archive = await self._egress.fetch(
            str(target), allowed_domains=registries, max_bytes=remaining, cancel=cancel
        )
        if archive.status != 200:
            raise DependencyDenied(f"Dependency archive returned HTTP {archive.status}")
        digest = hashlib.sha256(archive.body).hexdigest()
        if sha256 is not None and not hmac.compare_digest(digest, sha256.lower()):
            raise DependencyDenied("Dependency SHA256 verification failed")
        self._verify_integrity(archive.body, dist.get("integrity"))
        # Parsing bounded archives is CPU/file-format work, never the UI loop.
        files, entrypoint = await asyncio.to_thread(
            self._inspect_archive, archive.body, package, version, max_bytes
        )
        if cancel is not None and cancel():
            raise asyncio.CancelledError("Dependency installation canceled")
        return DependencyBundle(
            package,
            version,
            digest,
            registry,
            files,
            entrypoint,
            len(metadata.body) + len(archive.body),
        )

    @staticmethod
    def _validate_manifest(manifest: object, package: str, version: str) -> None:
        if (
            not isinstance(manifest, dict)
            or manifest.get("name") != package
            or manifest.get("version") != version
        ):
            raise DependencyDenied("Dependency identity differs from the requested pinned version")
        if manifest.get("scripts") or manifest.get("gypfile") or manifest.get("bin"):
            raise DependencyDenied(
                "Package scripts, executables and native builds are not supported"
            )
        for field in (
            "dependencies",
            "optionalDependencies",
            "peerDependencies",
            "bundledDependencies",
            "bundleDependencies",
        ):
            if manifest.get(field):
                raise DependencyDenied(
                    "Implicit transitive dependencies are not supported; "
                    "supply separate pinned bundles"
                )

    @staticmethod
    def _verify_integrity(body: bytes, integrity: object) -> None:
        if not isinstance(integrity, str):
            raise DependencyDenied("Registry must provide SHA256 or SHA512 package integrity")
        for item in integrity.split():
            algorithm, separator, encoded = item.partition("-")
            if separator and algorithm in {"sha256", "sha512"}:
                try:
                    expected = base64.b64decode(encoded, validate=True)
                except ValueError as exc:
                    raise DependencyDenied("Invalid package integrity encoding") from exc
                actual = hashlib.new(algorithm, body).digest()
                if hmac.compare_digest(actual, expected):
                    return
        raise DependencyDenied("Dependency registry integrity verification failed")

    @classmethod
    def _inspect_archive(
        cls, body: bytes, package: str, version: str, max_bytes: int
    ) -> tuple[dict[str, str], str]:
        files: dict[str, str] = {}
        total = 0
        entries = 0
        manifest: object = None
        try:
            # Bound decompression BEFORE tar parses PAX/GNU extended headers:
            # those can allocate based on attacker-supplied sizes before a
            # normal member reaches the checks below.
            expanded_limit = max_bytes + 512 * 258
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                expanded = compressed.read(expanded_limit + 1)
            if len(expanded) > expanded_limit:
                raise DependencyDenied("Unpacked dependency exceeds the byte limit")
            # Streaming mode avoids constructing a potentially huge member list.
            with tarfile.open(fileobj=io.BytesIO(expanded), mode="r|") as archive:
                for member in archive:
                    entries += 1
                    if entries > 256:
                        raise DependencyDenied("Dependency archive has too many entries")
                    path = PurePosixPath(member.name)
                    if (
                        "\\" in member.name
                        or ":" in member.name
                        or "\x00" in member.name
                        or path.is_absolute()
                        or ".." in path.parts
                        or not path.parts
                        or path.parts[0] != "package"
                    ):
                        raise DependencyDenied("Unsafe path in dependency archive")
                    if member.isdir():
                        continue
                    if not member.isfile() or len(path.parts) < 2:
                        raise DependencyDenied(
                            "Links and special files are forbidden in dependency archives"
                        )
                    name = str(PurePosixPath(*path.parts[1:]))
                    if name in files:
                        raise DependencyDenied("Duplicate dependency archive entry")
                    if path.suffix.lower() not in {
                        ".js",
                        ".mjs",
                        ".cjs",
                        ".json",
                        ".md",
                        ".txt",
                        ".ts",
                        "",
                    }:
                        raise DependencyDenied("Dependency contains a non-source or native file")
                    total += member.size
                    if member.size < 0 or total > max_bytes:
                        raise DependencyDenied("Unpacked dependency exceeds the byte limit")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise DependencyDenied("Dependency archive entry is unreadable")
                    with stream:
                        data = stream.read(member.size + 1)
                    if len(data) != member.size:
                        raise DependencyDenied("Dependency archive entry size mismatch")
                    files[name] = data.decode("utf-8")
                    if name == "package.json":
                        manifest = json.loads(files[name])
        except (tarfile.TarError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DependencyDenied("Invalid source dependency archive") from exc
        cls._validate_manifest(manifest, package, version)
        assert isinstance(manifest, dict)
        entrypoint = manifest.get("main", "index.js")
        if not isinstance(entrypoint, str):
            raise DependencyDenied("Dependency has no supported JavaScript entrypoint")
        entrypoint = entrypoint.removeprefix("./")
        if entrypoint not in files or PurePosixPath(entrypoint).suffix not in {
            ".js",
            ".mjs",
            ".cjs",
        }:
            raise DependencyDenied("Dependency entrypoint is not a bundled JavaScript source")
        return files, entrypoint
