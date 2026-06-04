"""Tests fuer Pre/Post-Check-Runner und Built-In-Checks (Phase 8.1).

Plan-Referenz: §6.1 Akzeptanzkriterium 2 — alle 5 Built-Ins,
Pre-Fail-Short-Circuit.
"""
from __future__ import annotations

import json

import pytest

from jarvis.core.review.checks import (
    Check,
    CheckResult,
    PostCheckRunner,
    PreCheckRunner,
    make_output_budget_check,
    no_stub_code,
    output_not_empty,
    task_not_empty,
    valid_json,
)

# ----------------------------------------------------------------------
# task_not_empty (pre)
# ----------------------------------------------------------------------


def test_task_not_empty_positive() -> None:
    """Task >10 Chars (gestrippt) ist ok."""
    result = task_not_empty("schreib mir ein Skript das X tut")
    assert result.ok is True
    assert result.name == "task_not_empty"


def test_task_not_empty_negative_short() -> None:
    """Task <=10 Chars (gestrippt) wird abgelehnt."""
    result = task_not_empty("hi")
    assert result.ok is False
    assert "task too short" in result.message


def test_task_not_empty_negative_only_whitespace() -> None:
    """Whitespace-only zaehlt als 0 Chars."""
    result = task_not_empty("            \n\t   ")
    assert result.ok is False


def test_task_not_empty_boundary_exactly_11() -> None:
    """Schwellwert ist `> 10`, also 11 ist ok, 10 nicht."""
    assert task_not_empty("a" * 11).ok is True
    assert task_not_empty("a" * 10).ok is False


# ----------------------------------------------------------------------
# output_not_empty (post)
# ----------------------------------------------------------------------


def test_output_not_empty_positive() -> None:
    assert output_not_empty("some content").ok is True


def test_output_not_empty_negative_empty_string() -> None:
    assert output_not_empty("").ok is False


def test_output_not_empty_negative_only_whitespace() -> None:
    assert output_not_empty("   \n\t   ").ok is False


# ----------------------------------------------------------------------
# output_within_budget factory (post)
# ----------------------------------------------------------------------


def test_output_within_budget_positive() -> None:
    check = make_output_budget_check(100)
    assert check("a" * 99).ok is True


def test_output_within_budget_negative_at_limit() -> None:
    """`<` (strikt) — bei genau `max_output_chars` muss abgelehnt werden."""
    check = make_output_budget_check(100)
    assert check("a" * 100).ok is False


def test_output_within_budget_negative_over() -> None:
    check = make_output_budget_check(100)
    result = check("a" * 200)
    assert result.ok is False
    assert "200" in result.message
    assert "100" in result.message


def test_output_budget_factory_rejects_zero_or_negative() -> None:
    with pytest.raises(ValueError):
        make_output_budget_check(0)
    with pytest.raises(ValueError):
        make_output_budget_check(-1)


# ----------------------------------------------------------------------
# no_stub_code (post)
# ----------------------------------------------------------------------


def test_no_stub_code_positive_clean_code() -> None:
    code = "def foo():\n    return 42\n"
    assert no_stub_code(code).ok is True


def test_no_stub_code_negative_todo_alone() -> None:
    code = "def foo():\n    TODO\n    return 42\n"
    result = no_stub_code(code)
    assert result.ok is False
    assert "TODO" in result.message


def test_no_stub_code_negative_pass_alone() -> None:
    code = "def foo():\n    pass\n"
    result = no_stub_code(code)
    assert result.ok is False


@pytest.mark.parametrize("marker", ["FIXME", "XXX", "TODO", "pass"])
def test_no_stub_code_negative_each_marker(marker: str) -> None:
    code = f"def foo():\n    {marker}\n"
    assert no_stub_code(code).ok is False


def test_no_stub_code_inline_comment_allowed() -> None:
    """Inline-Kommentare wie `x = 1  # TODO` sind keine Stub-Lines."""
    code = "x = 1  # TODO: add docstring later\n"
    assert no_stub_code(code).ok is True


def test_no_stub_code_word_in_string_allowed() -> None:
    """`pass` als Substring in legitimem Code ist erlaubt."""
    code = 'msg = "Please pass the test"\n'
    assert no_stub_code(code).ok is True


# ----------------------------------------------------------------------
# valid_json (post, optional)
# ----------------------------------------------------------------------


def test_valid_json_positive_object() -> None:
    assert valid_json('{"status": "pass", "score": 0.9}').ok is True


def test_valid_json_positive_array() -> None:
    assert valid_json("[1, 2, 3]").ok is True


def test_valid_json_negative_truncated() -> None:
    result = valid_json('{"status": "pass"')
    assert result.ok is False
    assert "JSON" in result.message


def test_valid_json_negative_prose() -> None:
    assert valid_json("This is not JSON.").ok is False


def test_valid_json_negative_empty() -> None:
    assert valid_json("").ok is False


# ----------------------------------------------------------------------
# PreCheckRunner
# ----------------------------------------------------------------------


def test_pre_runner_all_pass() -> None:
    runner = PreCheckRunner([task_not_empty])
    result = runner.run("ein hinreichend langer Task")
    assert result.ok is True
    assert result.failed is None
    assert len(result.executed) == 1


def test_pre_runner_short_circuit() -> None:
    """Plan-§6.1 AC: zweiter Check wird nicht aufgerufen wenn erster failt."""
    calls: list[str] = []

    def first(payload: str) -> CheckResult:
        calls.append("first")
        return CheckResult(ok=False, name="first", message="forced")

    def second(payload: str) -> CheckResult:
        calls.append("second")
        return CheckResult(ok=True, name="second")

    runner = PreCheckRunner([first, second])
    result = runner.run("anything")
    assert result.ok is False
    assert result.failed is not None
    assert result.failed.name == "first"
    assert calls == ["first"]  # second wurde NICHT aufgerufen
    assert len(result.executed) == 1


def test_pre_runner_empty_list_passes() -> None:
    """Leere Check-Liste = trivial bestanden."""
    runner = PreCheckRunner([])
    result = runner.run("anything")
    assert result.ok is True
    assert result.executed == ()


# ----------------------------------------------------------------------
# PostCheckRunner
# ----------------------------------------------------------------------


def test_post_runner_short_circuit_at_third() -> None:
    """Short-Circuit funktioniert auch im PostRunner, an beliebiger Stelle."""
    calls: list[str] = []

    def make(name: str, ok: bool) -> Check:
        def _check(payload: str) -> CheckResult:
            calls.append(name)
            return CheckResult(ok=ok, name=name)

        return _check

    runner = PostCheckRunner(
        [make("a", True), make("b", True), make("c", False), make("d", True)]
    )
    result = runner.run("output")
    assert result.ok is False
    assert result.failed is not None and result.failed.name == "c"
    assert calls == ["a", "b", "c"]


def test_post_runner_with_real_builtins() -> None:
    """End-to-End mit drei echten Built-Ins."""
    runner = PostCheckRunner(
        [output_not_empty, make_output_budget_check(1000), no_stub_code]
    )
    good = "def add(a, b):\n    return a + b\n"
    assert runner.run(good).ok is True

    bad_stub = "def add(a, b):\n    pass\n"
    bad_result = runner.run(bad_stub)
    assert bad_result.ok is False
    assert bad_result.failed is not None
    assert bad_result.failed.name == "no_stub_code"


def test_post_runner_with_valid_json_optional() -> None:
    """valid_json ist nur aktiv, wenn der Caller ihn in den Runner einfuegt."""
    runner_with_json = PostCheckRunner([output_not_empty, valid_json])
    assert runner_with_json.run('{"x": 1}').ok is True
    assert runner_with_json.run("not json").ok is False

    runner_without_json = PostCheckRunner([output_not_empty])
    assert runner_without_json.run("not json").ok is True  # optional uebersprungen


# ----------------------------------------------------------------------
# Smoke: Built-In-Checks zusammen mit json-Loads-Konsistenz
# ----------------------------------------------------------------------


def test_valid_json_negative_message_format() -> None:
    """Sicherstellen, dass die Fehler-Message eine Zeilennummer enthaelt."""
    result = valid_json("{ invalid")
    assert result.ok is False
    # JSONDecodeError liefert mind. die Position; wir testen dass das nicht
    # explodiert wenn man sie formatiert.
    assert isinstance(result.message, str)
    assert "line" in result.message


def test_valid_json_with_real_verdict_json_passes() -> None:
    """Realistischer Reviewer-Output ist gueltiges JSON."""
    payload = json.dumps(
        {"status": "pass", "summary": "ok", "issues": [], "score": 1.0}
    )
    assert valid_json(payload).ok is True
