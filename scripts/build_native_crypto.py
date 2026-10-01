"""Build missing cryptography wheels from authenticated, pinned upstream sources.

This publisher-only tool runs on native Intel macOS or Windows ARM64 builders.
End users install the resulting hashed wheels and never need a compiler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: E402
from scripts.prepare_browser_wheelhouse import unbundled_libraries  # noqa: E402


def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW_CREATIONFLAGS,
        **kwargs,
    )


def download_verified(url: str, digest: str, destination: Path) -> None:
    """Verify the entire source before allowing its build scripts to execute."""
    if not url.startswith("https://") or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("An HTTPS source and SHA256 digest are required")
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - HTTPS checked above
        content = response.read()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Source SHA256 mismatch")
    destination.write_bytes(content)


def windows_external_libraries(output: str) -> list[str]:
    """Static OpenSSL must not leave a DLL dependency on the build machine."""
    allowed = {
        "python3.dll",
        "kernel32.dll",
        "advapi32.dll",
        "bcrypt.dll",
        "crypt32.dll",
        "ncrypt.dll",
        "ws2_32.dll",
        "user32.dll",
        "vcruntime140.dll",
        "ucrtbase.dll",
    }
    imports = [
        line.strip().lower()
        for line in output.splitlines()
        if line.strip().lower().endswith(".dll")
    ]
    return [name for name in imports if name not in allowed and not name.startswith("api-ms-win-")]


def verify_native_links(wheel: Path, scratch: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        binaries = [name for name in archive.namelist() if name.endswith((".so", ".pyd"))]
        if not binaries:
            raise RuntimeError("The wheel contains no native module")
        for index, name in enumerate(binaries):
            binary = scratch / f"{index}-{Path(name).name}"
            binary.write_bytes(archive.read(name))
            if sys.platform == "darwin":
                links = run(["otool", "-L", str(binary)], capture_output=True).stdout
                identities = run(["otool", "-D", str(binary)], capture_output=True).stdout
                outside = unbundled_libraries(links, identities)
                arch = run(["lipo", "-archs", str(binary)], capture_output=True).stdout.strip()
                if arch != "x86_64":
                    raise RuntimeError(f"Expected native Intel module, got {arch}")
            else:
                links = run(["dumpbin", "/dependents", str(binary)], capture_output=True).stdout
                headers = run(["dumpbin", "/headers", str(binary)], capture_output=True).stdout
                if "AA64 machine (ARM64)" not in headers:
                    raise RuntimeError("Expected a native Windows ARM64 module")
                outside = windows_external_libraries(links)
            print(links, flush=True)
            if outside:
                raise RuntimeError(f"Wheel imports unbundled libraries: {outside}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    machine = platform.machine().lower()
    target = (
        "macos-x86_64"
        if sys.platform == "darwin" and machine == "x86_64"
        else "windows-arm64"
        if sys.platform == "win32" and machine in {"arm64", "aarch64"}
        else None
    )
    if target is None:
        raise RuntimeError("Use a native Intel macOS or Windows ARM64 builder")
    spec = json.loads((ROOT / "packaging/native-crypto.json").read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if list(output.glob("*.whl")):
        raise RuntimeError("The output directory already contains wheels")
    env = os.environ.copy()
    env["RUSTUP_TOOLCHAIN"] = spec["rust"]
    env["OPENSSL_STATIC"] = "1"
    env["CARGO_BUILD_TARGET"] = (
        "x86_64-apple-darwin" if target == "macos-x86_64" else "aarch64-pc-windows-msvc"
    )
    if target == "macos-x86_64":
        env["MACOSX_DEPLOYMENT_TARGET"] = spec["macos_deployment_target"]
    run(["rustup", "toolchain", "install", spec["rust"], "--profile", "minimal"])
    run(["rustup", "target", "add", env["CARGO_BUILD_TARGET"], "--toolchain", spec["rust"]])
    with tempfile.TemporaryDirectory(prefix="native-crypto-build-") as raw:
        scratch = Path(raw)
        for name in ("openssl", "cryptography"):
            download_verified(spec[name]["url"], spec[name]["sha256"], scratch / f"{name}.tar.gz")
        with tarfile.open(scratch / "openssl.tar.gz") as archive:
            archive.extractall(scratch / "source", filter="data")
        source = scratch / "source" / f"openssl-{spec['openssl']['version']}"
        prefix = scratch / "openssl"
        env["OPENSSL_DIR"] = str(prefix)
        configure = "darwin64-x86_64-cc" if target == "macos-x86_64" else "VC-WIN64-ARM"
        run(
            [
                "perl",
                "Configure",
                configure,
                "no-shared",
                "no-tests",
                f"--prefix={prefix}",
                "--libdir=lib",
            ],
            cwd=source,
            env=env,
        )
        make = "make" if target == "macos-x86_64" else "nmake"
        run([make, *(["-j2"] if make == "make" else [])], cwd=source, env=env)
        run([make, "install_sw"], cwd=source, env=env)
        run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-cache-dir",
                "--config-settings=build-args=--locked --features=pyo3/abi3-py311",
                "--wheel-dir",
                str(output),
                str(scratch / "cryptography.tar.gz"),
            ],
            env=env,
        )
        wheels = list(
            output.glob(f"cryptography-{spec['cryptography']['version']}-cp311-abi3-*.whl")
        )
        if len(wheels) != 1:
            raise RuntimeError("Expected one CPython 3.11+ abi3 wheel of the pinned version")
        wheel = wheels[0]
        verify_native_links(wheel, scratch)
        record = {
            "target": target,
            "filename": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "cryptography": spec["cryptography"],
            "openssl": spec["openssl"],
            "rust": spec["rust"],
            "python": sys.version,
        }
        (output / f"{target}.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(record, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
