"""make_autostart_manager: platform routing + headless null-fallback."""

from __future__ import annotations

from jarvis.autostart.factory import make_autostart_manager
from jarvis.autostart.linux import LinuxAutostart
from jarvis.autostart.macos import MacOSAutostart
from jarvis.autostart.null import NullAutostart
from jarvis.autostart.windows import WindowsAutostart

from .conftest import make_caps


def test_headless_host_gets_null_regardless_of_os() -> None:
    for platform in ("win32", "darwin", "linux"):
        mgr = make_autostart_manager(make_caps(platform=platform, display_present=False))
        assert isinstance(mgr, NullAutostart)


def test_windows_routes_to_windows_manager() -> None:
    mgr = make_autostart_manager(make_caps(platform="win32", display_present=True))
    assert isinstance(mgr, WindowsAutostart)


def test_darwin_routes_to_macos_manager() -> None:
    mgr = make_autostart_manager(make_caps(platform="darwin", display_present=True))
    assert isinstance(mgr, MacOSAutostart)


def test_linux_routes_to_linux_manager() -> None:
    mgr = make_autostart_manager(make_caps(platform="linux", display_present=True))
    assert isinstance(mgr, LinuxAutostart)
