"""The name of THIS machine, the way its owner named it.

Why this needs its own module: the obvious sources are all wrong for a label a
human reads. ``Path.home().name`` gives the account name ("Administrator"),
which says nothing about the device. ``platform.node()`` gives the network
hostname, which on macOS is a mangled derivative ("rubens-macbook.local", or
worse "Rubens-MacBook-Pro-2.fritz.box"). What the owner actually set — "Ruben's
MacBook" — lives in a per-OS place:

* **macOS**   ``scutil --get ComputerName`` (the Sharing pane's "Computer Name")
* **Linux**   ``hostnamectl --pretty`` ("Pretty hostname"), else /etc/hostname
* **Windows** the computer name, which IS what the user typed when renaming the
  PC, available without a subprocess via the ``COMPUTERNAME`` environment

Every lookup degrades: an unreachable command, a missing tool, or a container
without hostnamectl falls back to ``platform.node()`` and finally to a plain
"This machine". The result is cached for the process, because it cannot change
without a restart of the OS-level identity and because nothing user-facing
should pay for a subprocess twice (AP-26).

Subprocess discipline: every spawn passes ``NO_WINDOW_CREATIONFLAGS`` (AP-1) and
a short timeout — a hung `scutil` must never hold a REST request open.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from functools import lru_cache

from loguru import logger

_TIMEOUT_S = 2.0
_FALLBACK = "This machine"


def _run(argv: list[str]) -> str | None:
    """Run a short identity command, returning its trimmed stdout or None."""
    try:
        from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("device name: {} failed: {}", argv[0], exc)
        return None
    out = (proc.stdout or "").strip()
    return out or None


def _strip_hostname_noise(name: str) -> str:
    """Drop the network suffix and separators a hostname carries.

    "Rubens-MacBook-Pro.local" -> "Rubens MacBook Pro". Only applied to a
    hostname fallback; a real ComputerName is used verbatim.
    """
    base = name.split(".", 1)[0]
    return base.replace("-", " ").replace("_", " ").strip() or name


@lru_cache(maxsize=1)
def device_name() -> str:
    """Human-facing name of this machine ("Ruben's MacBook", "STUDIO-PC")."""
    if sys.platform == "darwin":
        friendly = _run(["scutil", "--get", "ComputerName"])
        if friendly:
            return friendly
    elif sys.platform.startswith("linux"):
        pretty = _run(["hostnamectl", "--pretty"])
        if pretty:
            return pretty
        try:
            from pathlib import Path

            text = Path("/etc/hostname").read_text(encoding="utf-8").strip()
            if text:
                return _strip_hostname_noise(text)
        except OSError:
            pass
    else:
        # Windows: COMPUTERNAME *is* the name shown in Settings > About.
        env = (os.environ.get("COMPUTERNAME") or "").strip()
        if env:
            return env

    node = (platform.node() or "").strip()
    return _strip_hostname_noise(node) if node else _FALLBACK


def reset_cache() -> None:
    """Forget the cached name — tests only."""
    device_name.cache_clear()


__all__ = ["device_name", "reset_cache"]
