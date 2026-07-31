"""Golden tests for the regex literal miner.

``mine_pattern_literals`` is the highest-leverage and highest-risk part of the
relevance scorer: it turns every author-written voice trigger into vocabulary,
and a scanner bug would silently poison EVERY skill at once — no crash, no log
line, just a matcher that quietly stops working. So the expectations below were
derived BY HAND from the real builtin patterns before the function existed. If
you change the scanner and a case here flips, re-derive it by hand rather than
pasting in whatever the new code produces; the alternative is a test that only
asserts the code does what the code does.

The patterns are the actual ones shipped in jarvis/skills/builtin/*/SKILL.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis.skills.relevance import mine_pattern_literals, normalize_text, trigrams

# ---------------------------------------------------------------------------
# Hand-derived golden cases (real builtin patterns)
# ---------------------------------------------------------------------------

# (label, pattern, expected literal runs as a SET)
# Every German string is trigger vocabulary under test.  # i18n-allow: speech input
_GOLDEN: tuple[tuple[str, str, set[str]], ...] = (
    (
        "morning-routine/voice",
        r"(morgenroutine|morgen[-\s]?briefing|morning routine|morning briefing"
        r"|start day|tages(ue|ü)berblick)",  # i18n-allow: speech input
        {
            "morgenroutine",  # i18n-allow: speech input
            "morgen",  # i18n-allow: speech input
            "briefing",
            "morning routine",
            "morning briefing",
            "start day",
            "tages",  # i18n-allow: speech input
            "ue",
            "ü",  # i18n-allow: speech input
            "berblick",  # i18n-allow: speech input
        },
    ),
    (
        "morning-routine/greeting",
        r"^(guten morgen|good morning)[.!\s]*$",  # i18n-allow: speech input
        {"guten morgen", "good morning"},  # i18n-allow: speech input
    ),
    (
        "plugin-gmail",
        r"(gmail|postfach|posteingang|meine? mails?|neue mails?)",  # i18n-allow: speech input
        {
            "gmail",
            "postfach",  # i18n-allow: speech input
            "posteingang",  # i18n-allow: speech input
            "mein",  # i18n-allow: speech input
            "mail",
            "neue mail",  # i18n-allow: speech input
        },
    ),
    (
        "plugin-github",
        r"(github|pull request|pull-request|\bpr\b|repo|repository)",
        {"github", "pull request", "pull-request", "pr", "repo", "repository"},
    ),
    (
        "plugin-google_calendar",
        r"(google.?(kalender|calendar)|gcal|(in )?(meine[nm]? )?"
        r"(kalender|termin|meeting))",  # i18n-allow: speech input
        {
            "google",
            "kalender",  # i18n-allow: speech input
            "calendar",
            "gcal",
            "in",
            "meine",  # i18n-allow: speech input
            "termin",  # i18n-allow: speech input
            "meeting",
        },
    ),
    (
        "deep-work-mode",
        r"^(deep[-\s]?work([-\s]?mode| modus)?|fokus[-\s]?modus"
        r"|konzentrations[-\s]?modus|starte (deep work|fokus(modus)?"
        r"|konzentration(smodus)?)|aktiviere (deep work|fokus(modus)?))$",  # i18n-allow
        {
            "deep",
            "work",
            "mode",
            "modus",  # i18n-allow: speech input
            "fokus",  # i18n-allow: speech input
            "konzentrations",  # i18n-allow: speech input
            "starte",  # i18n-allow: speech input
            "deep work",
            "konzentration",  # i18n-allow: speech input
            "smodus",  # i18n-allow: speech input
            "aktiviere",  # i18n-allow: speech input
        },
    ),
)


@pytest.mark.parametrize(("label", "pattern", "expected"), _GOLDEN, ids=[g[0] for g in _GOLDEN])
def test_mining_matches_the_hand_derived_expectation(
    label: str, pattern: str, expected: set[str]
) -> None:
    assert set(mine_pattern_literals(pattern)) == expected


# ---------------------------------------------------------------------------
# Scanner rules, individually
# ---------------------------------------------------------------------------


def test_alternation_and_groups_split_runs() -> None:
    assert set(mine_pattern_literals(r"(alpha|beta)")) == {"alpha", "beta"}


def test_character_class_ends_a_run() -> None:
    """``deep[-\\s]?work`` guarantees "deep" and "work", not "deepwork"."""
    assert set(mine_pattern_literals(r"deep[-\s]?work")) == {"deep", "work"}


def test_negated_and_literal_bracket_classes_are_skipped_whole() -> None:
    assert set(mine_pattern_literals(r"a[^]x]b")) == {"a", "b"}
    assert set(mine_pattern_literals(r"a[]x]b")) == {"a", "b"}


def test_escaped_metacharacter_is_a_literal() -> None:
    assert set(mine_pattern_literals(r"a\.b")) == {"a.b"}
    assert set(mine_pattern_literals(r"c\-d")) == {"c-d"}


def test_class_escape_ends_a_run() -> None:
    assert set(mine_pattern_literals(r"foo\sbar")) == {"foo", "bar"}
    assert set(mine_pattern_literals(r"\bpr\b")) == {"pr"}


def test_optional_char_is_dropped_not_kept() -> None:
    """``meine?`` guarantees "mein"; the trailing "e" is optional."""
    assert set(mine_pattern_literals(r"meine?")) == {"mein"}  # i18n-allow: speech input


def test_star_and_plus_behave_like_optional() -> None:
    assert set(mine_pattern_literals(r"abcd*")) == {"abc"}
    assert set(mine_pattern_literals(r"abcd+")) == {"abc"}


def test_brace_quantifier_contributes_no_literal() -> None:
    assert set(mine_pattern_literals(r"ab\w{0,2}cd")) == {"ab", "cd"}


def test_anchors_and_dot_end_runs() -> None:
    assert set(mine_pattern_literals(r"^abc.def$")) == {"abc", "def"}


def test_multi_token_run_survives_as_a_phrase() -> None:
    """Phrases are the point: "morning routine" is worth more than two words."""
    assert "morning routine" in mine_pattern_literals(r"(morning routine|x)")


def test_whitespace_only_runs_are_dropped() -> None:
    assert set(mine_pattern_literals(r"(a| |b)")) == {"a", "b"}


@pytest.mark.parametrize("pattern", ["", "   ", "()", "|||", r"[", r"(unclosed"])
def test_degenerate_patterns_do_not_raise(pattern: str) -> None:
    assert isinstance(mine_pattern_literals(pattern), tuple)


def test_mining_is_deterministic() -> None:
    pattern = r"(morgenroutine|morning routine|tages(ue|ü)berblick)"  # i18n-allow: speech input
    first = mine_pattern_literals(pattern)
    assert all(mine_pattern_literals(pattern) == first for _ in range(20))


# ---------------------------------------------------------------------------
# Smoke test over every REAL builtin pattern
# ---------------------------------------------------------------------------


def _builtin_patterns() -> list[tuple[str, str]]:
    root = Path(__file__).resolve().parents[3] / "jarvis" / "skills" / "builtin"
    found: list[tuple[str, str]] = []
    for skill_file in sorted(root.rglob("SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        for raw in re.findall(r"^\s*pattern:\s*(.+)$", text, re.M):
            cleaned = raw.split("#")[0].strip().strip("\"'")
            if cleaned:
                found.append((skill_file.parent.name, cleaned))
    return found


def test_every_builtin_pattern_yields_usable_vocabulary() -> None:
    """A silent scanner failure on a real pattern is the nightmare case.

    Every shipped trigger must produce at least one token of length >= 3 —
    otherwise that skill contributes nothing to the index and can never be
    found by paraphrase, which is exactly the bug this system exists to fix.
    """
    patterns = _builtin_patterns()
    assert patterns, "no builtin trigger patterns found — the loader path moved"

    barren: list[str] = []
    for name, pattern in patterns:
        literals = mine_pattern_literals(pattern.replace("\\\\", "\\"))
        usable = [
            lit for lit in literals if len(normalize_text(lit).replace(" ", "")) >= 3
        ]
        if not usable:
            barren.append(f"{name}: {pattern}")
    assert not barren, "patterns that mine to nothing usable:\n" + "\n".join(barren)


# ---------------------------------------------------------------------------
# Trigram channel — the compound recovery this whole design depends on
# ---------------------------------------------------------------------------


def test_trigrams_recover_a_german_compound_split_by_the_scanner() -> None:
    """The load-bearing case for the trigram channel.

    The trigger "tages(ue|ue)berblick" mines to "tages" + "berblick" — the  <!-- i18n-allow -->
    group boundary
    destroys the compound. A user saying the word as one token must still
    reach the skill, and only a character-level channel can do that.
    """
    spoken = trigrams("tagesueberblick")  # i18n-allow: speech input
    indexed = trigrams("tages") | trigrams("berblick")  # i18n-allow: speech input
    overlap = len(spoken & indexed)
    assert overlap / len(spoken) > 0.5

    unrelated = trigrams("gmail") | trigrams("postfach")  # i18n-allow: speech input
    assert len(spoken & unrelated) / len(spoken) < 0.2


def test_trigrams_are_umlaut_insensitive() -> None:
    assert trigrams("fähigkeit") == trigrams("faehigkeit")  # i18n-allow: speech input


def test_trigrams_of_a_too_short_surface_are_empty() -> None:
    assert trigrams("") == frozenset()
