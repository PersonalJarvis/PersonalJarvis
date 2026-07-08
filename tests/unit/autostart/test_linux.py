"""LinuxAutostart: XDG .desktop write / status / drift / uninstall (CI-provable)."""

from __future__ import annotations

from pathlib import Path

from jarvis.autostart.linux import LinuxAutostart
from jarvis.autostart.protocol import LaunchSpec


def _spec(
    program: str = "/usr/bin/python3",
    working_dir: str = "/home/u/Personal Jarvis",
) -> LaunchSpec:
    return LaunchSpec(
        program=program,
        args=("-m", "jarvis.ui.web.launcher"),
        working_dir=working_dir,
        minimized=True,
    )


def test_install_writes_desktop_entry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    status = mgr.install(_spec())

    entry = tmp_path / "autostart" / "personal-jarvis.desktop"
    assert entry.exists()
    text = entry.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in text
    assert "Exec=/usr/bin/python3 -m jarvis.ui.web.launcher" in text
    assert "X-GNOME-Autostart-enabled=true" in text
    assert status.installed is True
    assert status.matches_spec is True


def test_install_brands_entry_with_icon_and_wmclass(monkeypatch, tmp_path: Path) -> None:
    """The .desktop must carry an Icon= (the bundled PNG) and a StartupWMClass so
    the app menu / taskbar shows Jarvis, not the generic python3 interpreter icon."""
    from jarvis.assets import bundled_app_icon_png

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    LinuxAutostart().install(_spec())
    text = (tmp_path / "autostart" / "personal-jarvis.desktop").read_text(encoding="utf-8")

    png = bundled_app_icon_png()
    assert png is not None, "bundled jarvis.png must ship for the Linux Icon= key"
    assert f"Icon={png}" in text
    assert "StartupWMClass=personal-jarvis" in text


def test_status_detects_path_drift(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    mgr.install(_spec(program="/old/python3"))

    # The running install now resolves to a different interpreter path.
    drifted = mgr.status(_spec(program="/new/python3"))
    assert drifted.installed is True
    assert drifted.matches_spec is False


def test_program_with_spaces_is_quoted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    spec = _spec(program="/opt/My Apps/python3")
    mgr.install(spec)
    text = (tmp_path / "autostart" / "personal-jarvis.desktop").read_text(encoding="utf-8")
    assert 'Exec="/opt/My Apps/python3" -m jarvis.ui.web.launcher' in text
    # And the round-trip still matches.
    assert mgr.status(spec).matches_spec is True


def test_uninstall_removes_entry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = LinuxAutostart()
    mgr.install(_spec())
    status = mgr.uninstall()
    assert not (tmp_path / "autostart" / "personal-jarvis.desktop").exists()
    assert status.installed is False


def test_status_supported_even_when_absent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    status = LinuxAutostart().status(_spec())
    assert status.supported is True
    assert status.installed is False
