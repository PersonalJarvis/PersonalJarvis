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


class TestShortcutTargetsOurOwnExe:
    """Windows will not list a shortcut aimed at a GENERIC HOST as an app.

    Forensic 2026-08-16, second half: the .lnk existed, opened the app on a
    double-click, and was still absent from Windows Search. Its target was
    `pythonw.exe`. The shell applies the same rule to its own entries — that is
    why `Command Prompt` (cmd.exe), `Run` (rundll32.exe) and `File Explorer` are
    likewise missing from the app list, while Obsidian and Discord, each aimed
    at its own .exe, are present.

    So the shortcut has to point at OUR exe. The in-venv `PersonalJarvis.exe`
    is the one branded copy that needs no environment handed to it, which is
    exactly what a shortcut can't supply.
    """

    def test_prefers_a_self_contained_branded_exe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        branded = tmp_path / "Scripts" / "PersonalJarvis.exe"
        monkeypatch.setattr(icon_utils, "ensure_branded_launcher_exe", lambda: branded)
        monkeypatch.setattr(
            icon_utils, "_is_self_contained_branded_exe", lambda p: True
        )
        assert icon_utils._shortcut_launch_target() == branded

    def test_falls_back_to_pythonw_when_the_copy_needs_an_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A per-user-dir copy needs __PYVENV_LAUNCHER__, which a shortcut has
        no way to set — pointing at it would produce an entry that does nothing."""
        branded = tmp_path / "bin" / "PersonalJarvis.exe"
        monkeypatch.setattr(icon_utils, "ensure_branded_launcher_exe", lambda: branded)
        monkeypatch.setattr(
            icon_utils, "_is_self_contained_branded_exe", lambda p: False
        )
        assert icon_utils._shortcut_launch_target() == icon_utils._pythonw_executable()

    def test_falls_back_when_nothing_can_be_branded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(icon_utils, "ensure_branded_launcher_exe", lambda: None)
        assert icon_utils._shortcut_launch_target() == icon_utils._pythonw_executable()

    @windows_only
    def test_self_contained_test_keys_off_pyvenv_cfg(self, tmp_path: Path) -> None:
        scripts = tmp_path / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        exe = scripts / "PersonalJarvis.exe"
        assert icon_utils._is_self_contained_branded_exe(exe) is False
        (tmp_path / "venv" / "pyvenv.cfg").write_text("home = x", encoding="utf-8")
        assert icon_utils._is_self_contained_branded_exe(exe) is True

    @windows_only
    def test_written_shortcut_points_at_a_real_launchable_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end: whatever target is chosen, it must exist on disk."""
        programs = tmp_path / "Programs"
        programs.mkdir()
        monkeypatch.setattr(icon_utils, "_interpreter_can_open_a_window", lambda: True)
        assert icon_utils.ensure_start_menu_shortcut(
            aumid=_TEST_AUMID, programs_dir=programs
        )
        from win32com.client import Dispatch

        sc = Dispatch("WScript.Shell").CreateShortcut(
            str(programs / icon_utils.START_MENU_SHORTCUT_NAME)
        )
        assert Path(sc.TargetPath).is_file(), "shortcut points at a missing exe"


class TestReexecLoopGuard:
    """Launching THROUGH the branded exe must not re-exec through it again."""

    @windows_only
    def test_running_as_the_branded_exe_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`sys._base_executable` still names the interpreter behind the branded
        copy (a Store alias, say), so the older guard could not see this and the
        app spawned a second process on every Start-Menu launch."""
        monkeypatch.delenv(icon_utils._BRANDED_LAUNCH_ENV, raising=False)
        monkeypatch.delenv("JARVIS_DEBUG", raising=False)
        monkeypatch.setattr(
            icon_utils.sys,
            "executable",
            r"C:\proj\.venv\Scripts\PersonalJarvis.exe",
        )
        monkeypatch.setattr(
            icon_utils.sys, "_base_executable", r"C:\store\pythonw.exe", raising=False
        )
        assert icon_utils.maybe_reexec_through_branded_launcher([]) is None


class TestShellIsToldAboutTheEntry:
    """Writing the file is half the job — the app index needs the notification.

    Windows answers Start-menu searches from `shell:AppsFolder`, refreshed from
    shell change notifications. The .lnk is finished by an IPropertyStore commit
    *after* Save(), which announces nothing, so without this the entry can sit
    on disk unindexed.
    """

    def test_writer_announces_the_entry(self) -> None:
        import inspect

        src = inspect.getsource(icon_utils.ensure_start_menu_shortcut)
        assert "_notify_shell_of_shortcut(lnk)" in src
        # …after the property-store commit, which is the last write to the file.
        assert src.index("rw_store.Commit()") < src.index("_notify_shell_of_shortcut")

    def test_is_a_noop_off_windows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(icon_utils.sys, "platform", "linux")
        icon_utils._notify_shell_of_shortcut(tmp_path / "whatever.lnk")

    @windows_only
    def test_notifies_the_item_and_its_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ctypes

        lnk = tmp_path / "Programs" / "Personal Jarvis.lnk"
        calls: list[tuple[int, object]] = []
        monkeypatch.setattr(
            ctypes.windll.shell32,
            "SHChangeNotify",
            lambda event, flags, a, b: calls.append((event, a.value)),
            raising=False,
        )
        icon_utils._notify_shell_of_shortcut(lnk)
        events = [c[0] for c in calls]
        assert events == [0x0002, 0x1000], "announce the item, then its folder"
        assert calls[0][1] == str(lnk)
        assert calls[1][1] == str(lnk.parent)

    @windows_only
    def test_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failed notification must never take the shortcut write down."""
        import ctypes

        def _explode(*a: object) -> None:
            raise OSError("shell is unavailable")

        monkeypatch.setattr(
            ctypes.windll.shell32, "SHChangeNotify", _explode, raising=False
        )
        icon_utils._notify_shell_of_shortcut(Path("C:/nope/x.lnk"))


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
