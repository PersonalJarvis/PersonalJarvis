"""The silent-exception ratchet must hold: no file GAINS an unexplained handler.

An except block that neither logs, nor re-raises, nor says why it stays quiet
makes a failure invisible — the feature just does nothing and nobody can tell.
These tests protect the detection logic and the ratchet itself.
"""
from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_GATE = _REPO / "scripts" / "ci" / "check_silent_exception_handlers.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_silent_exception_handlers", _GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _count(tmp_path: Path, source: str) -> int:
    path = tmp_path / "sample.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return len(_load_gate().silent_handlers(path))


def test_no_file_gained_a_silent_handler():
    gate = _load_gate()
    bad = gate.regressions(gate.scan(), gate.load_baseline())
    assert bad == {}, (
        "files gained an exception handler that says nothing: "
        f"{ {k: v for k, v in list(bad.items())[:10]} }"
    )


def test_a_bare_swallow_is_counted(tmp_path):
    assert _count(tmp_path, """
        def f():
            try:
                g()
            except Exception:
                pass
    """) == 1


def test_logging_the_error_is_not_counted(tmp_path):
    assert _count(tmp_path, """
        def f():
            try:
                g()
            except Exception:
                log.warning("dictation: segment dropped")
    """) == 0


def test_re_raising_is_not_counted(tmp_path):
    assert _count(tmp_path, """
        def f():
            try:
                g()
            except Exception:
                raise
    """) == 0


def test_a_written_reason_is_not_counted(tmp_path):
    """Explaining the silence is a valid answer — that is the point."""
    assert _count(tmp_path, """
        def f():
            try:
                g()
            except Exception:  # a capability probe must never hard-fail
                return None
    """) == 0


def test_a_bare_lint_escape_is_still_counted(tmp_path):
    """`# noqa: BLE001` alone is an escape, not a decision."""
    assert _count(tmp_path, """
        def f():
            try:
                g()
            except Exception:  # noqa: BLE001
                pass
    """) == 1


def test_reason_on_its_own_line_counts(tmp_path):
    """The most natural place to write a longer reason is a line of its own."""
    assert _count(tmp_path, """
        def f():
            try:
                g()
            except OSError:
                # The marker file is absent on every source checkout, which is
                # the common path rather than a failure.
                return None
    """) == 0


def test_reason_on_the_first_body_line_counts(tmp_path):
    assert _count(tmp_path, """
        def f():
            try:
                g()
            except Exception:
                pass  # teardown is best-effort; the socket is already gone
    """) == 0


def test_baseline_covers_the_real_tree():
    """Guard against a baseline that silently drifted to nothing."""
    gate = _load_gate()
    baseline = gate.load_baseline()
    assert baseline, "baseline is empty — the ratchet would allow anything"
    assert sum(baseline.values()) > 500, (
        f"baseline total {sum(baseline.values())} looks impossibly low; "
        "the scan probably broke"
    )
