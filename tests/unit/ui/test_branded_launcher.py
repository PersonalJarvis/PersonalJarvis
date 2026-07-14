"""Regression guard for the mascot-branded launcher exe (taskbar-icon fix).

The Windows taskbar button takes its icon from the executable that OWNS the
window — NOT the window/class icon, AUMID, Start-Menu shortcut, registry, or
icon cache (all verified to have no effect on the button). A bare
``pythonw.exe`` launch therefore shows the Python logo on the taskbar. The fix
re-execs the launcher through ``PersonalJarvis.exe`` — a copy of the *base*
interpreter's ``pythonw`` (the real window-owner; a venv launcher only
redirects to it) carrying the mascot icon, with the venv re-attached via
``__PYVENV_LAUNCHER__``. These tests pin the contract without spawning a GUI.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

from jarvis.ui import icon_utils


def test_branded_launcher_api_is_importable() -> None:
    for name in (
        "ensure_branded_launcher_exe",
        "maybe_reexec_through_branded_launcher",
        "BRANDED_LAUNCHER_EXE_NAME",
    ):
        assert hasattr(icon_utils, name)
    assert icon_utils.BRANDED_LAUNCHER_EXE_NAME.lower().endswith(".exe")


def test_launcher_wires_the_reexec() -> None:
    """main() must call the re-exec chokepoint, or nothing brands the taskbar."""
    import jarvis.ui.web.launcher as launcher

    src = inspect.getsource(launcher)
    assert "maybe_reexec_through_branded_launcher" in src


def test_non_windows_never_brands() -> None:
    """Everything is a no-op off Windows (Linux/macOS brand via .desktop/iconphoto)."""
    if sys.platform == "win32":
        pytest.skip("Windows path covered elsewhere")
    assert icon_utils.ensure_branded_launcher_exe() is None
    assert icon_utils.maybe_reexec_through_branded_launcher([]) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branding")
def test_reexec_is_guarded_against_relaunch_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env marker set on the child must short-circuit a second re-exec."""
    monkeypatch.setenv(icon_utils._BRANDED_LAUNCH_ENV, "1")
    assert icon_utils.maybe_reexec_through_branded_launcher(["--x"]) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branding")
def test_reexec_skips_a_debug_console_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A visible-console/debug run must not be swallowed by a windowless re-exec."""
    monkeypatch.delenv(icon_utils._BRANDED_LAUNCH_ENV, raising=False)
    monkeypatch.setenv("JARVIS_DEBUG", "1")
    assert icon_utils.maybe_reexec_through_branded_launcher([]) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branding")
def test_branded_candidates_prefer_base_then_user_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Candidate 1 sits beside the BASE pythonw (zero-copy DLL adjacency);
    candidate 2 is the always-writable per-user dir — the fix for machines
    where the base dir is read-only (Program Files installs)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    candidates = icon_utils._branded_launcher_candidates()
    if not candidates:
        pytest.skip("no base pythonw resolvable in this environment")
    assert candidates[0].parent == Path(sys.base_prefix)
    assert candidates[1] == tmp_path / "PersonalJarvis" / "bin" / (
        icon_utils.BRANDED_LAUNCHER_EXE_NAME
    )
    assert all(c.name == icon_utils.BRANDED_LAUNCHER_EXE_NAME for c in candidates)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branding")
def test_no_localappdata_means_base_candidate_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    candidates = icon_utils._branded_launcher_candidates()
    if not candidates:
        pytest.skip("no base pythonw resolvable in this environment")
    assert len(candidates) == 1
    assert candidates[0].parent == Path(sys.base_prefix)


def _fake_interpreter_dir(tmp_path: Path) -> Path:
    """A fake base-interpreter dir: pythonw stub + the runtime DLL set."""
    src_dir = tmp_path / "base"
    src_dir.mkdir()
    (src_dir / "pythonw.exe").write_bytes(b"MZ fake interpreter")
    (src_dir / "python311.dll").write_bytes(b"dll")
    (src_dir / "python3.dll").write_bytes(b"dll")
    (src_dir / "vcruntime140.dll").write_bytes(b"dll")
    return src_dir


def _brandable(monkeypatch: pytest.MonkeyPatch, *, boots: bool = True) -> None:
    """Stub the two Win32-only steps so copy/DLL logic is testable anywhere."""
    monkeypatch.setattr(icon_utils, "_replace_exe_icon", lambda *_a: True)
    monkeypatch.setattr(icon_utils, "_branded_copy_boots", lambda *_a: boots)


def test_relocated_copy_carries_the_runtime_dlls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A copy OUTSIDE the base dir must bring python3*/vcruntime140* along —
    without them the relocated exe dies before any window exists."""
    _brandable(monkeypatch)
    src_dir = _fake_interpreter_dir(tmp_path)
    ico = tmp_path / "jarvis.ico"
    ico.write_bytes(b"ico")
    target = tmp_path / "user" / "bin" / icon_utils.BRANDED_LAUNCHER_EXE_NAME

    built = icon_utils._ensure_branded_copy_at(src_dir / "pythonw.exe", target, ico)

    assert built == target
    assert target.is_file()
    for dll in ("python311.dll", "python3.dll", "vcruntime140.dll"):
        assert (target.parent / dll).is_file(), dll


def test_relocated_copy_that_fails_the_smoke_start_is_discarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relocated copy that cannot boot would strand the user with NO app
    (the re-exec parent already exited) — it must be deleted, not returned."""
    _brandable(monkeypatch, boots=False)
    src_dir = _fake_interpreter_dir(tmp_path)
    ico = tmp_path / "jarvis.ico"
    ico.write_bytes(b"ico")
    target = tmp_path / "user" / "bin" / icon_utils.BRANDED_LAUNCHER_EXE_NAME

    assert icon_utils._ensure_branded_copy_at(src_dir / "pythonw.exe", target, ico) is None
    assert not target.exists()


def test_relocated_copy_missing_a_dll_is_rebuilt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Freshness must include the DLL set: an exe-only leftover (older partial
    build) cannot start and would otherwise be trusted forever."""
    _brandable(monkeypatch)
    src_dir = _fake_interpreter_dir(tmp_path)
    ico = tmp_path / "jarvis.ico"
    ico.write_bytes(b"ico")
    target = tmp_path / "user" / "bin" / icon_utils.BRANDED_LAUNCHER_EXE_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"stale exe, no dlls")
    import os as _os
    import time as _time

    future = _time.time() + 3600  # newer than every input → "fresh" by mtime alone
    _os.utime(target, (future, future))

    built = icon_utils._ensure_branded_copy_at(src_dir / "pythonw.exe", target, ico)

    assert built == target
    assert (target.parent / "python311.dll").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branding")
def test_unwritable_first_home_falls_through_to_the_user_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """THE fresh-machine fix: base dir not writable (Program Files / non-admin)
    must not end branding — the per-user candidate takes over."""
    _brandable(monkeypatch)
    src_dir = _fake_interpreter_dir(tmp_path)
    src = src_dir / "pythonw.exe"
    ico = tmp_path / "jarvis.ico"
    ico.write_bytes(b"ico")
    # Candidate 1's parent is a FILE → every write attempt there fails, like a
    # read-only Program Files dir. Candidate 2 is a writable user dir.
    blocker = tmp_path / "readonly"
    blocker.write_bytes(b"not a directory")
    bad = blocker / icon_utils.BRANDED_LAUNCHER_EXE_NAME
    good = tmp_path / "user" / "bin" / icon_utils.BRANDED_LAUNCHER_EXE_NAME
    monkeypatch.setattr(icon_utils, "_base_pythonw_executable", lambda: src)
    monkeypatch.setattr(icon_utils, "_branded_launcher_candidates", lambda: [bad, good])
    monkeypatch.setattr(icon_utils, "project_icon_path", lambda: ico)

    assert icon_utils.ensure_branded_launcher_exe() == good
    assert good.is_file()
    assert (good.parent / "python311.dll").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branding")
def test_ms_store_alias_base_is_not_branded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 0-byte base exe (the MS Store app-execution alias) must bail gracefully."""
    fake_base = icon_utils._base_pythonw_executable()
    if fake_base is None:
        pytest.skip("no base pythonw resolvable")

    real_stat = os.stat

    class _ZeroStat:
        def __init__(self, st):
            self._st = st

        def __getattr__(self, k):
            if k == "st_size":
                return 0
            return getattr(self._st, k)

    def _fake_stat(path, *a, **k):
        st = real_stat(path, *a, **k)
        if Path(path) == fake_base:
            return _ZeroStat(st)
        return st

    monkeypatch.setattr(icon_utils.os, "stat", _fake_stat, raising=False)
    # Path.stat() is used in the code; patch that too.
    orig_path_stat = Path.stat

    def _fake_path_stat(self, *a, **k):
        st = orig_path_stat(self, *a, **k)
        if self == fake_base:
            return _ZeroStat(st)
        return st

    monkeypatch.setattr(Path, "stat", _fake_path_stat, raising=False)
    assert icon_utils.ensure_branded_launcher_exe() is None
