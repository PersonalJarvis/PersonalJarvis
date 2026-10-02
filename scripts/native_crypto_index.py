"""One reviewed package source for architectures absent from upstream wheels.

Only cryptography is published here. Standard project dependency metadata stays
valid on PyPI; the installer and uv explicitly opt into this supplemental index.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import html
import io
import json
import platform
import re
import struct
import sys
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urldefrag, urlparse

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packaging" / "native-crypto.json"
PUBLISH_ROOT = "https://personaljarvis.github.io/PersonalJarvis/native-crypto"
PLATFORMS = {
    "macos-x86_64": "macosx_13_0_x86_64",
    "windows-arm64": "win_arm64",
}


def load_manifest() -> dict:
    spec = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if spec.get("schema") != 1 or not re.fullmatch(
        r"\d+\.\d+\.\d+", spec["cryptography"]["version"]
    ):
        raise ValueError("Invalid native cryptography manifest")
    return spec


def index_url() -> str:
    return f"{PUBLISH_ROOT}/{publication_id(load_manifest())}/simple/"


def wheel_links_url() -> str:
    """Pip's flat link page avoids probing this host for unrelated packages."""
    return index_url() + "cryptography/"


def publication_id(spec: dict) -> str:
    revision = spec.get("build_revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("An immutable positive build revision is required")
    return f"{spec['cryptography']['version']}-{revision}"


def pip_options(system: str | None = None, machine: str | None = None) -> list[str]:
    system = sys.platform if system is None else system
    machine = (platform.machine() if machine is None else machine).lower()
    native = (system == "darwin" and machine in {"x86_64", "amd64"}) or (
        system == "win32" and machine in {"arm64", "aarch64"}
    )
    return ["--find-links", wheel_links_url()] if native else []


def native_records(spec: dict | None = None) -> list[dict]:
    spec = load_manifest() if spec is None else spec
    version = spec["cryptography"]["version"]
    records = spec.get("artifacts", [])
    if len(records) != len(PLATFORMS) or {row.get("target") for row in records} != set(PLATFORMS):
        raise ValueError("Both native architectures must have one verified artifact")
    for row in records:
        filename = f"cryptography-{version}-cp311-abi3-{PLATFORMS[row['target']]}.whl"
        expected_url = f"{PUBLISH_ROOT}/{publication_id(spec)}/files/{filename}"
        if row.get("filename") != filename or row.get("url") != expected_url:
            raise ValueError("Native wheel identity or download location does not match")
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")):
            raise ValueError("Native wheel SHA256 is missing or invalid")
    return records


def is_native_artifact(url: object, digest: str | None, target: str, python: str) -> bool:
    """An arbitrary GitHub/Pages URL never receives an artifact exception."""
    if not isinstance(url, str) or not url.startswith(PUBLISH_ROOT + "/"):
        return False
    if target not in PLATFORMS or python not in {"3.11", "3.12", "3.13", "3.14"}:
        return False
    location, fragment = urldefrag(url)
    if fragment and fragment != f"sha256={digest}":
        return False
    return any(
        row["target"] == target and row["url"] == location and row["sha256"] == digest
        for row in native_records()
    )


def fetch_native_wheel(target: str, directory: Path) -> Path:
    spec = load_manifest()
    row = next((row for row in native_records(spec) if row["target"] == target), None)
    if row is None:
        raise ValueError("No reviewed wheel for this native target")
    with urllib.request.urlopen(row["url"], timeout=120) as response:  # noqa: S310 - exact reviewed URL
        content = response.read(64 * 1024 * 1024 + 1)
    if len(content) > 64 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != row["sha256"]:
        raise ValueError("Native wheel size or SHA256 mismatch")
    validate_wheel(content, target, spec["cryptography"]["version"])
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / row["filename"]
    destination.write_bytes(content)
    return destination


def build_site(artifact_dir: Path, output: Path) -> None:
    """Generate a PEP 503 index with official artifacts and verified native wheels."""
    spec = load_manifest()
    version = spec["cryptography"]["version"]
    records = native_records(spec)
    url = f"https://pypi.org/pypi/cryptography/{version}/json"
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed HTTPS host
        metadata = json.load(response)
    if metadata["info"]["version"] != version:
        raise ValueError("Upstream metadata describes another version")
    sources = [row for row in metadata["urls"] if row["packagetype"] == "sdist"]
    if len(sources) != 1 or sources[0]["digests"]["sha256"] != spec["cryptography"]["sha256"]:
        raise ValueError("Upstream source differs from the reviewed source")
    entries = []
    for row in metadata["urls"]:
        parsed = urlparse(row["url"])
        digest = row["digests"]["sha256"]
        if parsed.scheme != "https" or parsed.hostname != "files.pythonhosted.org":
            raise ValueError("Upstream artifact is not hosted on public PyPI")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Upstream artifact has no valid SHA256")
        entries.append((row["filename"], row["url"], digest))
    publication = publication_id(spec)
    version_dir = output / "native-crypto" / publication
    files = version_dir / "files"
    files.mkdir(parents=True, exist_ok=True)
    for row in records:
        source = artifact_dir / row["filename"]
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise ValueError("Native artifact differs from the reviewed build")
        validate_wheel(content, row["target"], version)
        destination = files / row["filename"]
        if destination.exists() and destination.read_bytes() != content:
            raise ValueError("Published artifacts are immutable; increment build_revision")
        destination.write_bytes(content)
        entries.append((row["filename"], row["url"], row["sha256"]))
    package_dir = version_dir / "simple" / "cryptography"
    package_dir.mkdir(parents=True, exist_ok=True)
    links = [
        f'<a href="{html.escape(url)}#sha256={digest}">{html.escape(name)}</a><br>'
        for name, url, digest in sorted(entries)
    ]
    (package_dir / "index.html").write_text(
        "<!doctype html><html><head><title>cryptography</title></head><body>\n"
        + "\n".join(links)
        + "\n</body></html>\n",
        encoding="utf-8",
    )
    (version_dir / "simple" / "index.html").write_text(
        '<!doctype html><a href="cryptography/">cryptography</a>\n', encoding="utf-8"
    )
    (version_dir / "manifest.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    (output / "index.html").write_text(
        "<!doctype html><title>Personal Jarvis native packages</title>"
        "<h1>Personal Jarvis native packages</h1>"
        "<p>Compiler-free dependencies rebuilt from hash-pinned upstream sources. "
        "Official artifacts remain hosted by PyPI.</p>"
        f'<a href="native-crypto/{publication}/simple/">cryptography {version}</a> '
        f'<a href="native-crypto/{publication}/manifest.json">Build and artifact manifest</a>\n',
        encoding="utf-8",
    )


def validate_macho(data: bytes) -> None:
    """Validate the binary's deployment floor, not just its wheel filename."""
    if len(data) < 32 or struct.unpack_from("<II", data) != (0xFEEDFACF, 0x01000007):
        raise ValueError("Native Mac wheel must contain thin Intel Mach-O modules")
    count, command_bytes = struct.unpack_from("<II", data, 16)
    if 32 + command_bytes > len(data):
        raise ValueError("Invalid Mach-O load command extent")
    position, versions = 32, []
    for _ in range(count):
        if position + 8 > 32 + command_bytes:
            raise ValueError("Invalid Mach-O load command")
        command, size = struct.unpack_from("<II", data, position)
        if size < 8 or position + size > 32 + command_bytes:
            raise ValueError("Invalid Mach-O load command size")
        if command == 0x32 and size >= 24:  # LC_BUILD_VERSION
            system, minimum = struct.unpack_from("<II", data, position + 8)
            if system != 1:
                raise ValueError("Expected a macOS deployment target")
            versions.append(minimum)
        elif command == 0x24 and size >= 16:  # LC_VERSION_MIN_MACOSX
            versions.append(struct.unpack_from("<I", data, position + 8)[0])
        position += size
    if not versions or any(version > (13 << 16) for version in versions):
        raise ValueError("Native crypto must remain loadable on macOS 13.0")


def validate_wheel(content: bytes, target: str, version: str) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Wheel must describe exactly one distribution")
        fields = email.parser.BytesParser().parsebytes(archive.read(metadata[0]))
        if fields.get_all("Name") != ["cryptography"] or fields.get_all("Version") != [version]:
            raise ValueError("Wheel metadata does not match the reviewed distribution")
        modules = [name for name in archive.namelist() if name.endswith((".so", ".pyd"))]
        if not modules:
            raise ValueError("Wheel has no native modules")
        for name in modules:
            data = archive.read(name)
            if target == "macos-x86_64":
                validate_macho(data)
            elif target == "windows-arm64":
                if len(data) < 64 or data[:2] != b"MZ":
                    raise ValueError("Expected a Windows PE module")
                offset = struct.unpack_from("<I", data, 0x3C)[0]
                if offset + 6 > len(data) or data[offset : offset + 4] != b"PE\0\0":
                    raise ValueError("Invalid PE header")
                if struct.unpack_from("<H", data, offset + 4)[0] != 0xAA64:
                    raise ValueError("Native Windows wheel must contain ARM64 modules")
            else:
                raise ValueError("Unknown native target")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_site(args.artifacts, args.output)


if __name__ == "__main__":
    main()
