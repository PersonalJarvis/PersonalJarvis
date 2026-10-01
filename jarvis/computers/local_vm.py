"""Local virtual machines through Canonical Multipass.

Multipass is open source and runs on Windows (Hyper-V or VirtualBox), macOS
and Linux (snap) with one CLI, which makes it the one backend that keeps this
feature identical on every desktop OS. Jarvis only drives the ``multipass``
binary; a machine without it (a headless server, a box where the user has not
installed it) gets ``available=False`` plus the install link for its OS and
nothing else changes.

A new VM boots Ubuntu with Jarvis's public key planted by cloud-init, so it is
reachable over SSH as ``ubuntu@<vm ip>`` the moment it is up — the same code
path as any rented server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

DEFAULT_USER = "ubuntu"
#: Images offered in the UI; Multipass accepts any alias it knows.
IMAGES: tuple[str, ...] = ("24.04", "22.04")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")
LAUNCH_TIMEOUT_S = 1200
_CLI_TIMEOUT_S = 60


class LocalVmError(Exception):
    """A Multipass call failed; ``message`` is safe to show the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class LocalInstance:
    name: str
    state: str
    ipv4: str | None
    release: str | None
    cpus: int | None = None
    memory_total_mb: int | None = None
    disk_total_gb: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def install_url() -> str:
    if sys.platform == "darwin":
        return "https://canonical.com/multipass/install#macos"
    if sys.platform.startswith("linux"):
        return "https://canonical.com/multipass/install#linux"
    return "https://canonical.com/multipass/install#windows"


def install_hint() -> str:
    if sys.platform == "darwin":
        return "brew install --cask multipass"
    if sys.platform.startswith("linux"):
        return "sudo snap install multipass"
    return "Download the Multipass installer for Windows"


def binary() -> str | None:
    return shutil.which("multipass")


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))


async def _run(*args: str, timeout_s: float = _CLI_TIMEOUT_S) -> str:
    exe = binary()
    if exe is None:
        raise LocalVmError("Multipass is not installed on this computer.")
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except OSError as exc:
        raise LocalVmError("Multipass could not be started.") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise LocalVmError(f"Multipass did not finish within {int(timeout_s)} s.") from exc
    stdout = out.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        detail = err.decode("utf-8", errors="replace").strip().splitlines()
        # Multipass's own message is about the local VM, never a secret.
        raise LocalVmError(detail[-1] if detail else f"Multipass failed ({proc.returncode}).")
    return stdout


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):  # a missing number is shown as unknown
        return None


def parse_list(payload: Any) -> list[LocalInstance]:
    rows = payload.get("list", []) if isinstance(payload, dict) else []
    instances: list[LocalInstance] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        ips = row.get("ipv4") or []
        instances.append(
            LocalInstance(
                name=str(row["name"]),
                state=str(row.get("state") or "Unknown"),
                ipv4=str(ips[0]) if ips else None,
                release=row.get("release") or None,
            )
        )
    return instances


def parse_info(payload: Any, name: str) -> LocalInstance | None:
    info = (payload.get("info") or {}).get(name) if isinstance(payload, dict) else None
    if not isinstance(info, dict):
        return None
    ips = info.get("ipv4") or []
    memory = info.get("memory") or {}
    disk_total = 0.0
    for disk in (info.get("disks") or {}).values():
        disk_total += _num((disk or {}).get("total")) or 0.0
    mem_total = _num(memory.get("total"))
    cpus = _num(info.get("cpu_count"))
    return LocalInstance(
        name=name,
        state=str(info.get("state") or "Unknown"),
        ipv4=str(ips[0]) if ips else None,
        release=info.get("release") or info.get("image_release") or None,
        cpus=int(cpus) if cpus is not None else None,
        memory_total_mb=int(mem_total // (1024 * 1024)) if mem_total else None,
        disk_total_gb=round(disk_total / 1024**3, 1) if disk_total else None,
    )


async def status() -> dict[str, Any]:
    """Is Multipass here, which version, and which instances exist."""
    exe = binary()
    result: dict[str, Any] = {
        "backend": "multipass",
        "available": exe is not None,
        "version": None,
        "install_url": install_url(),
        "install_hint": install_hint(),
        "images": list(IMAGES),
        "instances": [],
        "error": None,
    }
    if exe is None:
        return result
    try:
        version = await _run("version", timeout_s=15)
        first = version.strip().splitlines()[0] if version.strip() else ""
        result["version"] = first.split()[-1] if first else None
        result["instances"] = [i.to_dict() for i in await list_instances()]
    except LocalVmError as exc:
        # Installed but not answering (daemon stopped, missing Hyper-V):
        # report it instead of pretending there is nothing.
        result["error"] = exc.message
    return result


async def list_instances() -> list[LocalInstance]:
    raw = await _run("list", "--format", "json")
    try:
        return parse_list(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise LocalVmError("Multipass sent a list Jarvis cannot read.") from exc


async def info(name: str) -> LocalInstance | None:
    raw = await _run("info", name, "--format", "json")
    try:
        return parse_info(json.loads(raw), name)
    except json.JSONDecodeError as exc:
        raise LocalVmError("Multipass sent details Jarvis cannot read.") from exc


def cloud_init(public_key: str) -> str:
    return f"#cloud-config\nssh_authorized_keys:\n  - {public_key.strip()}\n"


async def launch(
    name: str, *, cpus: int, memory_gb: int, disk_gb: int, image: str, public_key: str
) -> LocalInstance:
    """Create and boot a VM; returns once it runs and has an address."""
    if not valid_name(name):
        raise LocalVmError("Use 2-40 lowercase letters, digits or hyphens, starting with a letter.")
    with tempfile.TemporaryDirectory(prefix="jarvis-vm-") as tmp:
        seed = Path(tmp) / "cloud-init.yaml"
        seed.write_text(cloud_init(public_key), encoding="utf-8")
        await _run(
            "launch",
            image,
            "--name",
            name,
            "--cpus",
            str(cpus),
            "--memory",
            f"{memory_gb}G",
            "--disk",
            f"{disk_gb}G",
            "--cloud-init",
            str(seed),
            "--timeout",
            str(LAUNCH_TIMEOUT_S - 60),
            timeout_s=LAUNCH_TIMEOUT_S,
        )
    instance = await info(name)
    if instance is None or not instance.ipv4:
        raise LocalVmError("The VM started but has no network address yet.")
    return instance


async def start(name: str) -> None:
    await _run("start", name, timeout_s=300)


async def stop(name: str) -> None:
    await _run("stop", name, timeout_s=300)


async def delete(name: str) -> None:
    await _run("delete", "--purge", name, timeout_s=300)
