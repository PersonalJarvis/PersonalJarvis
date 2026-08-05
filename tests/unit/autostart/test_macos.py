"""MacOSAutostart: LaunchAgent plist write / status / drift (CI-provable).

launchctl is darwin-gated, so on the (non-darwin) CI host these tests exercise
only the pure plist write/parse path — exactly what we can prove anywhere.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

import jarvis.autostart.macos as macos
from jarvis.autostart.macos import MacOSAutostart
from jarvis.autostart.protocol import LaunchSpec


def _spec(program: str = "/usr/bin/open", working_dir: str = "/Users/u/jarvis") -> LaunchSpec:
    return LaunchSpec(
        program=program,
        args=("-W", "-a", "/Users/u/Applications/Personal Jarvis.app"),
        working_dir=working_dir,
        minimized=True,
    )


def test_install_writes_launchagent_plist(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(macos, "_agents_dir", lambda: tmp_path)
    mgr = MacOSAutostart()
    status = mgr.install(_spec())

    plist_path = tmp_path / "com.personal-jarvis.autostart.plist"
    assert plist_path.exists()
    with plist_path.open("rb") as fh:
        data = plistlib.load(fh)
    assert data["Label"] == "com.personal-jarvis.autostart"
    assert data["ProgramArguments"] == [
        "/usr/bin/open",
        "-W",
        "-a",
        "/Users/u/Applications/Personal Jarvis.app",
    ]
    assert data["RunAtLoad"] is True
    assert data["LimitLoadToSessionType"] == "Aqua"
    assert status.matches_spec is True


def test_status_detects_drift(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(macos, "_agents_dir", lambda: tmp_path)
    mgr = MacOSAutostart()
    mgr.install(_spec(program="/old/python3"))
    assert mgr.status(_spec(program="/new/python3")).matches_spec is False


def test_status_refreshes_legacy_non_aqua_entry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(macos, "_agents_dir", lambda: tmp_path)
    mgr = MacOSAutostart()
    mgr.install(_spec())
    plist_path = tmp_path / "com.personal-jarvis.autostart.plist"
    with plist_path.open("rb") as fh:
        data = plistlib.load(fh)
    data.pop("LimitLoadToSessionType")
    with plist_path.open("wb") as fh:
        plistlib.dump(data, fh)

    assert mgr.status(_spec()).matches_spec is False


def test_uninstall_removes_plist(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(macos, "_agents_dir", lambda: tmp_path)
    mgr = MacOSAutostart()
    mgr.install(_spec())
    status = mgr.uninstall()
    assert not (tmp_path / "com.personal-jarvis.autostart.plist").exists()
    assert status.installed is False


def test_uninstall_reports_a_plist_it_could_not_remove(monkeypatch, tmp_path: Path) -> None:
    """Reporting "Autostart disabled." while the LaunchAgent survives is a lie
    — RunAtLoad still starts Jarvis at the next login, and the Settings toggle
    showed off with nothing to explain the mismatch."""
    monkeypatch.setattr(macos, "_agents_dir", lambda: tmp_path)
    mgr = MacOSAutostart()
    mgr.install(_spec())

    def _denied(self: Path, **_kwargs: object) -> None:
        raise OSError("Operation not permitted")

    monkeypatch.setattr(Path, "unlink", _denied)
    status = mgr.uninstall()

    assert status.installed is True
    assert status.matches_spec is False
    assert (tmp_path / "com.personal-jarvis.autostart.plist").exists()


def test_install_leaves_no_temp_file_when_the_plist_write_fails(
    monkeypatch, tmp_path: Path,
) -> None:
    """A half-written .plist.tmp next to the real entry is debris — the
    successful replace() consumes the temp file, so anything left is a
    failed write."""
    monkeypatch.setattr(macos, "_agents_dir", lambda: tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(macos.plistlib, "dump", _boom)
    with pytest.raises(OSError, match="disk full"):
        MacOSAutostart().install(_spec())

    assert list(tmp_path.glob("*.tmp")) == []
