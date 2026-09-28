"""Brain-side matchers stay linear on long, hostile input.

CodeQL flagged these patterns as polynomial ReDoS (py/polynomial-redos): user
utterances and LLM output reach them unbounded. Each test pins the normal
behaviour AND a pathological input of ~50k characters that must finish well
under a second.
"""  # i18n-allow: quoted spoken input in the cases below

from __future__ import annotations

import re
import time
from collections.abc import Callable

import pytest

from jarvis.brain import ack_generator, local_outcome_gate, mission_command_gate, output_filter

_BUDGET_S = 1.0
_N = 50_000


def _fast(fn: Callable[[], object]) -> None:
    start = time.perf_counter()
    fn()
    assert time.perf_counter() - start < _BUDGET_S


# --- ack_generator ----------------------------------------------------------


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("halt", True),
        ("Stop please!", True),
        ("leiser bitte .", True),
        ("please stop talking now", True),
        ("stop the music", False),
        ("halt die Klappe", True),
        ("lauter Applaus war zu hoeren", False),
    ],
)
def test_voice_control_normal(utterance: str, expected: bool) -> None:
    assert ack_generator.is_voice_control_utterance(utterance) is expected


def test_voice_control_pathological() -> None:
    _fast(lambda: ack_generator.is_voice_control_utterance("halt" + " " * _N + "x"))


# --- mission_command_gate ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("brich die mission ab", "cancel"),
        ("brich das jetzt ab", "cancel"),
        ("status?", "status"),
        ("jarvis, wie weit?", "status"),
        ("wie weit ist berlin", None),
        ("what's the status", "status"),
    ],
)
def test_mission_command_normal(text: str, intent: str | None) -> None:
    match = mission_command_gate.match_mission_command(text)
    assert (match.intent if match else None) == intent


def test_status_en_keeps_final_line_break_quirk() -> None:
    # The EN arm ends in \b, which only holds at the '$' in front of a final
    # line break — the rewrite must keep that exact behaviour.
    pattern = mission_command_gate._STATUS_PATTERN_EN
    assert pattern.search("status\n") is not None
    assert pattern.search("status") is not None
    assert pattern.search("status now") is None


@pytest.mark.parametrize(
    "build",
    [
        lambda: "brich das" + " " * _N + "x",
        lambda: "status" + " " * _N + "x",
        lambda: "wie weit" + " " * _N + "x",
    ],
    ids=["cancel", "status", "how-far"],
)
def test_mission_command_pathological(build: Callable[[], str]) -> None:
    text = build()
    for pattern in (
        mission_command_gate._CANCEL_PATTERN_DE,
        mission_command_gate._STATUS_PATTERN_DE,
        mission_command_gate._STATUS_PATTERN_EN,
    ):
        _fast(lambda pattern=pattern: pattern.search(text))


# --- output_filter ----------------------------------------------------------

# The original patterns, kept here as the reference the linear rewrites must
# reproduce on short inputs.
_FUNCTION_CALLS_REFERENCE = re.compile(
    r"<function_calls>.*?</function_calls>", re.DOTALL | re.IGNORECASE
)
_UNICODE_DASH_REFERENCE = re.compile(r"\s*[—–]\s*")
_DOUBLE_HYPHEN_REFERENCE = re.compile(r"\s+-{2,}\s+")
_QUOTED_LITERAL_REFERENCE = re.compile(
    r"""(?<!\w)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`[^`]*`)"""
)


@pytest.mark.parametrize(
    "text",
    [
        "a <function_calls>x</function_calls> b",
        "<function_calls>1</FUNCTION_CALLS> mid <function_calls>2</function_calls> end",
        "<function_calls> unclosed",
        "no tags at all",
        "</function_calls> then <function_calls> open",
    ],
)
def test_strip_function_call_blocks_matches_pattern(text: str) -> None:
    expected = _FUNCTION_CALLS_REFERENCE.sub("", text)
    assert output_filter._strip_function_call_blocks(text) == expected


@pytest.mark.parametrize(
    "build",
    [
        lambda: "x</function_calls>" + "<function_calls>" * (_N // 16),
        lambda: "<function_calls>" + "<" * _N,
        lambda: ("<function_calls>" + "<" * 100 + "</function_calls>") * 400,
    ],
    ids=["unclosed-openers", "opener-then-brackets", "many-blocks"],
)
def test_strip_function_call_blocks_pathological(build: Callable[[], str]) -> None:
    text = build()
    _fast(lambda: output_filter._strip_function_call_blocks(text))


@pytest.mark.parametrize(
    "text",
    [
        "Fine — thanks",
        "a — — b",
        "  –x—  ",
        "a\n — \tb",
        "none",
        "That is -- as I said -- fine",
        "a -- -- b",
        "a ---x -- y",
        " -- ",
        "x --\n--  y",
        "-- a --",
    ],
)
def test_dash_helpers_match_patterns(text: str) -> None:
    unicode_expected = _UNICODE_DASH_REFERENCE.sub(", ", text)
    hyphen_expected = _DOUBLE_HYPHEN_REFERENCE.sub(", ", text)
    assert output_filter._collapse_unicode_dashes(text) == unicode_expected
    assert output_filter._collapse_double_hyphens(text) == hyphen_expected


@pytest.mark.parametrize(
    "build",
    [lambda: "a" + " " * _N + "x", lambda: "a" + " -- " * (_N // 4), lambda: "—" * _N],
    ids=["blank-run", "many-asides", "many-dashes"],
)
def test_dash_helpers_pathological(build: Callable[[], str]) -> None:
    text = build()
    _fast(lambda: output_filter._collapse_unicode_dashes(text))
    _fast(lambda: output_filter._collapse_double_hyphens(text))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fine — thanks", "Fine, thanks"),
        ("A – B — C", "A, B, C"),
        ("That is -- as I said -- fine", "That is, as I said, fine"),
        ("A T-Shirt and an E-Mail", "A T-Shirt and an E-Mail"),
    ],
)
def test_dash_scrub_normal(text: str, expected: str) -> None:
    assert output_filter.scrub_for_voice(text, language="en").cleaned == expected


@pytest.mark.parametrize(
    "build",
    [lambda: "a" + " " * _N + "x", lambda: "a -" + " " * _N + "x"],
    ids=["blank-run", "blank-run-after-hyphen"],
)
def test_dash_scrub_pathological(build: Callable[[], str]) -> None:
    text = build()
    _fast(lambda: output_filter.scrub_for_voice(text, language="en"))


# --- local_outcome_gate -----------------------------------------------------


def _mark(match: re.Match[str]) -> str:
    return f"<{match.start()}:{match.group()}>"


def _mark_literal(start: int, literal: str) -> str:
    return f"<{start}:{literal}>"


@pytest.mark.parametrize(
    "text",
    [
        'create "my file.txt" now',
        "don't touch 'a b' or `c d`",
        'escaped \\"x\\" and "y"',
        '"unclosed \\" and "closed"',
        "it's 'odd",
        "a\\\n'x' `y",
    ],
)
def test_sub_quoted_literals_matches_pattern(text: str) -> None:
    expected = _QUOTED_LITERAL_REFERENCE.sub(_mark, text)
    assert local_outcome_gate._sub_quoted_literals(_mark_literal, text) == expected


@pytest.mark.parametrize(
    "build",
    [lambda: '"' + '\\"' * (_N // 2), lambda: "'" + "\\'" * (_N // 2), lambda: "`" * _N + "a"],
    ids=["double", "single", "backtick"],
)
def test_sub_quoted_literals_pathological(build: Callable[[], str]) -> None:
    text = build()
    _fast(lambda: local_outcome_gate._sub_quoted_literals(_mark_literal, text))


def test_local_outcome_mandate_escaped_quote_run() -> None:
    text = "create a folder " + '"' + '\\"' * (_N // 2)
    _fast(lambda: local_outcome_gate.resolve_local_outcome_mandate(text))
