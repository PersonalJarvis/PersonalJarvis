"""Localized phrases for the deterministic computer-use / local-action paths."""
from __future__ import annotations

from jarvis.voice.action_phrases import action_phrase, resolve_phrase_language


class TestResolvePhraseLanguage:
    def test_explicit_pin_wins(self) -> None:
        assert resolve_phrase_language("en", "öffne den Explorer") == "en"
        assert resolve_phrase_language("es", "open the explorer") == "es"
        assert resolve_phrase_language("de", "open the explorer") == "de"

    def test_auto_detects_from_text(self) -> None:
        assert resolve_phrase_language("auto", "Could you please open Chrome for me?") == "en"
        assert resolve_phrase_language(None, "Could you please open Chrome for me?") == "en"
        assert resolve_phrase_language("auto", "Kannst du mir bitte Chrome aufmachen?") == "de"

    def test_ambiguous_keeps_german_default(self) -> None:
        assert resolve_phrase_language("auto", "ok") == "de"


class TestActionPhrase:
    def test_done_localized(self) -> None:
        assert action_phrase("cu_done", "en") == "Done."
        assert action_phrase("cu_done", "de") == "Erledigt."
        assert action_phrase("cu_done", "es") == "Listo."

    def test_unknown_language_falls_back_to_german(self) -> None:
        assert action_phrase("cu_done", "fr") == "Erledigt."

    def test_failure_with_reason_fills_field(self) -> None:
        out = action_phrase("cu_failed_reason", "en", error="HTTP 403")
        assert out == "That didn't work on screen: HTTP 403"

    def test_timeout_fills_seconds(self) -> None:
        out = action_phrase("cu_timeout", "en", secs="180")
        assert "180 seconds" in out
        assert "Erledigt" not in out

    def test_tool_failed_fills_name(self) -> None:
        assert action_phrase("tool_failed", "en", tool="open_app") == "open_app failed."
