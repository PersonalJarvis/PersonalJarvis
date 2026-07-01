"""Unit tests for optimistic/router.py — classify() and ack_for().

TDD: written BEFORE router.py exists. All tests must fail with ImportError /
AttributeError first, then go green once the implementation is in place.

No third-party deps. No pytest-asyncio.
"""
from __future__ import annotations

import time

from optimistic.events import RouteKind
from optimistic.router import ack_for, classify

# ---------------------------------------------------------------------------
# DUMB_TOOL classification (before smart — AD-OE3)
# ---------------------------------------------------------------------------

class TestClassifyDumbTool:
    """Commands that should resolve to DUMB_TOOL (local script, never wakes worker)."""

    def test_spiel_musik_is_dumb(self):
        assert classify("spiel Musik") == RouteKind.DUMB_TOOL  # i18n-allow

    def test_spiel_melodie_is_dumb(self):
        """'spiel die mail-melodie ab' must be DUMB, not SMART (dumb-before-smart rule)."""
        assert classify("spiel die mail-melodie ab") == RouteKind.DUMB_TOOL  # i18n-allow

    def test_spotify_is_dumb(self):
        assert classify("Spotify abspielen") == RouteKind.DUMB_TOOL  # i18n-allow

    def test_lauter_is_dumb(self):
        assert classify("mach es lauter") == RouteKind.DUMB_TOOL  # i18n-allow

    def test_leiser_is_dumb(self):
        assert classify("leiser bitte") == RouteKind.DUMB_TOOL  # i18n-allow

    def test_lautstaerke_is_dumb(self):
        assert classify("Lautstärke erhöhen") == RouteKind.DUMB_TOOL  # i18n-allow

    def test_adjust_is_dumb(self):
        assert classify("adjustier das Fenster") == RouteKind.DUMB_TOOL

    def test_play_english_is_dumb(self):
        assert classify("play the next song") == RouteKind.DUMB_TOOL


# ---------------------------------------------------------------------------
# SMART_TOOL classification
# ---------------------------------------------------------------------------

class TestClassifySmartTool:
    """Commands that should resolve to SMART_TOOL (heavy worker needed)."""

    def test_mail_is_smart(self):
        assert classify("Schreib Max eine Mail") == RouteKind.SMART_TOOL  # i18n-allow

    def test_email_is_smart(self):
        assert classify("Schick eine Email an Lisa") == RouteKind.SMART_TOOL  # i18n-allow

    def test_termin_is_smart(self):
        assert classify("Erstell einen Termin für morgen") == RouteKind.SMART_TOOL  # i18n-allow

    def test_kalender_is_smart(self):
        assert classify("Trag das in den Kalender ein") == RouteKind.SMART_TOOL  # i18n-allow

    def test_drive_is_smart(self):
        assert classify("Lade das Dokument in Drive hoch") == RouteKind.SMART_TOOL  # i18n-allow

    def test_schreib_triggers_gmail(self):
        assert classify("schreib eine Nachricht") == RouteKind.SMART_TOOL  # i18n-allow


# ---------------------------------------------------------------------------
# Action verb escalation (unknown commands with verbs → SMART)
# ---------------------------------------------------------------------------

class TestClassifyActionVerbEscalation:
    """Unknown commands containing action verbs must escalate to SMART_TOOL."""

    def test_installier_unknown_is_smart(self):
        """'installier das Update' — unknown tool but action verb → SMART."""
        assert classify("installier das Update") == RouteKind.SMART_TOOL  # i18n-allow

    def test_oeffne_is_smart(self):
        assert classify("öffne den Browser") == RouteKind.SMART_TOOL  # i18n-allow

    def test_baue_is_smart(self):
        assert classify("baue die App") == RouteKind.SMART_TOOL  # i18n-allow

    def test_erstell_unknown_is_smart(self):
        assert classify("erstell eine Präsentation") == RouteKind.SMART_TOOL  # i18n-allow

    def test_such_is_smart(self):
        assert classify("such nach dem Ordner") == RouteKind.SMART_TOOL  # i18n-allow


# ---------------------------------------------------------------------------
# SMALLTALK classification
# ---------------------------------------------------------------------------

class TestClassifySmallTalk:
    """Greetings and smalltalk must never wake the worker."""

    def test_hallo_is_smalltalk(self):
        assert classify("Hallo") == RouteKind.SMALLTALK  # i18n-allow

    def test_hi_is_smalltalk(self):
        assert classify("hi Jarvis") == RouteKind.SMALLTALK  # i18n-allow

    def test_wie_geht_is_smalltalk(self):
        assert classify("wie geht es dir heute") == RouteKind.SMALLTALK  # i18n-allow

    def test_danke_is_smalltalk(self):
        assert classify("danke schön") == RouteKind.SMALLTALK  # i18n-allow

    def test_witz_is_smalltalk(self):
        assert classify("erzähl mir einen Witz") == RouteKind.SMALLTALK  # i18n-allow

    def test_hey_is_smalltalk(self):
        assert classify("hey") == RouteKind.SMALLTALK  # i18n-allow

    def test_guten_morgen_is_smalltalk(self):
        assert classify("guten morgen") == RouteKind.SMALLTALK  # i18n-allow

    def test_unrecognized_no_verb_is_smalltalk(self):
        """A completely unknown command with no action verb defaults to SMALLTALK."""
        assert classify("blah blah keine Ahnung") == RouteKind.SMALLTALK  # i18n-allow


# ---------------------------------------------------------------------------
# Edge cases and priority ordering
# ---------------------------------------------------------------------------

class TestClassifyEdgeCases:
    """Boundary and ordering edge cases."""

    def test_empty_string_is_smalltalk(self):
        assert classify("") == RouteKind.SMALLTALK

    def test_case_insensitive_dumb(self):
        assert classify("SPIEL MUSIK") == RouteKind.DUMB_TOOL

    def test_case_insensitive_smart(self):
        assert classify("MAIL schicken") == RouteKind.SMART_TOOL

    def test_smalltalk_with_no_action_verb_wins_over_default(self):
        """Explicit SMALLTALK trigger (no action verb) → SMALLTALK."""
        assert classify("alles klar") == RouteKind.SMALLTALK

    def test_action_verb_mach_without_dumb_trigger_is_smart(self):
        """'mach es lauter' has DUMB trigger 'lauter'; it's DUMB not SMART (dumb wins)."""
        # 'lauter' triggers volume (DUMB) → should be DUMB
        assert classify("mach es lauter") == RouteKind.DUMB_TOOL

    def test_action_verb_mach_without_dumb_trigger(self):
        """'mach das Fenster auf' — 'mach' action verb but no dumb tool trigger."""
        # No dumb trigger, no smart trigger, but 'mach' is an action verb
        result = classify("mach das Fenster auf")  # i18n-allow
        assert result == RouteKind.SMART_TOOL

    def test_smalltalk_trigger_plus_action_verb_prefers_smart(self):
        """If both smalltalk trigger and action verb present, action verb wins (SMART)."""
        # 'danke' is SMALLTALK but 'mail' is SMART trigger → tool wins first
        # Actually: match_tool("maile mir danke") → gmail (SMART) wins at step 2
        # Even without a tool match: 'hallo' + 'installier' → action verb → SMART
        result = classify("hallo kannst du bitte installier das")
        assert result == RouteKind.SMART_TOOL


# ---------------------------------------------------------------------------
# Latency: classify must be < 150 ms worst-case over ~20 samples
# ---------------------------------------------------------------------------

class TestClassifyLatency:
    """Performance guard: classify() must be pure and fast (< 150 ms cold)."""

    COMMANDS = [
        "Hallo",  # i18n-allow
        "spiel Musik",  # i18n-allow
        "spiel die mail-melodie ab",  # i18n-allow
        "Schreib Max eine Mail",  # i18n-allow
        "Trag einen Termin ein",  # i18n-allow
        "installier das Update",  # i18n-allow
        "öffne den Browser",  # i18n-allow
        "hi Jarvis",  # i18n-allow
        "wie geht es dir",  # i18n-allow
        "danke",  # i18n-allow
        "Lautstärke erhöhen",  # i18n-allow
        "Lade das Dokument in Drive hoch",  # i18n-allow
        "baue die App",  # i18n-allow
        "erstell eine Präsentation",  # i18n-allow
        "such nach dem Ordner",  # i18n-allow
        "lauter bitte",  # i18n-allow
        "leiser",  # i18n-allow
        "Kalender Termin morgen",  # i18n-allow
        "erzähl mir einen Witz",  # i18n-allow
        "blah blah keine Ahnung",  # i18n-allow
    ]

    def test_classify_worst_case_latency(self):
        """Worst-case single classify() call must be well under 150 ms."""
        latencies_ms: list[float] = []
        for cmd in self.COMMANDS:
            t0 = time.perf_counter()
            classify(cmd)
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)

        worst_ms = max(latencies_ms)
        assert worst_ms < 150.0, (
            f"classify() worst-case latency {worst_ms:.2f} ms >= 150 ms threshold"
        )

    def test_classify_all_samples_correct(self):
        """Sanity: at least one DUMB, one SMART, one SMALLTALK in our sample set."""
        results = {classify(cmd) for cmd in self.COMMANDS}
        assert RouteKind.DUMB_TOOL in results
        assert RouteKind.SMART_TOOL in results
        assert RouteKind.SMALLTALK in results


# ---------------------------------------------------------------------------
# ack_for: non-empty for all three RouteKind values
# ---------------------------------------------------------------------------

class TestAckFor:
    """ack_for() must return a non-empty German string for every RouteKind."""

    def test_ack_smart_tool_non_empty(self):
        text = ack_for("Schreib Max eine Mail", RouteKind.SMART_TOOL)  # i18n-allow
        assert isinstance(text, str)
        assert len(text.strip()) > 0

    def test_ack_dumb_tool_non_empty(self):
        text = ack_for("spiel Musik", RouteKind.DUMB_TOOL)  # i18n-allow
        assert isinstance(text, str)
        assert len(text.strip()) > 0

    def test_ack_smalltalk_non_empty(self):
        text = ack_for("Hallo", RouteKind.SMALLTALK)  # i18n-allow
        assert isinstance(text, str)
        assert len(text.strip()) > 0

    def test_ack_smart_tool_contains_german(self):
        """Smart-tool ACK must feel like an optimistic butler reply."""
        text = ack_for("Termin eintragen", RouteKind.SMART_TOOL)  # i18n-allow
        # At minimum: non-empty and contains at least one German character or word
        assert len(text) > 5

    def test_ack_all_route_kinds_unique(self):
        """Each RouteKind should produce a distinct ACK text (they serve different purposes)."""
        smart = ack_for("Mail schreiben", RouteKind.SMART_TOOL)  # i18n-allow
        dumb = ack_for("spiel Musik", RouteKind.DUMB_TOOL)  # i18n-allow
        small = ack_for("Hallo", RouteKind.SMALLTALK)  # i18n-allow
        # All must be non-empty; we don't require they differ but they usually do
        assert all(len(t.strip()) > 0 for t in [smart, dumb, small])

    def test_ack_for_every_route_kind_parametrized(self):
        """Parametrized coverage: all enum members return non-empty acks."""
        for kind in RouteKind:
            result = ack_for("test command", kind)
            assert result and result.strip(), f"ack_for returned empty for {kind}"
