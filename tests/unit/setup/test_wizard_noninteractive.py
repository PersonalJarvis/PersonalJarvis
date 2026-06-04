"""Tests for the wizard's non-interactive / headless boot path.

Covers:
  (a) _is_noninteractive() returns True when JARVIS_NONINTERACTIVE=1 is set.
  (b) _is_noninteractive() returns True when sys.stdin.isatty() is False.
  (c) _is_noninteractive() returns False when stdin is a real TTY.
  (d) run() in non-interactive mode skips all prompts, calls
      cfg.mark_setup_complete(), and returns 0 — without ever reaching
      builtins.input() (any accidental call to input() raises, failing the test).
"""

from __future__ import annotations

import builtins
from unittest.mock import MagicMock, patch

import pytest

from jarvis.setup.wizard import _is_noninteractive, run


# ---------------------------------------------------------------------------
# _is_noninteractive() unit tests
# ---------------------------------------------------------------------------

class TestIsNoninteractive:
    def test_env_var_set_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JARVIS_NONINTERACTIVE=1 forces non-interactive mode regardless of TTY."""
        monkeypatch.setenv("JARVIS_NONINTERACTIVE", "1")
        # Ensure isatty() would otherwise say True (interactive) so we prove
        # the env var takes priority.
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            assert _is_noninteractive() is True

    def test_no_tty_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No TTY on stdin triggers non-interactive mode (VPS / Docker / CI)."""
        monkeypatch.delenv("JARVIS_NONINTERACTIVE", raising=False)
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            assert _is_noninteractive() is True

    def test_interactive_tty_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A real TTY with no env override means interactive mode."""
        monkeypatch.delenv("JARVIS_NONINTERACTIVE", raising=False)
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            assert _is_noninteractive() is False

    def test_env_var_zero_not_noninteractive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JARVIS_NONINTERACTIVE=0 does NOT force non-interactive mode."""
        monkeypatch.setenv("JARVIS_NONINTERACTIVE", "0")
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            assert _is_noninteractive() is False

    def test_missing_isatty_defaults_to_noninteractive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stdin object without isatty() is treated as non-interactive (safe fallback)."""
        monkeypatch.delenv("JARVIS_NONINTERACTIVE", raising=False)
        with patch("sys.stdin", new=object()):
            # object() has no isatty attribute → AttributeError → returns True
            assert _is_noninteractive() is True


# ---------------------------------------------------------------------------
# run() non-interactive path integration test
# ---------------------------------------------------------------------------

class TestRunNoninteractive:
    def test_run_noninteractive_skips_prompts_and_marks_complete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run() in non-interactive mode must:
        - not call input() at all (any call raises AssertionError)
        - call cfg.mark_setup_complete() exactly once
        - return 0
        """
        # Force non-interactive mode via env var.
        monkeypatch.setenv("JARVIS_NONINTERACTIVE", "1")

        # Any accidental call to input() fails the test immediately.
        def _input_must_not_be_called(*args: object, **kwargs: object) -> str:
            raise AssertionError(
                "input() was called in non-interactive mode — "
                "the wizard leaked an interactive prompt onto the headless path."
            )

        mark_complete = MagicMock()

        with (
            patch.object(builtins, "input", side_effect=_input_must_not_be_called),
            patch("jarvis.setup.wizard.cfg.mark_setup_complete", mark_complete),
        ):
            rc = run()

        assert rc == 0, f"run() returned {rc!r}, expected 0"
        mark_complete.assert_called_once()
