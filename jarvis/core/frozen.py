"""Where this process was installed from — the ONE probe every layer shares.

Three install kinds exist, and each one ships a different update path:

* ``frozen``  — a PyInstaller bundle distributed as a native installer
  (Windows ``Setup.exe``, macOS ``.dmg``, Linux ``.AppImage``). PyInstaller
  sets ``sys.frozen`` and ``sys._MEIPASS`` (the bundled-resources root).
  Updates arrive as a new installer asset from the GitHub Release, never
  through git.
* ``managed`` — the one-line installer's git checkout under
  ``~/.personal-jarvis`` (marker file + official origin; the probe lives in
  ``jarvis/ui/web/update_routes.py``). Updates are ``git fetch`` + reinstall.
* ``dev``     — anything else: a maintainer checkout, a fork, a plain
  ``pip install``. Never self-updates.

This module is pure stdlib and imports nothing from ``jarvis`` so it can sit
on the boot critical path (AP-26) and be tested on any OS by patching ``sys``.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["bundle_root", "is_frozen", "resources_root"]


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle (``sys.frozen`` + ``_MEIPASS``)."""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def bundle_root() -> Path | None:
    """Directory that holds the frozen executable (the ``onedir`` layout).

    This is where an installer placed the app — the directory a Windows
    uninstaller removes, the ``Contents/MacOS`` folder inside an ``.app``, or
    the AppImage's mounted ``usr/bin``. ``None`` outside a frozen bundle.
    """
    if not is_frozen():
        return None
    return Path(sys.executable).resolve().parent


def resources_root() -> Path | None:
    """Root of the bundled data files (``sys._MEIPASS``); ``None`` when not frozen.

    In the ``onedir`` layout this is ``<bundle_root>/_internal``; the
    package-relative ``datas`` from ``jarvis.spec`` resolve beneath it.
    """
    if not is_frozen():
        return None
    return Path(getattr(sys, "_MEIPASS")).resolve()  # noqa: B009 — attribute is injected at runtime
