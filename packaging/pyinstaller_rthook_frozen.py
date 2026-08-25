"""PyInstaller runtime hook: keep a frozen install out of its own program files.

Why this exists
---------------
``jarvis.core.config`` derives ``PROJECT_ROOT`` from ``jarvis/core/config.py``'s
own location. Inside a PyInstaller ``onedir`` bundle that resolves to the
bundled-resources root (``sys._MEIPASS`` = ``<install dir>/_internal``), so
without this hook a natively installed app would keep its ``jarvis.toml`` and
its whole ``data/`` tree INSIDE the installed program directory. Two things
break there:

* an in-place upgrade reinstalls ``_internal/jarvis.toml`` and silently
  discards every setting the user made, and
* an uninstall that removes the program directory takes the user's memory,
  skills and logs with it.

A runtime hook runs before the frozen entry script imports anything from
``jarvis``, which is the only moment where the import-time path constants in
``jarvis.core.config`` can still be steered. It points the two documented
override variables at the per-user application directory and seeds a first-run
``jarvis.toml`` from the copy inside the bundle.

An explicitly exported ``JARVIS_CONFIG`` / ``JARVIS_DATA_DIR`` always wins, so a
maintainer can still run a frozen build against a scratch directory.

Standard library only, and no ``jarvis`` import: a failure in a runtime hook
kills a windowed process before any logging exists. ``tests/unit/packaging/
test_frozen_runtime_hook.py`` pins the directory convention below to
``jarvis.core.paths.user_data_dir`` so the duplication cannot drift.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Mirrors jarvis.core.branding.WINDOWS_USER_DATA_DIR_NAME / FALLBACK_USER_DATA_DIR_NAME.
_WINDOWS_USER_DATA_DIR_NAME = "Jarvis"
_FALLBACK_USER_DATA_DIR_NAME = ".jarvis"
_CONFIG_FILE_NAME = "jarvis.toml"


def _user_data_dir() -> Path:
    """``%LOCALAPPDATA%\\Jarvis`` on Windows, ``~/.jarvis`` everywhere else."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / _WINDOWS_USER_DATA_DIR_NAME
    return Path.home() / _FALLBACK_USER_DATA_DIR_NAME


def _is_set(name: str) -> bool:
    value = os.environ.get(name)
    return bool(value and value.strip())


def _warn(message: str) -> None:
    """Report a hook problem without ever becoming the crash itself."""
    stream = getattr(sys, "stderr", None)
    if stream is None:
        return
    try:
        stream.write(f"[jarvis] {message}\n")
    except (OSError, ValueError, AttributeError):
        # A windowed bundle can hand out a closed or null stream. There is no
        # log file yet at hook time, so an unreportable warning is dropped on
        # purpose rather than killing a boot that still works.
        pass


def _apply() -> None:
    if not (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")):
        return

    home = _user_data_dir()
    config_path = home / _CONFIG_FILE_NAME

    if not _is_set("JARVIS_CONFIG"):
        if not config_path.exists():
            bundled = Path(sys._MEIPASS) / _CONFIG_FILE_NAME  # noqa: SLF001
            try:
                home.mkdir(parents=True, exist_ok=True)
                if bundled.is_file():
                    shutil.copyfile(bundled, config_path)
            except OSError as exc:
                # Not silence: say it on stderr (a console CLI run shows it) and
                # continue. jarvis.core.config_writer creates the file on first
                # write anyway, so an unseeded config still boots on defaults.
                # A windowed bundle has no stderr yet — ensure_standard_streams()
                # runs later — so writing to it must never become the crash.
                _warn(f"could not seed {config_path}: {exc}")
        os.environ["JARVIS_CONFIG"] = str(config_path)

    if not _is_set("JARVIS_DATA_DIR"):
        os.environ["JARVIS_DATA_DIR"] = str(home / "data")


_apply()
