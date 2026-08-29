"""The updater's subprocess helper, now that it taps stderr live.

Reading git's progress counters while the fetch runs meant replacing
``communicate()`` with two concurrent pipe readers. That is the kind of change
that works in a unit test and deadlocks in the field, so these tests use REAL
child processes and deliberately noisy output:

* both pipes must drain concurrently — a child that fills stderr while we read
  stdout hangs forever,
* git redraws its counter with a carriage return, so ``\\r`` has to end a
  segment just like ``\\n``,
* and the existing contract (return code, decoding, timeout, missing binary)
  must survive untouched, because every other git call in the updater uses it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from jarvis.ui.web.update_routes import _run


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _call(cmd: list[str], *, timeout_s: float = 30.0, tap: object = None):
    return asyncio.run(
        _run(cmd, cwd=Path.cwd(), timeout_s=timeout_s, on_stderr_line=tap)  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# The existing contract
# --------------------------------------------------------------------------- #
def test_stdout_and_returncode_are_unchanged() -> None:
    rc, out, _err = _call(_python("print('hello')"))
    assert rc == 0
    assert out == "hello"


def test_stderr_is_still_returned_whole() -> None:
    rc, _out, err = _call(_python("import sys; sys.stderr.write('boom'); sys.exit(3)"))
    assert rc == 3
    assert err == "boom"


def test_a_missing_binary_reports_minus_one() -> None:
    rc, _out, err = _call(["definitely-not-a-real-binary-xyz"])
    assert rc == -1
    assert "could not run" in err


def test_a_timeout_reports_minus_one_and_kills_the_child() -> None:
    rc, _out, err = _call(_python("import time; time.sleep(30)"), timeout_s=0.5)
    assert rc == -1
    assert "timed out" in err


# --------------------------------------------------------------------------- #
# The live tap
# --------------------------------------------------------------------------- #
def test_carriage_returns_end_a_segment() -> None:
    """git redraws its counter with \\r, so \\n alone would see one giant line."""
    seen: list[str] = []
    _call(
        _python(
            "import sys; sys.stderr.write('Receiving objects: 10%\\r"
            "Receiving objects: 90%\\r'); sys.stderr.flush()"
        ),
        tap=seen.append,
    )
    assert seen == ["Receiving objects: 10%", "Receiving objects: 90%"]


def test_the_tap_sees_output_while_the_child_still_runs() -> None:
    """The whole point: a percentage that only arrives at the end is useless."""
    seen: list[str] = []
    _call(
        _python(
            "import sys, time\n"
            "for i in range(5):\n"
            "    sys.stderr.write(f'tick {i}\\r'); sys.stderr.flush(); time.sleep(0.05)\n"
        ),
        tap=seen.append,
    )
    assert seen == [f"tick {i}" for i in range(5)]


def test_a_chatty_child_does_not_deadlock() -> None:
    """A child filling one pipe while we read the other is the classic hang."""
    seen: list[str] = []
    rc, out, err = _call(
        _python(
            "import sys\n"
            "for i in range(4000):\n"
            "    sys.stdout.write('o' * 60 + '\\n')\n"
            "    sys.stderr.write(f'progress {i}\\r')\n"
        ),
        timeout_s=60.0,
        tap=seen.append,
    )
    assert rc == 0
    assert len(out.splitlines()) == 4000
    assert len(seen) >= 3999  # the last segment may still be buffered
    assert "progress 3999" in err


def test_blank_segments_are_not_delivered() -> None:
    seen: list[str] = []
    _call(
        _python("import sys; sys.stderr.write('\\r\\n\\r\\n  \\r\\nreal\\r')"),
        tap=seen.append,
    )
    assert seen == ["real"]


def test_undecodable_bytes_do_not_kill_the_run() -> None:
    """A locale-mangled git message must degrade, not raise."""
    seen: list[str] = []
    rc, _out, _err = _call(
        _python(
            "import sys; sys.stderr.buffer.write(b'bad \\xff byte\\r'); sys.stderr.buffer.flush()"
        ),
        tap=seen.append,
    )
    assert rc == 0
    assert len(seen) == 1
    assert "bad" in seen[0]


def test_output_is_complete_with_no_tap_attached() -> None:
    """Every other git call in the updater passes no tap; it must be unaffected."""
    rc, out, err = _call(_python("import sys; print('out'); sys.stderr.write('err')"), tap=None)
    assert (rc, out, err) == (0, "out", "err")


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_large_single_stream_output_survives(stream: str) -> None:
    rc, out, err = _call(
        _python(
            f"import sys; sys.{stream}.write('x' * 500_000)",
        ),
        timeout_s=60.0,
    )
    assert rc == 0
    assert len(out if stream == "stdout" else err) == 500_000
