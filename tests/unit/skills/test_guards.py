"""Characterization + contract tests for jarvis.skills.guards.

``guards.py`` is a MOVE, not a redesign: the definitional-question guard, the
block-tier check and the lifecycle filter used to live inline in
``jarvis/brain/manager.py`` and ``jarvis/skills/trigger_matcher.py``. The first
test below proves the move preserved behaviour by running both implementations
over the same corpus and comparing verdicts — including the live forensic
utterances the original guards were written for.

The rest pin the contract every other layer depends on: a closed veto
vocabulary (an unlisted reason renders as a blank cell in the Test-Match panel)
and a stable-length guard ladder (a list that shrinks as vetoes fire is useless
for "why didn't my skill fire?").
"""
from __future__ import annotations

import pytest

from jarvis.skills import guards

# ---------------------------------------------------------------------------
# Fakes (mirroring tests/unit/skills/test_local_search.py)
# ---------------------------------------------------------------------------


class _FakeRiskPolicy:
    def __init__(self, tier: str = "monitor") -> None:
        self.default_tier = tier


class _FakeFrontmatter:
    def __init__(self, tier: str = "monitor") -> None:
        self.risk_policy = _FakeRiskPolicy(tier)


class _FakeState:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeSkill:
    def __init__(
        self,
        name: str = "demo",
        state: str = "active",
        tier: str = "monitor",
        *,
        frontmatter: object | None = _FakeFrontmatter,
    ) -> None:
        self.name = name
        self.state = _FakeState(state)
        if frontmatter is _FakeFrontmatter:
            self.frontmatter: object | None = _FakeFrontmatter(tier)
        else:
            self.frontmatter = frontmatter


# ---------------------------------------------------------------------------
# Characterization: the move preserved behaviour exactly
# ---------------------------------------------------------------------------

# Every case the original guard was written against, plus the near-misses that
# make it non-trivial. Sources: the negative controls in
# scripts/skill_routing_eval.py and the forensic comments at manager.py:860-885.
_DEFINITIONAL_CORPUS: tuple[tuple[str, str], ...] = (
    # (utterance, matched trigger token) — every German string below is a
    # simulated user utterance, i.e. the speech input under test.
    ("was ist eigentlich GitHub fuer eine plattform?", "GitHub"),  # i18n-allow: speech input
    ("was ist Stripe ueberhaupt und wofuer nutzt man das?", "Stripe"),  # i18n-allow: speech input
    ("was ist Discord?", "Discord"),  # i18n-allow: speech input
    ("what is Stripe?", "Stripe"),
    ("what's gmail actually", "gmail"),
    ("erkläre mir Notion", "Notion"),  # i18n-allow: speech input
    ("wofür ist Linear da", "Linear"),  # i18n-allow: speech input
    ("tell me about Vercel", "Vercel"),
    # NOT definitional — a real data request that merely opens with "was ist".
    # The data-context word breaks the predicate run, so these must NOT veto.
    ("was ist in meinem Posteingang?", "Posteingang"),  # i18n-allow: speech input
    ("was ist auf meinem Kalender heute", "Kalender"),  # i18n-allow: speech input
    ("was ist mein naechster Termin", "Termin"),  # i18n-allow: speech input
    # Plain commands — no definitional opener at all.
    ("starte die Morgenroutine", "Morgenroutine"),  # i18n-allow: speech input
    ("schick eine Nachricht auf Discord", "Discord"),  # i18n-allow: speech input
    ("oeffne Discord", "Discord"),  # i18n-allow: speech input
    ("lies meine neuen Mails", "Mails"),  # i18n-allow: speech input
    # Umlaut token: the guard re.escapes the RAW span, so a transliterated
    # token would silently stop matching here.
    ("was ist Fähigkeit", "Fähigkeit"),  # i18n-allow: speech input
    ("was ist der Tagesüberblick", "Tagesüberblick"),  # i18n-allow: speech input
    # Degenerate inputs.
    ("", "Discord"),
    ("was ist das", ""),  # i18n-allow: speech input
)


@pytest.mark.parametrize(("utterance", "token"), _DEFINITIONAL_CORPUS)
def test_definitional_guard_matches_the_manager_implementation(
    utterance: str, token: str
) -> None:
    """The extracted guard must agree with the inline original on every case.

    This is the whole justification for calling Phase 0 a no-behaviour-change
    refactor. If it ever fails, the extraction changed routing.
    """
    from jarvis.brain.manager import _is_definitional_question_about as original

    assert guards.is_definitional_question_about(utterance, token) == original(
        utterance, token
    )


def test_definitional_guard_suppresses_a_knowledge_question() -> None:
    assert guards.is_definitional_question_about("was ist Discord?", "Discord") is True


def test_definitional_guard_allows_a_data_request_with_the_same_opener() -> None:
    """"was ist in meinem Posteingang?" is a request, not a definition."""
    assert (
        guards.is_definitional_question_about(
            "was ist in meinem Posteingang?", "Posteingang"
        )
        is False
    )


def test_definitional_guard_needs_the_raw_span_not_a_normalized_token() -> None:
    """Feeding a transliterated token blinds the guard — pin that it matters.

    Documents WHY MatchCandidate.evidence must carry the untouched span: the
    guard escapes the token into a pattern run against the original text.
    """
    spoken = "was ist Fähigkeit"  # i18n-allow: speech input
    raw_span = "Fähigkeit"  # i18n-allow: speech input
    transliterated = "Faehigkeit"  # i18n-allow: speech input
    assert guards.is_definitional_question_about(spoken, raw_span) is True
    assert guards.is_definitional_question_about(spoken, transliterated) is False


def test_definitional_predicate_cache_is_bounded() -> None:
    """The compiled-pattern cache must not grow without bound."""
    guards._PREDICATE_CACHE.clear()
    for index in range(guards._PREDICATE_CACHE_MAX + 25):
        guards.is_definitional_question_about(f"was ist tok{index}", f"tok{index}")
    assert len(guards._PREDICATE_CACHE) <= guards._PREDICATE_CACHE_MAX


# ---------------------------------------------------------------------------
# Lifecycle / risk guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["active", "validated"])
def test_matchable_states_pass_the_lifecycle_guard(state: str) -> None:
    assert guards.lifecycle_veto(_FakeSkill(state=state)) is None


def test_draft_never_passes_the_lifecycle_guard() -> None:
    """AP-15: an agent-authored draft must never fire automatically."""
    assert guards.lifecycle_veto(_FakeSkill(state="draft")) == guards.VETO_DRAFT_STATE


def test_disabled_reports_its_own_reason() -> None:
    """A user-disabled skill and a draft are both vetoed, but not the same way."""
    assert (
        guards.lifecycle_veto(_FakeSkill(state="disabled"))
        == guards.VETO_DISABLED_STATE
    )


def test_block_tier_is_vetoed() -> None:
    assert guards.block_tier_veto(_FakeSkill(tier="block")) == guards.VETO_BLOCK_TIER


@pytest.mark.parametrize("tier", ["safe", "monitor", "ask"])
def test_non_block_tiers_pass(tier: str) -> None:
    assert guards.block_tier_veto(_FakeSkill(tier=tier)) is None


def test_missing_frontmatter_is_vetoed_separately_from_block_tier() -> None:
    """Resolution the old boolean _skill_is_blocked could not express.

    Both still veto — the verdict is unchanged — but the panel can now say
    which one fired.
    """
    broken = _FakeSkill(frontmatter=None)
    assert guards.frontmatter_veto(broken) == guards.VETO_NO_FRONTMATTER
    assert guards.block_tier_veto(broken) is None


def test_block_tier_verdict_matches_the_manager_implementation() -> None:
    """Old ``_skill_is_blocked`` == (frontmatter_veto or block_tier_veto)."""
    from jarvis.brain.manager import BrainManager

    for skill in (
        _FakeSkill(tier="block"),
        _FakeSkill(tier="safe"),
        _FakeSkill(tier="monitor"),
        _FakeSkill(tier="ask"),
        _FakeSkill(frontmatter=None),
    ):
        extracted = bool(
            guards.frontmatter_veto(skill) or guards.block_tier_veto(skill)
        )
        assert extracted == BrainManager._skill_is_blocked(skill)


# ---------------------------------------------------------------------------
# Brain-side guards (verdicts arrive as plain data — no reverse import)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["direct", "computer_use", "DIRECT"])
def test_local_action_gate_claims_the_turn(mode: str) -> None:
    """Live bug 2026-06-21: plugin-discord must not suppress Computer-Use."""
    assert guards.local_action_veto(mode) == guards.VETO_LOCAL_ACTION


@pytest.mark.parametrize("mode", [None, "", "unsupported"])
def test_a_pure_dispatch_keeps_its_skill(mode: str | None) -> None:
    assert guards.local_action_veto(mode) is None


def test_explicit_heavy_request_stands_the_skill_down() -> None:
    """AD-S9: an utterance naming the vehicle is a mission, not a skill.

    Forensic example: "spawne einen Sub-Agent für Gmail".  <!-- i18n-allow -->
    """
    assert guards.explicit_heavy_veto(True) == guards.VETO_EXPLICIT_HEAVY
    assert guards.explicit_heavy_veto(False) is None


def test_guards_module_has_no_jarvis_imports() -> None:
    """A reverse import would close the jarvis.brain -> jarvis.skills cycle."""
    from pathlib import Path

    import jarvis.skills.guards as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("#", "*"))
    )
    assert "import jarvis" not in code
    assert "from jarvis" not in code


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def test_ladder_passes_a_clean_skill() -> None:
    ladder = guards.evaluate_guards(
        _FakeSkill(), user_text="starte die Morgenroutine", evidence="Morgenroutine"
    )
    assert ladder.passed
    assert ladder.vetoed_by == ""
    assert all(result.verdict == "pass" for result in ladder.results)


def test_ladder_reports_every_guard_even_after_a_veto() -> None:
    """A ladder that shrinks as vetoes fire cannot answer "what else checked?"."""
    ladder = guards.evaluate_guards(_FakeSkill(state="draft"))
    assert len(ladder.results) == len(guards.GUARD_ORDER)
    assert tuple(r.guard for r in ladder.results) == guards.GUARD_ORDER
    assert ladder.vetoed_by == guards.VETO_DRAFT_STATE
    assert [r.verdict for r in ladder.results].count("veto") == 1


def test_ladder_reports_the_first_veto_in_evaluation_order() -> None:
    """A draft block-tier skill reports the lifecycle veto, not the tier one."""
    ladder = guards.evaluate_guards(_FakeSkill(state="draft", tier="block"))
    assert ladder.vetoed_by == guards.VETO_DRAFT_STATE


def test_ladder_catches_the_definitional_over_fire() -> None:
    ladder = guards.evaluate_guards(
        _FakeSkill(name="plugin-discord"),
        user_text="was ist Discord?",
        evidence="Discord",
    )
    assert ladder.vetoed_by == guards.VETO_DEFINITIONAL
    assert not ladder.passed


def test_every_reachable_veto_string_is_in_the_closed_vocabulary() -> None:
    """An unlisted reason renders as a blank cell in the UI — the silent
    version of a multi-layer drift bug."""
    produced = {
        guards.frontmatter_veto(_FakeSkill(frontmatter=None)),
        guards.block_tier_veto(_FakeSkill(tier="block")),
        guards.lifecycle_veto(_FakeSkill(state="draft")),
        guards.lifecycle_veto(_FakeSkill(state="disabled")),
        guards.local_action_veto("direct"),
        guards.explicit_heavy_veto(True),
    }
    produced.discard(None)
    assert produced <= guards.VETO_REASONS


def test_ladder_vetoes_are_always_in_the_closed_vocabulary() -> None:
    cases = (
        guards.evaluate_guards(_FakeSkill(frontmatter=None)),
        guards.evaluate_guards(_FakeSkill(state="draft")),
        guards.evaluate_guards(_FakeSkill(state="disabled")),
        guards.evaluate_guards(_FakeSkill(tier="block")),
        guards.evaluate_guards(_FakeSkill(), is_explicit_heavy=True),
        guards.evaluate_guards(_FakeSkill(), local_action_mode="computer_use"),
        guards.evaluate_guards(
            _FakeSkill(), user_text="was ist Discord?", evidence="Discord"
        ),
    )
    for ladder in cases:
        assert ladder.vetoed_by in guards.VETO_REASONS
