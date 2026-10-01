"""The agentic-IDE parsers stay linear on long, hostile input.

CodeQL flagged these patterns as polynomial ReDoS (py/polynomial-redos): typed
or pasted text reaches them unbounded, and the old forms rescanned long blank
runs from every start position. Each test pins the normal behaviour AND a
pathological input of ~50k characters that must finish well under a second.
"""  # i18n-allow: quoted spoken input in the cases below

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from jarvis.agentic_ide import clarify, intent

_BUDGET_S = 1.0
_N = 50_000


def _fast(fn: Callable[[], object]) -> None:
    start = time.perf_counter()
    fn()
    assert time.perf_counter() - start < _BUDGET_S


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("", True),
        ("Hallo.", True),
        ("Hallo.  ", True),
        ("erste Zeile\n", True),
        ("erste Zeile\n  ", True),
        ("   ", False),
        ("sag", False),
        ("x. y", False),
    ],
)
def test_opens_sentence_normal(prefix: str, expected: bool) -> None:
    assert clarify._opens_sentence(prefix) is expected


def test_opens_sentence_pathological() -> None:
    _fast(lambda: clarify._opens_sentence("\n" * _N + "x"))


@pytest.mark.parametrize(
    ("gap", "expected"),
    [(", ", True), (" und ", True), (" as well as ", True), ("", True), (" oder ", False)],
)
def test_enumeration_gap_normal(gap: str, expected: bool) -> None:
    assert bool(clarify._ENUMERATION_GAP_RE.match(gap)) is expected


def test_enumeration_gap_pathological() -> None:
    _fast(lambda: clarify._ENUMERATION_GAP_RE.match("\t" * _N + "x"))


@pytest.mark.parametrize(
    ("gap", "expected"),
    [(", ", True), (" und ", True), (" & ", True), (" oder ", False), (" sagt ", False)],
)
def test_coordination_normal(gap: str, expected: bool) -> None:
    assert bool(intent._COORDINATION_RE.match(gap)) is expected


def test_coordination_pathological() -> None:
    _fast(lambda: intent._COORDINATION_RE.match("\t" * _N + "x"))


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [("nicht", True), (", aber nicht.", True), ("not !", True), ("no way", False)],
)
def test_trailing_negation_normal(suffix: str, expected: bool) -> None:
    assert bool(intent._TRAILING_NEGATION_RE.fullmatch(suffix)) is expected


def test_trailing_negation_pathological() -> None:
    _fast(lambda: intent._TRAILING_NEGATION_RE.fullmatch("no" + " " * _N + "x"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Und was passiert dann?", "was passiert dann?"),
        ("¿ y qué pasa?", "qué pasa?"),
        ("and, and — why?", "why?"),
        ("Was passiert?", "Was passiert?"),
    ],
)
def test_question_discourse_prefix_normal(text: str, expected: str) -> None:
    assert intent._QUESTION_DISCOURSE_PREFIX_RE.sub("", text) == expected


def test_question_discourse_prefix_pathological() -> None:
    _fast(lambda: intent._QUESTION_DISCOURSE_PREFIX_RE.sub("", " " * _N + "x"))


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("die Tests fixen und der ", "die Tests fixen"),
        ("den Bug fixen, and the", "den Bug fixen"),
        ("den Hund", "den Hund"),
        ("docs lesen. ", "docs lesen"),
    ],
)
def test_group_task_trim_normal(task: str, expected: str) -> None:
    assert intent._GROUP_TASK_TRIM_RE.sub("", task) == expected


@pytest.mark.parametrize("blank", ["\t", " ", " ."], ids=["tab", "space", "dot"])
def test_group_task_trim_pathological(blank: str) -> None:
    _fast(lambda: intent._GROUP_TASK_TRIM_RE.sub("", blank * _N + "x"))


@pytest.mark.parametrize(
    ("text", "span"),
    [
        ("mach zwei Terminals und prompt sie", (24, 30)),
        ("zwei Terminals, gib ihnen die Aufgabe, Tests zu fixen", (16, 37)),
        ("gib mir zwei Terminals\ndie Aufgabe kommt später", None),
        ("gib jedem eine Aufgabe und tell them", (0, 22)),
        ("zwei Terminals auf", None),
    ],
)
def test_fleet_brief_search_normal(text: str, span: tuple[int, int] | None) -> None:
    match = intent._fleet_brief_search(text)
    assert (match.span() if match else None) == span


def test_fleet_brief_search_honours_bounds() -> None:
    text = "tell them, gib ihnen die Aufgabe"
    match = intent._fleet_brief_search(text, 5)
    assert match is not None and match.start() == 11
    assert intent._fleet_brief_search(text, 5, 20) is None


@pytest.mark.parametrize(
    "build",
    [
        lambda: "gib" + " " * _N + "x",
        lambda: "gib " * (_N // 4),
        lambda: "gib\n" * (_N // 4) + "aufgabe",
    ],
    ids=["one-long-gap", "many-leads", "many-lines"],
)
def test_fleet_brief_search_pathological(build: Callable[[], str]) -> None:
    text = build()
    _fast(lambda: intent._fleet_brief_search(text))
