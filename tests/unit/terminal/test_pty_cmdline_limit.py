"""A PTY spawn Windows would reject must be refused BEFORE it is attempted.

This is not a tidiness rule, it is the fix for a whole-app freeze. Windows caps
a command line at 32,767 characters, and pywinpty flattens argv into exactly
that one string. When CreateProcessW rejects it, the pseudoconsole has already
been created — and the orphan left behind hangs forever in its destructor while
holding the GIL, which stops every other thread in the process: the event loop,
the log writer, and the desktop window. The user sees "Python is not
responding" and a log that stops mid-sentence.

Live on 2026-08-14 the app froze fifteen times in an evening on exactly this:
a 33,514-character judge prompt passed as an argv element, once per Consolidator
run. The log brackets the boundary — 32,066 characters answered normally in
37 s; 33,514 raised WinptyError in 22 ms and the app died with it.

Nothing can rescue that state once it exists, so these tests pin the only cure
there is: refuse early, and measure the string Windows will actually receive
rather than the length of the payload.
"""
from __future__ import annotations

import subprocess

import pytest

from jarvis.terminal.backend import (
    _WINDOWS_CMDLINE_LIMIT,
    _reject_overlong_cmdline,
    _windows_cmdline_length,
)


def test_an_ordinary_command_line_is_allowed():
    """The common case must stay completely untouched."""
    _reject_overlong_cmdline(("C:\\tools\\agy.EXE", "--print", "hello", "--effort", "medium"))


def test_the_measurement_counts_what_windows_receives():
    """Length is the assembled command line, not the sum of the arguments.

    pywinpty passes argv[0] separately and flattens the rest with
    ``list2cmdline``, whose quoting grows the payload — an argument with a
    space costs two characters more than the argument itself.
    """
    argv = ("exe", "a b")
    expected = len("exe") + 1 + len(subprocess.list2cmdline(["a b"]))
    assert _windows_cmdline_length(argv) == expected
    assert _windows_cmdline_length(argv) > len("exe") + len("a b")


def test_an_empty_argv_measures_zero_and_is_allowed():
    """Degenerate input must not raise on the way to a real failure."""
    assert _windows_cmdline_length(()) == 0
    _reject_overlong_cmdline(())


def test_the_prompt_that_froze_the_app_is_refused():
    """The exact 2026-08-14 shape: a 33,514-character prompt in argv."""
    argv = ("C:\\Users\\x\\AppData\\Local\\agy\\bin\\agy.EXE", "--print", "x" * 33_514)
    with pytest.raises(RuntimeError) as excinfo:
        _reject_overlong_cmdline(argv)
    message = str(excinfo.value)
    assert "too long" in message
    # The message has to carry both numbers, or the next person cannot tell
    # how far over the line they are.
    assert "33" in message and str(_WINDOWS_CMDLINE_LIMIT) in message


def test_the_refusal_says_what_to_do_instead():
    """A guard that only says 'no' sends the reader back to the same mistake."""
    with pytest.raises(RuntimeError) as excinfo:
        _reject_overlong_cmdline(("exe", "y" * 40_000))
    message = str(excinfo.value).lower()
    assert "stdin" in message or "file" in message


def test_a_command_line_just_under_the_cap_is_still_allowed():
    """Headroom must not swallow payloads that genuinely fit.

    The 32,066-character prompt in the same log answered normally, so the
    guard has to leave room for that shape rather than refusing anything
    merely large.
    """
    argv = ("exe", "z" * 30_000)
    assert _windows_cmdline_length(argv) < _WINDOWS_CMDLINE_LIMIT
    _reject_overlong_cmdline(argv)


def test_the_guard_runs_before_any_pseudoconsole_is_created():
    """The whole point: refuse without pywinpty ever being asked to spawn.

    If the check ever moved below the spawn call, the orphaned-ConPTY freeze
    would be back and this suite would still pass on the message alone — so
    the ordering is asserted directly, on the real backend, by proving the
    refusal happens with a spawn that would explode if it were reached.
    """
    pytest.importorskip("winpty", reason="Windows-only PTY backend")
    from jarvis.terminal.backend import WinptyBackend

    with pytest.raises(RuntimeError) as excinfo:
        WinptyBackend().spawn(
            ("cmd.exe", "/c", "rem " + "q" * 40_000),
            cwd=None,
            cols=80,
            rows=24,
        )
    assert "too long" in str(excinfo.value)
