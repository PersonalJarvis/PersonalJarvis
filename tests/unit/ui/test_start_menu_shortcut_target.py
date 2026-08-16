"""The Start-Menu shortcut must never be aimed at an interpreter without a window.

Forensic 2026-08-16 — "the app doesn't start any more, neither from the CLI nor
from Windows Search":

``jarvis/ui/desktop_app.py`` called ``ensure_windows_app_identity()`` as an
*import* side effect, and that call rewrites the Start-Menu shortcut to point at
``sys.executable``. But importing ``desktop_app`` is not the same as opening a
window — a ``--headless`` boot, a ``jarvis <group>`` control command and the test
suite all import it too. So whichever interpreter imported the module last won
the shortcut. Once that was a virtualenv without pywebview, Windows Search
launched a ``pythonw`` that died on ``import webview`` — and ``pythonw`` has no
console, so the error went nowhere and the app simply never appeared.

Two independent guards, one test class each:
  * the shortcut writer refuses an interpreter that cannot open a window, and
  * the import path no longer writes the shortcut at all.

Both are checked without touching the live Start Menu: a throwaway
``programs_dir`` everywhere a shortcut may be written.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis.ui import icon_utils

_TEST_AUMID = "PersonalJarvis.Test.ShortcutTarget"

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="Start-Menu shortcuts are Windows-only"
)


class TestWindowCapabilityProbe:
    """``_interpreter_can_open_a_window`` decides whether we may claim the entry."""

    def test_matches_the_actual_import_state(self) -> None:
        import importlib.util

        expected = importlib.util.find_spec("webview") is not None
        assert icon_utils._interpreter_can_open_a_window() is expected

    def test_never_imports_pywebview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """It runs on the boot critical path, so resolving must stay stat-only."""
        import importlib

        real_import = importlib.import_module

        def _boom(name: str, *a: object, **k: object) -> object:
            if name == "webview" or name.startswith("webview."):
                raise AssertionError("probe imported pywebview; find_spec only")
            return real_import(name, *a, **k)

        monkeypatch.setattr(importlib, "import_module", _boom)
        icon_utils._interpreter_can_open_a_window()

    def test_broken_install_reads_as_no_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A half-removed package raises from find_spec; treat that as unusable."""
        import importlib.util

        def _raise(name: str) -> object:
            raise ValueError("half-removed distribution")

        monkeypatch.setattr(importlib.util, "find_spec", _raise)
        assert icon_utils._interpreter_can_open_a_window() is False


@windows_only
class TestShortcutWriterRefusesADeadTarget:
    def test_leaves_an_existing_shortcut_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The regression itself: a window-less run must not steal the entry."""
        programs = tmp_path / "Programs"
        programs.mkdir()
        lnk = programs / icon_utils.START_MENU_SHORTCUT_NAME
        lnk.write_bytes(b"pretend this is the installer's working shortcut")
        before = lnk.read_bytes()

        monkeypatch.setattr(icon_utils, "_interpreter_can_open_a_window", lambda: False)
        result = icon_utils.ensure_start_menu_shortcut(
            aumid=_TEST_AUMID, programs_dir=programs
        )

        assert lnk.read_bytes() == before, "a window-less run rewrote the shortcut"
        assert result is True, "an existing entry is still present, so report it"

    def test_writes_no_shortcut_where_none_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Better no Start-Menu entry than one that opens nothing."""
        programs = tmp_path / "Programs"
        programs.mkdir()
        monkeypatch.setattr(icon_utils, "_interpreter_can_open_a_window", lambda: False)

        result = icon_utils.ensure_start_menu_shortcut(
            aumid=_TEST_AUMID, programs_dir=programs
        )

        assert not (programs / icon_utils.START_MENU_SHORTCUT_NAME).exists()
        assert result is False

    def test_writes_the_shortcut_when_the_interpreter_can_open_a_window(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard must not break the case it is meant to protect."""
        programs = tmp_path / "Programs"
        programs.mkdir()
        monkeypatch.setattr(icon_utils, "_interpreter_can_open_a_window", lambda: True)

        result = icon_utils.ensure_start_menu_shortcut(
            aumid=_TEST_AUMID, programs_dir=programs
        )

        lnk = programs / icon_utils.START_MENU_SHORTCUT_NAME
        assert result is True and lnk.is_file()

        from win32com.client import Dispatch

        sc = Dispatch("WScript.Shell").CreateShortcut(str(lnk))
        assert Path(sc.TargetPath).is_file(), "shortcut points at a missing exe"
        assert sc.Arguments.strip() == f"-m {icon_utils._LAUNCHER_MODULE}"


@windows_only
class TestIdentityCallCanSkipTheShortcut:
    def test_write_shortcut_false_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[object] = []
        monkeypatch.setattr(
            icon_utils,
            "ensure_start_menu_shortcut",
            lambda **kw: calls.append(kw) or True,
        )
        icon_utils.ensure_windows_app_identity(_TEST_AUMID, write_shortcut=False)
        assert not calls, "import-path identity call must not write the shortcut"

    def test_default_still_writes_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A real window run keeps claiming the entry — the taskbar name needs it."""
        calls: list[object] = []
        monkeypatch.setattr(
            icon_utils,
            "ensure_start_menu_shortcut",
            lambda **kw: calls.append(kw) or True,
        )
        icon_utils.ensure_windows_app_identity(_TEST_AUMID)
        assert calls, "a window run must still name its taskbar button"


class TestImportPathHasNoShortcutSideEffect:
    """Source-level guard — the import side effect is what caused the outage."""

    def test_desktop_app_import_passes_write_shortcut_false(self) -> None:
        src = (
            Path(icon_utils.__file__).resolve().parent / "desktop_app.py"
        ).read_text(encoding="utf-8-sig")
        head = src.split("# Constants", 1)[0]
        assert "ensure_windows_app_identity(write_shortcut=False)" in head, (
            "importing jarvis.ui.desktop_app must not rewrite the Start-Menu "
            "shortcut — an import is no promise of a window"
        )
