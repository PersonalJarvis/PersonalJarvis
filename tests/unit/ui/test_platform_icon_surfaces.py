"""Non-Windows app-icon surfaces: Linux applications entry + macOS Dock.

Windows shows the taskbar icon of the LAUNCHING exe (covered by
``test_branded_launcher.py``). The other desktops have their own binding:

* **Linux** — the dock/taskbar maps a running window to a ``.desktop`` entry
  under ``$XDG_DATA_HOME/applications`` by matching the window's WM_CLASS
  against ``StartupWMClass``, and renders THAT entry's ``Icon=``. Without the
  entry the dock shows the generic python3 icon and app search finds nothing.
* **macOS** — a bare interpreter run shows the Python rocket in the Dock; the
  runtime override is ``NSApplication.setApplicationIconImage_``.

The Linux entry is pure text I/O behind a test seam (``applications_dir``), so
its contract is provable on every OS.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis.ui import icon_utils


def test_linux_desktop_entry_binds_icon_to_the_pinned_wm_class(tmp_path: Path) -> None:
    """The entry must carry the SAME class token ``pin_linux_wm_class`` pins —
    the icon binding only works when the two halves match."""
    apps = tmp_path / "applications"

    assert icon_utils.ensure_linux_desktop_entry(applications_dir=apps) is True

    entry = apps / icon_utils.LINUX_DESKTOP_ENTRY_NAME
    text = entry.read_text(encoding="utf-8")
    assert f"StartupWMClass={icon_utils.LINUX_WM_CLASS}" in text
    assert f"Name={icon_utils.APP_DISPLAY_NAME}" in text
    assert "-m jarvis.ui.web.launcher" in text
    assert "Terminal=false" in text
    # The autostart-only keys must NOT leak into the menu entry.
    assert "X-GNOME-Autostart" not in text


def test_linux_desktop_entry_is_idempotent(tmp_path: Path) -> None:
    apps = tmp_path / "applications"
    assert icon_utils.ensure_linux_desktop_entry(applications_dir=apps) is True
    entry = apps / icon_utils.LINUX_DESKTOP_ENTRY_NAME
    first = entry.read_text(encoding="utf-8")
    first_mtime = entry.stat().st_mtime_ns

    assert icon_utils.ensure_linux_desktop_entry(applications_dir=apps) is True

    assert entry.read_text(encoding="utf-8") == first
    # Unchanged content is not rewritten (no churn for file watchers).
    assert entry.stat().st_mtime_ns == first_mtime


def test_linux_desktop_entry_never_raises(tmp_path: Path) -> None:
    """Best-effort contract: an unwritable target degrades to False, no crash."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"file blocks mkdir")

    assert icon_utils.ensure_linux_desktop_entry(applications_dir=blocker) is False


@pytest.mark.skipif(sys.platform == "linux", reason="covers the non-Linux no-op")
def test_linux_desktop_entry_noop_off_linux_without_seam() -> None:
    assert icon_utils.ensure_linux_desktop_entry() is False


@pytest.mark.skipif(sys.platform == "darwin", reason="covers the non-macOS no-op")
def test_macos_dock_icon_noop_off_darwin() -> None:
    assert icon_utils.apply_macos_dock_icon() is False


def test_desktop_app_wires_all_three_platform_surfaces() -> None:
    """The pre-window hook must call every per-OS icon surface, or one desktop
    silently regresses to the interpreter icon."""
    import inspect

    import jarvis.ui.desktop_app as desktop_app

    src = inspect.getsource(desktop_app)
    for hook in (
        "pin_linux_wm_class",
        "ensure_linux_desktop_entry",
        "apply_macos_dock_icon",
    ):
        assert hook in src, hook
