"""Build the pinned crypto wheel for Intel Mac packages, never on the user's Mac.

Cryptography 49+ no longer publishes Intel Mac wheels. Keep the current version
and build a statically linked wheel on the publisher's toolchain instead of
requiring Rust/Xcode at first run or downgrading the user's crypto dependency.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: E402


def crypto_requirement(lock: str) -> str:
    match = re.search(r"^cryptography==[^\n]+(?:\n[ \t]+[^\n]*)*", lock, re.MULTILINE)
    if match is None:
        raise ValueError("Browser lock does not pin cryptography")
    return match.group()


def add_wheel_hash(lock: str, wheel: Path) -> str:
    block = crypto_requirement(lock)
    version = block.splitlines()[0].split("==", 1)[1].split()[0]
    if not wheel.name.startswith(f"cryptography-{version}-"):
        raise ValueError("Built wheel does not match the locked cryptography version")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    first, remainder = block.split("\n", 1)
    replacement = f"{first}\n    --hash=sha256:{digest} \\\n{remainder}"
    return lock.replace(block, replacement, 1)


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, check=True, encoding="utf-8", creationflags=NO_WINDOW_CREATIONFLAGS, **kwargs
    )


def main() -> int:
    if sys.platform != "darwin" or platform.machine().lower() not in {"x86_64", "amd64"}:
        print("Browser wheelhouse is only needed on Intel macOS")
        return 0
    assets = ROOT / "jarvis" / "assets" / "browser"
    lock = (assets / "requirements.lock").read_text(encoding="utf-8")
    wheels = assets / "wheels"
    wheels.mkdir(exist_ok=True)
    if list(wheels.glob("*.whl")):
        raise RuntimeError("Build the browser wheelhouse in a clean checkout")
    env = os.environ.copy()
    env["OPENSSL_STATIC"] = "1"
    env["OPENSSL_DIR"] = run(["brew", "--prefix", "openssl@3"], capture_output=True).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="jarvis-browser-wheel-") as scratch:
        source = Path(scratch) / "source.lock"
        source.write_text(crypto_requirement(lock) + "\n", encoding="utf-8")
        run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-cache-dir",
                "--no-binary",
                "cryptography",
                "--require-hashes",
                "-r",
                str(source),
                "--wheel-dir",
                str(wheels),
            ],
            env=env,
        )
        built = list(wheels.glob("cryptography-*.whl"))
        if len(built) != 1:
            raise RuntimeError("Expected exactly one pinned cryptography wheel")
        # A wheel linked to Homebrew's shared OpenSSL works only on the builder.
        # Check the actual Mach-O dependencies before packaging the artifact.
        with zipfile.ZipFile(built[0]) as archive:
            binaries = [name for name in archive.namelist() if name.endswith(".so")]
            if not binaries:
                raise RuntimeError("Cryptography wheel has no native module")
            for name in binaries:
                binary = Path(scratch) / Path(name).name
                binary.write_bytes(archive.read(name))
                links = run(["otool", "-L", str(binary)], capture_output=True).stdout
                for line in links.splitlines()[1:]:
                    dependency = line.strip().split(" ", 1)[0]
                    if not dependency.startswith(("/usr/lib/", "/System/Library/")):
                        raise RuntimeError("Cryptography wheel links a non-system shared library")
        (assets / "requirements-bundled.lock").write_text(
            add_wheel_hash(lock, built[0]), encoding="utf-8"
        )
        print(f"Bundled verified {built[0].name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
