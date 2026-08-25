"""A desktop start that cannot happen must SAY so — even under ``pythonw``.

The Start-Menu shortcut and ``run.bat`` both launch ``pythonw``, whose standard
streams point at nothing. So the ``ModuleNotFoundError: webview`` that ends the
boot is written into the void: no window, no console, no error dialog. From the
user's side the app "just doesn't start any more" — nothing to read, nothing to
search for. These tests pin the two halves of the fix: the launcher refuses
early and explains itself, and the explanation reaches a surface a user can see.
"""
from __future__ import annotations

import sys

import pytest

from jarvis.ui.web import launcher


class TestWindowToolkitProbe:
    def test_none_when_pywebview_is_importable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib.util

        monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
        assert launcher._missing_window_toolkit() is None

    def test_names_the_package_and_both_ways_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib.util

        monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
        msg = launcher._missing_window_toolkit()
        assert msg is not None
        assert "pywebview" in msg, "the user must learn WHAT is missing"
        assert "personal-jarvis[full]" in msg, "…and how to install it"
        assert "jarvis serve" in msg, "…and what already works without a window"
        assert sys.executable in msg, (
            "name the interpreter — the usual cause is a shortcut aimed at the "
            "wrong Python, which is invisible without this line"
        )

    def test_broken_install_reads_as_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib.util

        def _raise(name: str) -> object:
            raise ValueError("half-removed distribution")

        monkeypatch.setattr(importlib.util, "find_spec", _raise)
        assert launcher._missing_window_toolkit() is not None


class TestBackendServerProbe:
    def test_none_when_uvicorn_imports(self) -> None:
        assert launcher._missing_backend_server() is None

    def test_names_the_missing_package_and_the_interpreter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> None:
            raise ModuleNotFoundError("No module named 'click'", name="click")

        monkeypatch.setattr(launcher, "_import_backend_server", _boom)
        msg = launcher._missing_backend_server()
        assert msg is not None
        assert "click" in msg, "the user must learn WHAT is missing"
        assert "pip install -r requirements.txt" in msg
        assert sys.executable in msg

    def test_falls_back_to_uvicorn_when_the_exception_has_no_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> None:
            raise ModuleNotFoundError("broken install")

        monkeypatch.setattr(launcher, "_import_backend_server", _boom)
        msg = launcher._missing_backend_server()
        assert msg is not None
        assert "uvicorn" in msg


@pytest.fixture
def dialogs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Intercept the modal box.

    Every test that can reach the dialog branch MUST use this. A real
    ``MessageBoxW`` blocks the calling thread until a human clicks it, so an
    unguarded test hangs the run — which is exactly what happened while writing
    these tests.
    """
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        launcher, "_show_error_dialog", lambda title, msg: seen.append((title, msg))
    )
    return seen


class TestFailureIsVisible:
    def test_writes_to_stderr(
        self, capsys: pytest.CaptureFixture[str], dialogs: list[tuple[str, str]]
    ) -> None:
        launcher._report_startup_failure("no window toolkit here")
        assert "no window toolkit here" in capsys.readouterr().err

    def test_no_dialog_while_stderr_is_readable(
        self, monkeypatch: pytest.MonkeyPatch, dialogs: list[tuple[str, str]]
    ) -> None:
        """A terminal, a pipe or a build log already showed it — a box is noise."""
        monkeypatch.setattr(
            "jarvis.core.process_utils.standard_error_is_visible", lambda: True
        )
        launcher._report_startup_failure("readable stderr")
        assert not dialogs

    def test_dialog_when_stderr_went_nowhere(
        self, monkeypatch: pytest.MonkeyPatch, dialogs: list[tuple[str, str]]
    ) -> None:
        """The pythonw case — the only surface left is a box."""
        monkeypatch.setattr(
            "jarvis.core.process_utils.standard_error_is_visible", lambda: False
        )
        launcher._report_startup_failure("pywebview is missing")
        assert len(dialogs) == 1
        title, body = dialogs[0]
        assert "could not start" in title
        assert "pywebview is missing" in body

    def test_never_raises(
        self, monkeypatch: pytest.MonkeyPatch, dialogs: list[tuple[str, str]]
    ) -> None:
        """Reporting a failed start must not itself blow up the process."""

        def _explode(*a: object, **k: object) -> None:
            raise RuntimeError("logging sink is gone")

        monkeypatch.setattr("loguru.logger.error", _explode)
        launcher._report_startup_failure("still has to survive this")

class TestDialogOnEveryDesktopOS:
    """A GUI launch is mute on all three platforms, so all three get a dialog.

    ``sys.platform`` is patched rather than skipped on, so the macOS and Linux
    branches are exercised wherever the suite runs — including the maintainer's
    Windows box, which is the only machine most of this code is written on. A
    fake ``subprocess.run`` keeps real dialogs closed and needs no helper binary
    installed.
    """

    @pytest.fixture
    def spawned(self, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        import subprocess

        calls: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            assert kwargs.get("encoding") == "utf-8", "subprocesses must be UTF-8"
            return object()

        monkeypatch.setattr(subprocess, "run", _fake_run)
        return calls

    @staticmethod
    def _as_platform(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        monkeypatch.setattr(launcher.sys, "platform", name)

    def test_macos_uses_osascript(
        self, spawned: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._as_platform(monkeypatch, "darwin")
        launcher._show_error_dialog("Title", "Body")
        assert spawned and spawned[0][0] == "osascript"
        assert "display dialog" in spawned[0][-1]

    def test_macos_escapes_applescript_string_literals(
        self, spawned: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unescaped quote turns the message into a syntax error, i.e. silence."""
        self._as_platform(monkeypatch, "darwin")
        launcher._show_error_dialog('Ti"tle', 'say \\ and "quote"')
        script = spawned[0][-1]
        assert '\\"quote\\"' in script and "\\\\" in script

    def test_linux_prefers_zenity_and_passes_text_as_an_argument(
        self, spawned: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._as_platform(monkeypatch, "linux")
        monkeypatch.setenv("DISPLAY", ":0")
        launcher._show_error_dialog("Title", "Body; rm -rf /")
        assert spawned and spawned[0][0] == "zenity"
        # No shell anywhere, so shell metacharacters are inert data.
        assert "--text=Body; rm -rf /" in spawned[0]

    def test_linux_falls_back_to_kdialog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        calls: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            if cmd[0] == "zenity":
                raise FileNotFoundError("zenity is not installed")
            return object()

        monkeypatch.setattr(subprocess, "run", _fake_run)
        self._as_platform(monkeypatch, "linux")
        monkeypatch.setenv("DISPLAY", ":0")
        launcher._show_error_dialog("Title", "Body")
        assert [c[0] for c in calls] == ["zenity", "kdialog"]

    def test_linux_without_a_graphical_session_stays_quiet(
        self, spawned: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A headless host has nowhere to show a box; stderr and the log carry it."""
        self._as_platform(monkeypatch, "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        launcher._show_error_dialog("Title", "Body")
        assert not spawned

    def test_wayland_counts_as_a_graphical_session(
        self, spawned: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._as_platform(monkeypatch, "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        launcher._show_error_dialog("Title", "Body")
        assert spawned, "a Wayland desktop can show a dialog"

    def test_linux_survives_a_distro_with_neither_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        def _missing(cmd: list[str], **kwargs: object) -> object:
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(subprocess, "run", _missing)
        self._as_platform(monkeypatch, "linux")
        monkeypatch.setenv("DISPLAY", ":0")
        launcher._show_error_dialog("Title", "Body")  # must not raise

    def test_no_dialog_helper_ever_runs_through_a_shell(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The message is attacker-influenced only in theory, but shell=True here
        would turn a diagnostic into command execution. Pin it closed."""
        import subprocess

        seen: list[dict[str, object]] = []

        def _fake_run(cmd: list[str], **kwargs: object) -> object:
            seen.append(kwargs)
            return object()

        monkeypatch.setattr(subprocess, "run", _fake_run)
        monkeypatch.setenv("DISPLAY", ":0")
        for platform_name in ("darwin", "linux"):
            self._as_platform(monkeypatch, platform_name)
            launcher._show_error_dialog("T", "B")
        assert seen and all(not kw.get("shell") for kw in seen)


class TestStandardErrorVisibility:
    """The probe that decides whether a dialog is needed at all."""

    def test_a_real_stream_counts_as_visible(self) -> None:
        from jarvis.core.process_utils import standard_error_is_visible

        assert standard_error_is_visible() is True

    def test_the_substituted_null_device_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reproduce the pythonw substitution and confirm it reads as invisible."""
        import os

        from jarvis.core import process_utils

        null = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        try:
            monkeypatch.setattr(process_utils.sys, "stderr", null)
            monkeypatch.setattr(process_utils, "_NULL_STANDARD_STREAMS", [null])
            assert process_utils.standard_error_is_visible() is False
        finally:
            null.close()

    def test_missing_stream_does_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from jarvis.core import process_utils

        monkeypatch.setattr(process_utils.sys, "stderr", None)
        assert process_utils.standard_error_is_visible() is False


class TestLauncherRefusesEarly:
    def test_headless_never_needs_a_window(self) -> None:
        """`--headless` is the window-less path; the probe must not gate it.

        This is the €5-VPS / Docker case from the open-source contract: no
        pywebview anywhere, and the server still has to boot.
        """
        import inspect

        # ``main`` is a thin crash-reporting wrapper; the boot body is ``_main``.
        src = inspect.getsource(launcher._main)
        before_gate = src[: src.index("_missing_window_toolkit")]
        assert "if not args.headless:" in before_gate.split("args = _parse_args")[-1], (
            "the window-toolkit gate must sit behind `if not args.headless:` — "
            "a headless host legitimately has no pywebview"
        )

    def test_gate_runs_before_the_expensive_boot(self) -> None:
        """Fail in a second, not after a minute of discarded work."""
        import inspect

        # ``main`` is a thin crash-reporting wrapper; the boot body is ``_main``.
        src = inspect.getsource(launcher._main)
        gate_at = src.index("_missing_window_toolkit")
        server_at = src.index("_missing_backend_server")
        for later in ("load_config()", "ensure_control_key", "_run_desktop("):
            later_at = src.index(later)
            assert gate_at < later_at, (
                f"the window check must run before {later}; otherwise the user "
                "pays the full boot for an error that was knowable up front"
            )
            assert server_at < later_at, (
                f"the uvicorn/click check must run before {later}; a missing "
                "click is the same mute Start-Menu click as a missing pywebview"
            )

    def test_refusal_reports_a_failing_exit_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        dialogs: list[tuple[str, str]],
    ) -> None:
        monkeypatch.setattr(
            launcher, "_missing_window_toolkit", lambda: "pywebview is missing"
        )
        rc = launcher.main([])
        assert rc == 4, "a start that never happened must not report success"
        assert "pywebview is missing" in capsys.readouterr().err

    def test_missing_backend_dep_refuses_before_the_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        dialogs: list[tuple[str, str]],
    ) -> None:
        monkeypatch.setattr(launcher, "_missing_window_toolkit", lambda: None)
        monkeypatch.setattr(
            launcher, "_missing_backend_server", lambda: "the package 'click' is missing"
        )
        rc = launcher.main([])
        assert rc == 4
        assert "click" in capsys.readouterr().err
