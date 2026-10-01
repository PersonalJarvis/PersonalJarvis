"""Bench corpus integrity and tool-call grading."""

from __future__ import annotations

import pytest

from jarvis.voice_engine.bench import corpus
from jarvis.voice_engine.bench.stats import error_rates, percentile, summary


@pytest.mark.parametrize("language", corpus.LANGUAGES)
def test_each_language_has_at_least_200_tool_cases(language: str) -> None:
    cases = corpus.tool_cases(language)
    assert len(cases) >= 200
    assert sum(case.tool is None for case in cases) >= 25
    declared = {tool["function"]["name"] for tool in corpus.tools()}
    assert {case.tool for case in cases if case.tool} <= declared


@pytest.mark.parametrize("language", corpus.LANGUAGES)
def test_corpus_sections_are_complete(language: str) -> None:
    data = corpus.load(language)
    assert data["language"] == language
    assert len(data["roundtrip"]) >= 15
    assert len(data["dialog"]) >= 10
    for item in data["hesitations"]:
        assert len(item["pauses_ms"]) == len(item["segments"]) - 1


def _case(**overrides: object) -> corpus.ToolCase:
    base = {"language": "en", "utterance": "Set a timer for ten minutes.", "tool": "set_timer",
            "accept": ("set_reminder",), "checks": {"minutes": {"equals": 10}}}
    base.update(overrides)
    return corpus.ToolCase(**base)  # type: ignore[arg-type]


def test_grade_verdicts() -> None:
    case = _case()
    assert corpus.grade(case, [{"name": "set_timer", "arguments": {"minutes": 10}}])["verdict"] == (
        "correct"
    )
    assert corpus.grade(case, [{"name": "set_timer", "arguments": {"minutes": "10"}}])[
        "verdict"
    ] == "correct"
    assert corpus.grade(case, [{"name": "set_timer", "arguments": {"minutes": 5}}])["verdict"] == (
        "wrong_args"
    )
    assert corpus.grade(case, [{"name": "set_reminder", "arguments": {}}])["verdict"] == "correct"
    assert corpus.grade(case, [{"name": "open_app", "arguments": {}}])["verdict"] == "wrong_tool"
    assert corpus.grade(case, [])["verdict"] == "missed"
    direct = _case(tool=None, accept=(), checks={})
    assert corpus.grade(direct, [])["verdict"] == "correct"
    assert corpus.grade(direct, [{"name": "search_web", "arguments": {}}])["verdict"] == "unneeded"


def test_contains_check_accepts_alternatives() -> None:
    case = _case(tool="open_app", accept=(), checks={"name": {"contains": ["rechner", "calc"]}})
    assert corpus.grade(case, [{"name": "open_app", "arguments": {"name": "Calculator"}}])[
        "verdict"
    ] == "correct"


def test_error_rates_ignore_case_and_punctuation() -> None:
    assert error_rates("Wie wird das Wetter?", "wie wird das wetter") == (0.0, 0.0)  # i18n-allow
    cer, wer = error_rates("one two three", "one too three")
    assert 0 < cer < 0.2
    assert wer == pytest.approx(1 / 3)


def test_percentiles() -> None:
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert summary([1, 2, 3])["n"] == 3
