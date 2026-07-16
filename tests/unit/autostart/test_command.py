"""resolve_launch_spec: interpreter selection + path derived from the package."""

from __future__ import annotations

import sys

from jarvis.autostart.command import LAUNCHER_MODULE, resolve_launch_spec
from jarvis.core.config import PROJECT_ROOT

from .conftest import make_cfg


def test_args_target_the_full_voice_launcher(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    spec = resolve_launch_spec(make_cfg())
    assert spec.args == ("-m", LAUNCHER_MODULE)
    assert LAUNCHER_MODULE == "jarvis.ui.web.launcher"


def test_working_dir_is_the_running_project_root() -> None:
    # This is the BUG-006 stale-path defense: the entry targets the clone that
    # is actually running, never a frozen absolute string.
    spec = resolve_launch_spec(make_cfg())
    assert spec.working_dir == str(PROJECT_ROOT)


def test_program_is_python_on_posix(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    spec = resolve_launch_spec(make_cfg())
    assert spec.program == sys.executable


def test_program_uses_macos_bundle_identity(monkeypatch, tmp_path) -> None:
    import jarvis.setup.macos_app_bundle as bundle_module

    bundle = tmp_path / "Personal Jarvis.app"
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(bundle_module, "macos_app_bundle_path", lambda: bundle)
    monkeypatch.setattr(
        bundle_module, "macos_app_bundle_is_launchable", lambda _bundle: True
    )

    spec = resolve_launch_spec(make_cfg(start_minimized=True))

    assert spec.program == "/usr/bin/open"
    assert spec.args == ("-g", "-W", "-a", str(bundle))


def test_program_fails_closed_to_launchservices_without_macos_bundle(
    monkeypatch, tmp_path
) -> None:
    import jarvis.setup.macos_app_bundle as bundle_module

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        bundle_module,
        "macos_app_bundle_path",
        lambda: tmp_path / "missing.app",
    )

    spec = resolve_launch_spec(make_cfg(start_minimized=False))

    assert spec.program == "/usr/bin/open"
    assert spec.args == ("-W", "-a", str(tmp_path / "missing.app"))


def test_program_prefers_pythonw_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "jarvis.autostart.command._detect_pythonw", lambda: r"C:\py\pythonw.exe"
    )
    spec = resolve_launch_spec(make_cfg())
    assert spec.program == r"C:\py\pythonw.exe"


def test_minimized_follows_config() -> None:
    assert resolve_launch_spec(make_cfg(start_minimized=False)).minimized is False
    assert resolve_launch_spec(make_cfg(start_minimized=True)).minimized is True


def test_command_line_joins_program_and_args(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    spec = resolve_launch_spec(make_cfg())
    assert spec.command_line().endswith("-m jarvis.ui.web.launcher")
