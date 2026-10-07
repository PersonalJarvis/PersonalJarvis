"""Spoken-name resolution: misheard agent names land, unrelated words do not."""

from __future__ import annotations

import logging

import pytest

from jarvis.core.spoken_names import (
    ACT_SCORE,
    ASK_SCORE,
    NameCandidate,
    jaro_winkler,
    koelner_phonetik,
    name_tokens,
    normalize,
    resolve_name,
)

#: The live roster of 2026-10-02 plus a coding agent called "Jarvis Code".
ROSTER = (
    NameCandidate(
        "jarvis-code", "Jarvis Code", ("Jarvis Code", "jarvis code"),
        ("coding agent", "code agent", "coder"), frozenset({"coding"}),
    ),
    NameCandidate(
        "jarvis-scout", "Jarvis-Scout", ("Jarvis-Scout", "jarvis scout"),
        ("GitHub-Trends für Personal Jarvis",),  # i18n-allow: agent title from the live roster
    ),
    NameCandidate("x-markring", "X markring", ("X markring", "x markring")),
    NameCandidate("gmail-tagesreport", "Gmail-Tagesreport", ("Gmail-Tagesreport",)),
    NameCandidate(
        "github-issue-sortieren", "GitHub Issue sortieren.", ("GitHub Issue sortieren.",)
    ),
)  # fmt: skip


@pytest.mark.parametrize(
    ("heard", "method"),
    [
        ("Jarvis Code", "exact"),
        ("jarvis-code", "exact"),
        ("JARVIS CODE!", "exact"),
        ("Jarviscode", "fuzzy"),
        ("Jarvis Kot", "phonetic"),
        ("Jarvis-Kot", "phonetic"),
        ("Jarwis Code", "phonetic"),
        ("Davis Code", "fuzzy"),
        ("Jarvis Coder", "fuzzy"),
        ("unseren Jarvis Code", "exact"),  # i18n-allow: speech-recognition input
        ("der Code Agent", "alias"),  # i18n-allow: speech-recognition input
        ("coder", "alias"),
    ],
)
def test_variants_of_jarvis_code_act(heard, method):
    result = resolve_name(heard, ROSTER)
    assert result.decision == "act", result.as_dict()
    assert result.key == "jarvis-code"
    assert result.best is not None and result.best.method == method


def test_charles_code_is_asked_about_not_guessed():
    result = resolve_name("Charles Code", ROSTER)
    assert result.decision == "ask"
    assert [m.key for m in result.candidates] == ["jarvis-code"]


def test_coding_context_tips_a_close_coding_name():
    result = resolve_name("Charles Code", ROSTER, context="bitte fix den Bug im Repo")  # i18n-allow
    assert result.decision == "act" and result.key == "jarvis-code"
    assert result.context_used


def test_context_never_creates_a_match_on_its_own():
    result = resolve_name("Telegram", ROSTER, context="programmiere das bitte")  # i18n-allow
    assert result.decision == "none"


@pytest.mark.parametrize(
    ("heard", "agent_id"),
    [
        ("Java Scout", "jarvis-scout"),  # the live 2026-10-02 transcript
        ("Jarvis Scout", "jarvis-scout"),
        ("Scout", "jarvis-scout"),
        ("X Marketing Agent", "x-markring"),  # live 2026-10-02 08:58
        ("GitHub Issue Sortierer", "github-issue-sortieren"),  # live 2026-10-02 09:45
    ],
)
def test_other_agents_resolve_to_themselves(heard, agent_id):
    result = resolve_name(heard, ROSTER)
    assert result.decision == "act" and result.key == agent_id, result.as_dict()


@pytest.mark.parametrize(
    "heard",
    [
        "Jarvis Scout",
        "Java Scout",
        "Gmail Agent",
        "Telegram",
        "Morgenbriefing",  # i18n-allow: speech-recognition input
        "Charles",
        "Jarvis Guide",  # same Cologne code as "Code" (42), different onset
        "Jarvis Gut",  # i18n-allow: speech-recognition input
        "Claude Code",  # a coding CLI, not this agent
        "Code Review",
        "Codex",
        "Jarvis Codex",
        "Jarvis",
    ],
)
def test_negatives_never_act_on_jarvis_code(heard):
    result = resolve_name(heard, ROSTER)
    assert not (result.decision == "act" and result.key == "jarvis-code"), result.as_dict()


@pytest.mark.parametrize("heard", ["Telegram", "Morgenbriefing", "Charles", "Jarvis Gut"])
def test_unrelated_words_match_nothing(heard):
    assert resolve_name(heard, ROSTER).decision == "none"


def test_a_long_reference_still_finds_the_name_inside_it():
    """Live 2026-10-02: the model passed the whole garbled phrase as the name."""
    result = resolve_name("Java Scout and Quick Meshes", ROSTER)
    assert result.decision == "ask"
    assert result.candidates[0].key == "jarvis-scout"


def test_two_close_candidates_are_a_question():
    twins = (
        NameCandidate("mail-a", "Mail North", ("Mail North",)),
        NameCandidate("mail-b", "Mail South", ("Mail South",)),
    )
    result = resolve_name("Mail", twins)
    assert result.decision == "ask"
    assert {m.key for m in result.candidates} == {"mail-a", "mail-b"}


def test_empty_inputs_decide_none():
    assert resolve_name("", ROSTER).decision == "none"
    assert resolve_name("Jarvis Code", ()).decision == "none"


def test_every_resolution_is_logged(caplog):
    with caplog.at_level(logging.INFO, logger="jarvis.core.spoken_names"):
        resolve_name("Jarvis Kot", ROSTER, surface="delegate_to_agent")
    line = caplog.records[-1].getMessage()
    assert "[delegate_to_agent]" in line
    assert "heard='Jarvis Kot'" in line
    assert "Jarvis Code (jarvis-code)" in line
    assert "method=phonetic" in line and "decision=act" in line and "score=0.9" in line


def test_normalization_folds_case_umlauts_and_punctuation():
    assert normalize("Jarvis-Code!") == "jarvis code"
    assert normalize("Gmail-Überblick") == "gmail ueberblick"  # i18n-allow: folding input
    assert normalize("Grüße, Straße") == "gruesse strasse"  # i18n-allow: folding input
    assert name_tokens("unseren Jarvis Code Agent") == ("jarvis", "code")  # i18n-allow
    assert name_tokens("the agent") == ("the", "agent")  # fillers only: kept


def test_cologne_phonetics():
    assert koelner_phonetik("Jarvis") == koelner_phonetik("Jarwis") == "0738"
    assert koelner_phonetik("Kot") == koelner_phonetik("Code") == "42"
    assert koelner_phonetik("Müller-Lüdenscheidt") == "65752682"  # i18n-allow: textbook


def test_jaro_winkler_bounds():
    assert jaro_winkler("code", "code") == 1.0
    assert jaro_winkler("", "code") == 0.0
    assert 0.9 < jaro_winkler("coder", "code") < 1.0
    assert ASK_SCORE < ACT_SCORE
