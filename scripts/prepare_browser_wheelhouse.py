"""Bundle the reviewed current crypto wheel for Intel Mac packages.

Cryptography 49+ no longer publishes Intel Mac wheels. Keep the current version
and reuse the verified publisher-built wheel instead of requiring Rust/Xcode
at first run or downgrading the user's crypto dependency.
"""

from __future__ import annotations

import hashlib
import platform
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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
    if f"--hash=sha256:{digest}" in block:
        return lock
    first, remainder = block.split("\n", 1)
    replacement = f"{first}\n    --hash=sha256:{digest} \\\n{remainder}"
    return lock.replace(block, replacement, 1)


def unbundled_libraries(links: str, identities: str) -> list[str]:
    # otool -L includes LC_ID_DYLIB (the module's own install name) as well
    # as LC_LOAD_DYLIB entries. Only actual imported libraries need checking.
    own_names = {line.strip() for line in identities.splitlines()[1:] if line.strip()}
    dependencies = [line.strip().split(" ", 1)[0] for line in links.splitlines()[1:]]
    return [
        path
        for path in dependencies
        if path not in own_names and not path.startswith(("/usr/lib/", "/System/Library/"))
    ]


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
    from scripts.native_crypto_index import fetch_native_wheel, load_manifest

    version = crypto_requirement(lock).splitlines()[0].split("==", 1)[1].split()[0]
    if version != load_manifest()["cryptography"]["version"]:
        raise ValueError("Browser lock and reviewed native crypto version disagree")
    wheel = fetch_native_wheel("macos-x86_64", wheels)
    (assets / "requirements-bundled.lock").write_text(add_wheel_hash(lock, wheel), encoding="utf-8")
    print(f"Bundled verified {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
