"""Localized phrases for the deterministic computer-use / local-action paths."""
from __future__ import annotations

import re

from jarvis.voice.action_phrases import (
    action_phrase,
    cu_failure_readback,
    resolve_phrase_language,
)

# A bare exit-code token (e.g. "exit 5") must NEVER reach the user-facing
# readback — that is the live bug this module guards against.
_EXIT_TOKEN_RE = re.compile(r"\bexit\s*\d+\b", re.IGNORECASE)


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


class TestCuFailureReadback:
    """The CU failure readback must never leak a raw exit code (live bug:
    the user heard "That didn't work on screen: exit 5" and asked "what is
    the exit file?"). A bare ``exit N`` / numeric-only error gets mapped to a
    static, localized, human sentence; a real reason sentence is forwarded."""

    def test_bare_exit_code_never_leaks_to_readback(self) -> None:
        # The literal upstream error string that leaked live.
        out = cu_failure_readback("en", error="exit 5", exit_code=5)
        assert not _EXIT_TOKEN_RE.search(out), out
        # And it is a human, plain-language sentence — not empty, not numeric.
        assert len(out) > 10
        assert "5" not in out or "screen" in out.lower()

    def test_bare_exit_code_localized_de_es(self) -> None:
        de = cu_failure_readback("de", error="exit 5", exit_code=5)
        es = cu_failure_readback("es", error="exit 5", exit_code=5)
        assert not _EXIT_TOKEN_RE.search(de), de
        assert not _EXIT_TOKEN_RE.search(es), es
        # German fallback for an unknown language too.
        fr = cu_failure_readback("fr", error="exit 5", exit_code=5)
        assert not _EXIT_TOKEN_RE.search(fr), fr

    def test_exit_5_is_the_gave_up_phrase(self) -> None:
        # exit 5 == the model's `fail` action: it could not complete the task.
        out = cu_failure_readback("en", error="exit 5", exit_code=5)
        assert "screen" in out.lower()

    def test_exit_124_timeout_phrase_distinct_from_gave_up(self) -> None:
        timed_out = cu_failure_readback("en", error="exit 124", exit_code=124)
        gave_up = cu_failure_readback("en", error="exit 5", exit_code=5)
        assert not _EXIT_TOKEN_RE.search(timed_out), timed_out
        assert timed_out != gave_up

    def test_empty_error_still_yields_a_human_sentence(self) -> None:
        out = cu_failure_readback("en", error="", exit_code=8)
        assert len(out) > 10
        assert not _EXIT_TOKEN_RE.search(out), out

    def test_none_error_yields_a_human_sentence(self) -> None:
        out = cu_failure_readback("en", error=None, exit_code=None)
        assert len(out) > 10
        assert not _EXIT_TOKEN_RE.search(out), out

    def test_real_reason_sentence_is_forwarded_verbatim(self) -> None:
        # When the harness provides the model's actual `fail` reason, FORWARD it
        # (it gets scrubbed downstream) — do not replace it with a generic phrase.
        reason = "The BridgeMind server has no visible news channel."
        out = cu_failure_readback("en", error=reason, exit_code=5)
        assert reason in out

    def test_numeric_only_error_is_treated_as_opaque(self) -> None:
        out = cu_failure_readback("en", error="5", exit_code=5)
        assert "5" not in out or "screen" in out.lower()
        assert not _EXIT_TOKEN_RE.search(out), out

    def test_harness_stderr_reason_is_extracted_from_cu_prefix(self) -> None:
        # The screenshot loop writes "[cu] fail at <tag>: <reason>" to stderr;
        # when that reaches us we surface the human reason, not "exit 5".
        detail = "[cu] fail at step-3: BridgeMind has no news channel"
        out = cu_failure_readback("en", error="exit 5", exit_code=5, detail=detail)
        assert "BridgeMind has no news channel" in out
        assert not _EXIT_TOKEN_RE.search(out), out
