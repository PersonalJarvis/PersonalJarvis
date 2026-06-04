"""Policy-Tests fuer den Router-Delegator-Prompt.

Diese Tests sind KEINE LLM-Aufrufe — sie pruefen nur dass die Policy-
Strings im SYSTEM_PROMPT sind. Echte Klassifikations-Accuracy-Tests
gibt es in test_tier_router.py (Goldset mit 50 Utterances).
"""
from __future__ import annotations

from jarvis.brain.router import SYSTEM_PROMPT


class TestDelegatorPolicyInPrompt:
    def test_delegator_principle_present(self) -> None:
        assert "Delegator" in SYSTEM_PROMPT
        assert "reasonst NIE" in SYSTEM_PROMPT or "reasonst nicht lange" in SYSTEM_PROMPT.lower() or "Millisekunden" in SYSTEM_PROMPT

    def test_three_categories_named(self) -> None:
        assert "TRIVIAL" in SYSTEM_PROMPT
        assert "DIRECT_ACTION" in SYSTEM_PROMPT
        assert "SPAWN_OPENCLAW" in SYSTEM_PROMPT

    def test_five_second_threshold_mentioned(self) -> None:
        # Der 5-Sekunden-Heuristik sollte irgendwo referenziert sein
        assert "5 Sekunden" in SYSTEM_PROMPT or "5 sec" in SYSTEM_PROMPT or "laenger als" in SYSTEM_PROMPT.lower()

    def test_default_on_uncertainty_is_delegate(self) -> None:
        low = SYSTEM_PROMPT.lower()
        assert "unsicherheit" in low or "unsicher" in low
        # und der Tenor "im Zweifel delegieren"
        assert "delegier" in low

    def test_build_trigger_words_listed(self) -> None:
        # Wichtige Trigger-Worte die zu SPAWN fuehren muessen
        low = SYSTEM_PROMPT.lower()
        for word in ["bau", "programmier", "refactor", "plane", "analysier"]:
            assert word in low, f"Trigger-Wort fehlt: {word}"

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
        # Fakten-Fragen sollten als TRIVIAL kategorisiert sein (keine Sub-Agent)
        assert "einstein" in low or "hauptstadt" in low or "fakten" in low

    def test_forbidden_behaviors_listed(self) -> None:
        assert "VERBOTEN" in SYSTEM_PROMPT
        low = SYSTEM_PROMPT.lower()
        # Selber komplexe Aufgabe
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
        # Die existierenden ABSOLUTE REGELN (Anti-Halluzination) bleiben drin
        assert "ABSOLUTE REGELN" in SYSTEM_PROMPT
        assert "Provider" in SYSTEM_PROMPT  # Anti-Provider-Switch-Regel

    def test_argument_format_preserved(self) -> None:
        # SPAWN_OPENCLAW-Argument-Format (action/target) bleibt dokumentiert
        assert "action" in SYSTEM_PROMPT
        assert "target" in SYSTEM_PROMPT
        assert "utterance" in SYSTEM_PROMPT
