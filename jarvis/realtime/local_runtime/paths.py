"""Filesystem paths for native runtimes that do not opt into Windows long paths."""

from __future__ import annotations

import ntpath
import os
from pathlib import Path


def windows_extended_path(value: str) -> str:
    """Pass an explicit absolute Win32 data path without the legacy 260-char cap."""
    value = value.replace("/", "\\")
    if value.startswith("\\\\?\\"):
        return value
    drive, tail = ntpath.splitdrive(value)
    if not drive or not tail.startswith("\\"):
        raise ValueError("Native Windows model paths must be absolute.")
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def native_data_path(path: Path) -> str:
    resolved = str(path.resolve())
    return windows_extended_path(resolved) if os.name == "nt" else resolved
