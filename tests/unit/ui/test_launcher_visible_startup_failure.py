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

    @pytest.mark.skipif(sys.platform == "win32", reason="tests the non-Windows no-op")
    def test_dialog_is_a_noop_off_windows(self) -> None:
        launcher._show_error_dialog("title", "body")


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

        src = inspect.getsource(launcher.main)
        before_gate = src[: src.index("_missing_window_toolkit")]
        assert "if not args.headless:" in before_gate.split("args = _parse_args")[-1], (
            "the window-toolkit gate must sit behind `if not args.headless:` — "
            "a headless host legitimately has no pywebview"
        )

    def test_gate_runs_before_the_expensive_boot(self) -> None:
        """Fail in a second, not after a minute of discarded work."""
        import inspect

        src = inspect.getsource(launcher.main)
        gate_at = src.index("_missing_window_toolkit")
        for later in ("load_config()", "ensure_control_key", "_run_desktop("):
            assert gate_at < src.index(later), (
                f"the window check must run before {later}; otherwise the user "
                "pays the full boot for an error that was knowable up front"
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
