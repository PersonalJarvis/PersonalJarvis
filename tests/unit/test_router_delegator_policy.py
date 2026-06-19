"""Policy tests for the router delegator prompt.

These tests make NO LLM calls — they only pin that the policy strings are
present in SYSTEM_PROMPT. Real classification-accuracy tests live in
test_tier_router.py (goldset with 50 utterances).

Update 2026-06-10 (user mandate): spawn_worker is reserved for GENUINELY
HEAVY tasks. Light AND medium requests (news/knowledge/research questions,
single reads, anything the router can finish inline with its own tools) are
handled by the router itself — thinking a little longer is fine; a
multi-minute worker mission for a single lookup is not. The
on-uncertainty default flipped accordingly: try it yourself first,
delegate only clear heavy chunks.
"""
from __future__ import annotations

from jarvis.brain.router import SYSTEM_PROMPT


class TestDelegatorPolicyInPrompt:
    def test_delegator_principle_present(self) -> None:
        assert "Delegator" in SYSTEM_PROMPT or "Dispatcher" in SYSTEM_PROMPT
        assert "Millisekunden" in SYSTEM_PROMPT or "reasonst nicht lange" in SYSTEM_PROMPT.lower()

    def test_three_categories_named(self) -> None:
        assert "TRIVIAL" in SYSTEM_PROMPT
        assert "DIRECT_ACTION" in SYSTEM_PROMPT
        assert "SPAWN_WORKER" in SYSTEM_PROMPT

    def test_spawn_is_reserved_for_heavy_tasks(self) -> None:
        """The heavy-only doctrine must be pinned verbatim (2026-06-10)."""
        assert "NUR fuer wirklich schwere" in SYSTEM_PROMPT, (
            "spawn_worker must be explicitly reserved for genuinely heavy "
            "tasks — the over-spawning complaint of 2026-06-10."
        )

    def test_minutes_vs_seconds_cost_framing_present(self) -> None:
        """The honest cost framing (worker = minutes, inline = seconds)
        replaces the old false '5 seconds' claim that justified spawning."""
        low = SYSTEM_PROMPT.lower()
        assert "minuten" in low, (
            "prompt must state that a worker mission costs MINUTES so the "
            "model stops treating delegation as the cheap option"
        )

    def test_default_on_uncertainty_is_self_serve(self) -> None:
        """Flipped 2026-06-10: when unsure, try it yourself — do NOT default
        to delegation. The old doctrine spawned a worker mission for plain
        news questions."""
        assert "BEI UNSICHERHEIT: DELEGIERE" not in SYSTEM_PROMPT, (
            "the old delegate-on-uncertainty doctrine resurfaced — it made "
            "the router spawn worker missions for simple questions"
        )
        assert "BEI UNSICHERHEIT: MACH ES SELBST" in SYSTEM_PROMPT
        # The reversed doctrine keeps 'delegier' for the genuinely heavy case.
        assert "delegier" in SYSTEM_PROMPT.lower()

    def test_news_question_routed_to_search_web_not_spawn(self) -> None:
        """The concrete 2026-06-10 complaint, pinned as a prompt example:
        a news question is answered inline via search_web, never spawned."""
        low = SYSTEM_PROMPT.lower()
        assert "search_web" in low
        assert "news" in low

    def test_build_trigger_words_listed(self) -> None:
        # Heavy-task examples must keep naming the classic build verbs.
        low = SYSTEM_PROMPT.lower()
        for word in ["bau", "programmier", "refactor", "plane", "analysier"]:
            assert word in low, f"heavy-example verb missing: {word}"

    def test_on_screen_belongs_to_direct_action(self) -> None:
        low = SYSTEM_PROMPT.lower()
        # Desktop/Screen actions must be explicitly categorized as DIRECT_ACTION.
        # The prompt has been reworded across branches; accept any of the
        # canonical phrasings the brain has historically used.
        assert (
            "on-screen" in low
            or "screen bedienen" in low
            or "pc bedienen" in low
            or ("dispatch_to_harness" in low and "klicken" in low and "tippen" in low)
        )

    def test_trivial_examples_mention_facts(self) -> None:
        low = SYSTEM_PROMPT.lower()
        # Fact questions must be categorized as TRIVIAL (no sub-agent).
        assert "einstein" in low or "hauptstadt" in low or "fakten" in low

    def test_forbidden_behaviors_listed(self) -> None:
        assert "VERBOTEN" in SYSTEM_PROMPT
        low = SYSTEM_PROMPT.lower()
        assert "selber" in low or "selbst" in low

    def test_wellbeing_smalltalk_is_not_status_filler(self) -> None:
        """Wellbeing-smalltalk ("wie geht's") is routed as TRIVIAL.

        Historically this test pinned three exact phrases — "wie geht's
        dir", "ich bin einsatzbereit", "betriebsstatus" — that were once
        present in the router prompt to bias the brain toward a friendly
        one-sentence reply rather than a robotic "betriebsstatus
        nominal" filler. The prompt has since been compressed: the
        wellbeing example survives as "wie geht's" under the TRIVIAL
        bullet list, but the bespoke filler-prevention paragraph was
        removed. Pin the structural property (wellbeing IS a TRIVIAL
        example, and smalltalk is explicitly listed) rather than the
        exact phrasing so prompt edits do not break the test on every
        wording tweak.
        """
        low = SYSTEM_PROMPT.lower()
        assert "wie geht" in low, (
            "Wellbeing-smalltalk must remain a TRIVIAL example"
        )
        assert "trivial" in low and "smalltalk" in low, (
            "TRIVIAL category must explicitly call out smalltalk so the brain "
            "does not over-spawn on friendly questions"
        )

    def test_absolute_rules_preserved(self) -> None:
        # The existing ABSOLUTE REGELN (anti-hallucination) stay in.
        assert "ABSOLUTE REGELN" in SYSTEM_PROMPT
        assert "Provider" in SYSTEM_PROMPT  # anti-provider-switch rule

    def test_argument_format_preserved(self) -> None:
        # The SPAWN_WORKER argument format (action/target) stays documented.
        assert "action" in SYSTEM_PROMPT
        assert "target" in SYSTEM_PROMPT
        assert "utterance" in SYSTEM_PROMPT
