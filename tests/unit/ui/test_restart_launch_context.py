"""A desktop restart keeps the endpoint and source environment it was given."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.ui import relauncher


@pytest.fixture(autouse=True)
def _isolated_context(monkeypatch):
    monkeypatch.setattr(relauncher, "_desktop_launch_args", ())


def test_desktop_overrides_are_replaced_for_each_launch():
    relauncher.remember_desktop_launch_args(port=47869, dev=True, no_lock=True)
    assert relauncher.desktop_launch_args() == ("--port", "47869", "--dev", "--no-lock")
    relauncher.remember_desktop_launch_args(port=None, dev=False, no_lock=False)
    assert relauncher.desktop_launch_args() == ()


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_dev_restart_keeps_explicit_overrides_and_active_interpreter(monkeypatch, platform):
    from jarvis.core import instance

    identity = SimpleNamespace(is_default=False, launcher_args=("--instance", "dev"))
    monkeypatch.setattr(instance, "current_instance", lambda: identity)
    monkeypatch.setattr(relauncher.sys, "platform", platform)
    monkeypatch.setattr(relauncher, "_existing_branded_launcher", lambda: None)
    executable = "/isolated/.venv/bin/python"
    args = ("--port", "47869", "--dev", "--no-lock")
    assert relauncher.build_launch_command(executable, launcher_args=args) == [
        executable,
        "-m",
        relauncher.LAUNCHER_MODULE,
        "--instance",
        "dev",
        *args,
    ]


def test_macos_default_keeps_launchservices_and_passes_overrides(monkeypatch, tmp_path):
    from jarvis.core import instance
    from jarvis.setup import macos_app_bundle

    monkeypatch.setattr(
        instance,
        "current_instance",
        lambda: SimpleNamespace(
            is_default=True,
            launcher_args=(),
        ),
    )
    monkeypatch.setattr(relauncher.sys, "platform", "darwin")
    bundle = tmp_path / "Personal Jarvis.app"
    monkeypatch.setattr(macos_app_bundle, "macos_app_bundle_path", lambda: bundle)
    monkeypatch.setattr(macos_app_bundle, "macos_app_bundle_is_launchable", lambda _: True)
    args = ("--port", "47869", "--no-lock")
    assert relauncher.build_launch_command("python", launcher_args=args) == [
        "/usr/bin/open",
        "-W",
        "-a",
        str(bundle),
        "--args",
        *args,
    ]


def test_explicit_port_probe_never_uses_the_configured_instance_port(monkeypatch):
    addresses = []

    def forbidden_config_port(_cwd):
        raise AssertionError("An explicit endpoint must not be re-derived from config")

    monkeypatch.setattr(relauncher, "_restart_admin_port", forbidden_config_port)
    assert relauncher._desktop_is_serving(
        cwd="isolated",
        port=47869,
        _connect=lambda address, **kwargs: addresses.append(address) or nullcontext(),
    )
    assert addresses == [("127.0.0.1", 47869)]


def test_helper_preserves_overrides_cwd_and_venv_without_repinning(monkeypatch, tmp_path):
    from jarvis.core import instance
    from jarvis.ui import desktop_log

    monkeypatch.setattr(
        instance,
        "current_instance",
        lambda: SimpleNamespace(
            is_default=False,
            launcher_args=("--instance", "dev"),
        ),
    )
    monkeypatch.setattr(relauncher.sys, "platform", "linux")
    executable = str(tmp_path / ".venv" / "bin" / "python")
    monkeypatch.setattr(relauncher.sys, "executable", executable)
    monkeypatch.setattr(relauncher, "fresh_user_env", lambda: {"VIRTUAL_ENV": "preserved"})
    monkeypatch.setattr(desktop_log, "_install_desktop_log_sink", lambda _: None)
    spawned = []
    probes = []
    monkeypatch.setattr(relauncher, "_desktop_is_serving", lambda **kw: probes.append(kw) or True)
    args = ("--port", "47869", "--dev", "--no-lock")
    assert (
        relauncher.main(
            ["123", str(tmp_path), *args],
            _spawn=lambda cmd, **kw: spawned.append((cmd, kw)) or SimpleNamespace(pid=456),
            _alive=lambda _: False,
            _sleep=lambda _: None,
            _finalize_update=lambda _: True,
        )
        == 0
    )
    assert len(spawned) == 1
    command, kwargs = spawned[0]
    assert command == [executable, "-m", relauncher.LAUNCHER_MODULE, "--instance", "dev", *args]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"] == {"VIRTUAL_ENV": "preserved"}
    assert probes and all(p == {"cwd": str(tmp_path), "port": 47869} for p in probes)


@pytest.mark.parametrize("args", [["--port", "0"], ["--port", "65536"], ["--port"], ["--other"]])
def test_invalid_helper_overrides_do_not_start_or_stop_any_process(args):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Invalid overrides must be rejected before lifecycle work")

    assert relauncher.main(["123", "isolated", *args], _spawn=forbidden, _alive=forbidden) == 2


def test_desktop_sends_context_to_detached_helper_without_running_it(monkeypatch):
    import jarvis
    from jarvis.ui import desktop_app

    relauncher.remember_desktop_launch_args(port=47869, dev=True, no_lock=True)
    spawned = []
    threads = []
    monkeypatch.setattr(relauncher, "fresh_user_env", lambda: {"JARVIS_INSTANCE": "dev"})
    monkeypatch.setattr(relauncher, "spawn_detached", lambda cmd, **kw: spawned.append((cmd, kw)))

    class DeferredThread:
        def __init__(self, **kwargs):
            threads.append(kwargs)

        def start(self):
            pass  # Keep the real quit callback unexecuted in this test.

    monkeypatch.setattr(desktop_app.threading, "Thread", DeferredThread)
    app = SimpleNamespace(_window=object())
    assert desktop_app.DesktopApp._schedule_restart(app, drop_elevation=False)[0]
    command, kwargs = spawned[0]
    root = str(Path(jarvis.__file__).resolve().parent.parent)
    assert command == [
        desktop_app.sys.executable,
        "-m",
        "jarvis.ui.relauncher",
        str(desktop_app.os.getpid()),
        root,
        "--port",
        "47869",
        "--dev",
        "--no-lock",
    ]
    assert kwargs == {"cwd": root, "env": {"JARVIS_INSTANCE": "dev"}}
    assert len(threads) == 1 and threads[0]["daemon"] is True
