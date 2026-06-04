"""Pytest-Setup fuer ``tests/overlay/``.

- Pfad-Erweiterung: ``OS-Level/src`` in ``sys.path``, damit ``import overlay`` greift.
- Headless-Qt: ``QT_QPA_PLATFORM=offscreen`` falls noch nicht gesetzt.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OS_LEVEL_SRC = REPO_ROOT / "OS-Level" / "src"

# These tests exercise the ABANDONED PySide6 overlay tree under OS-Level/src
# (see docs/plans/cross-platform-mac-linux/ADR-orb-framework.md — the LIVE orb
# is the Tk package ui/orb/overlay.py). Many modules `import overlay` at module
# scope, which would crash collection on a box without PySide6 (e.g. the Linux
# CI leg, which installs only the base deps). Gate the whole dir on PySide6 so
# collection stays clean and the Wave-0 min-passed floor is honest (sub-task 0.7).
import importlib.util as _ilu

_HAS_PYSIDE = _ilu.find_spec("PySide6") is not None

if _HAS_PYSIDE and str(OS_LEVEL_SRC) not in sys.path:
    sys.path.insert(0, str(OS_LEVEL_SRC))

# Qt headless — schadet nicht wenn ein Test es nicht braucht.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# Wave-2 cross-platform tests that exercise the LIVE Tk orb seam + fakes (not the
# abandoned PySide6 tree). They must collect on EVERY leg — including a Linux CI
# leg without PySide6 — so they are exempt from the PySide6 gate below.
_PYSIDE_INDEPENDENT = frozenset(
    {
        "conftest.py",
        "test_overlay_surface.py",
        "test_tray_surface.py",
    }
)


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    """Skip the PySide6-overlay tests entirely when PySide6 is absent.

    Without PySide6 their module-scope ``import overlay`` would error collection;
    ignoring them keeps the Linux CI leg clean rather than polluting the floor
    with a collection failure. The live Tk orb is covered elsewhere.

    The Wave-2 ``OverlaySurface`` tests (``_PYSIDE_INDEPENDENT``) are exempt —
    they drive the live Tk orb seam through fakes and do not import PySide6, so
    they must collect on every leg.
    """
    if _HAS_PYSIDE:
        return None
    if collection_path.name in _PYSIDE_INDEPENDENT:
        return False
    return True


@pytest.fixture(scope="session")
def qapp():
    """Single QApplication pro Session. PySide6 erlaubt nur eine Instanz."""
    pyside = pytest.importorskip("PySide6.QtWidgets")
    app = pyside.QApplication.instance() or pyside.QApplication([])
    yield app
    # KEIN app.quit() — das wuerde nachfolgende Tests killen.
