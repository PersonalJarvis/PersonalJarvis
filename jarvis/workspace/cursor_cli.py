"""Resolve Cursor's CLI without mistaking another vendor's `agent` for it."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _supports_cursor_cli(binary: str, modified_ns: int) -> bool:
    """Probe the headless contract once per binary revision, without logging in."""
    try:
        result = subprocess.run(
            [binary, "--help"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Cursor CLI capability probe failed: %s", type(exc).__name__)
        return False
    text = result.stdout or ""
    return result.returncode == 0 and all(
        flag in text for flag in ("--print", "--output-format", "--resume", "CURSOR_API_KEY")
    )


def resolve_cursor_binary() -> str | None:
    """Prefer the unambiguous alias; continue past shadowing PATH entries."""
    from jarvis.core.path_augment import ensure_cli_paths

    try:
        ensure_cli_paths()
    except Exception as exc:  # noqa: BLE001 — discovery can still use the inherited PATH
        log.debug("Cursor CLI PATH augmentation failed: %s", type(exc).__name__)
    seen: set[str] = set()
    for name in ("cursor-agent", "agent"):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            binary = shutil.which(name, path=directory)
            if not binary or binary in seen:
                continue
            seen.add(binary)
            try:
                modified_ns = Path(binary).stat().st_mtime_ns
            except OSError:
                # A concurrently removed executable is not an installed CLI.
                continue
            if _supports_cursor_cli(binary, modified_ns):
                return binary
    return None
