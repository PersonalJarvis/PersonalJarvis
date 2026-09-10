"""Verified per-user Python bootstrap for frozen desktop installations."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

UV_VERSION = "0.12.9"


def ensure_uv(root: Path) -> str:
    found = shutil.which("uv")
    if found:
        return found
    target = root / ("uv.exe" if os.name == "nt" else "uv")
    if target.is_file():
        return str(target)
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    tag = (
        ("win_arm64" if arm else "win_amd64")
        if os.name == "nt"
        else ("arm64" if sys.platform == "darwin" and arm else "aarch64" if arm else "x86_64")
    )
    family = "win_" if os.name == "nt" else "macosx" if sys.platform == "darwin" else "manylinux"
    with urllib.request.urlopen(f"https://pypi.org/pypi/uv/{UV_VERSION}/json", timeout=30) as res:
        metadata = json.load(res)
    wheels = [
        x
        for x in metadata["urls"]
        if x["filename"].endswith(".whl") and tag in x["filename"] and family in x["filename"]
    ]
    if not wheels:
        raise RuntimeError("No Python bootstrap binary supports this OS and architecture")
    wheel = wheels[0]
    if (
        urlsplit(wheel["url"]).scheme != "https"
        or urlsplit(wheel["url"]).hostname != "files.pythonhosted.org"
    ):
        raise RuntimeError("Unexpected Python bootstrap download host")
    with urllib.request.urlopen(wheel["url"], timeout=120) as res:  # noqa: S310 — HTTPS host checked
        blob = res.read(100_000_000)
    if hashlib.sha256(blob).hexdigest() != wheel["digests"]["sha256"]:
        raise RuntimeError("Python bootstrap checksum mismatch")
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        member = next(n for n in archive.namelist() if n.endswith("/" + target.name))
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(archive.read(member))
        temporary.chmod(0o755)
        os.replace(temporary, target)
        for name in archive.namelist():
            if "license" in name.lower() and not name.endswith("/"):
                (root / ("uv-" + Path(name).name)).write_bytes(archive.read(name))
    return str(target)
