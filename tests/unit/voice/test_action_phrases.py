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

    def test_exit_2_names_invalid_model_response_not_screen_confusion(self) -> None:
        # Live 2026-06-20: the CU brain provider chain returned no valid action
        # response, but the user heard "I got confused on screen". Exit 2 is a
        # model/parse failure; the readback must name that class honestly.
        out = cu_failure_readback("en", error="exit 2", exit_code=2)
        assert "confused" not in out.lower(), out
        assert "valid screen-control response" in out.lower(), out

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

    def test_no_progress_guard_diagnostic_is_not_spoken(self) -> None:
        # Live bug 2026-06-20 (Angela-Merkel x.com mission): the user heard
        #   "Das am Bildschirm hat nicht geklappt: 3 identical screenshots in a
        #    row at step 9 -- the click target is unreactive or off-screen."
        # The no-progress guard writes a developer DIAGNOSTIC to stderr — it is
        # loop instrumentation, not the model's human reason — so it must never
        # be forwarded as the spoken reason. Fall back to the generic, localized
        # exit-code phrase (exit 5 == the model gave up).
        detail = (
            "[cu] no progress: 3 identical screenshots in a row at step 9 -- "
            "the click target is unreactive or off-screen."
        )
        out = cu_failure_readback("de", error="exit 5", exit_code=5, detail=detail)
        assert "identical screenshots" not in out.lower(), out
        assert "step 9" not in out.lower(), out
        assert out == action_phrase("cu_exit_gave_up", "de")

    def test_mission_profile_telemetry_never_reaches_readback(self) -> None:
        # The latency profiler appends "[cu] mission profile: ..." to stderr on
        # every _final; _cu_failure_detail forwards the WHOLE stderr block, so
        # the telemetry rode along into the spoken readback. It must never be
        # spoken or displayed.
        detail = (
            "[cu] no progress: 3 identical screenshots in a row at step 9 -- "
            "the click target is unreactive or off-screen.\n"
            "[cu] mission profile: steps=9 total=53.2s act=10.8s observe=2.1s "
            "plan=5.2s think=27.6s verify=7.2s"
        )
        out = cu_failure_readback("de", error="exit 5", exit_code=5, detail=detail)
        assert "mission profile" not in out.lower(), out
        assert "[cu]" not in out.lower(), out
        assert "steps=9" not in out, out

    def test_anti_oscillation_guard_diagnostic_is_not_spoken(self) -> None:
        # The toggle/guard family ("N guard-blocked actions this mission",
        # "toggle-stop") is internal instrumentation too — not a spoken reason.
        detail = "5 guard-blocked actions this mission (toggle-stop)"
        out = cu_failure_readback("de", error="exit 5", exit_code=5, detail=detail)
        assert "guard-blocked" not in out.lower(), out
        assert out == action_phrase("cu_exit_gave_up", "de")

    def test_internal_diagnostic_on_error_field_is_not_spoken(self) -> None:
        # The same diagnostic arriving via the ``error`` field (path 2) must be
        # rejected just like the ``detail`` path, not forwarded verbatim.
        err = "3 identical screenshots in a row at step 9 -- the click target"
        out = cu_failure_readback("de", error=err, exit_code=5)
        assert "identical screenshots" not in out.lower(), out
        assert out == action_phrase("cu_exit_gave_up", "de")


class TestCuSuccessReadback:
    """The SUCCESS sibling of cu_failure_readback (live bug 2026-06-18, session
    241a1984): "open the browser and check which tabs I have open" was answered
    only with a content-free "Done." while the verifier's observation ("...shows
    the active tab X") sat in the harness stdout and was discarded. On success we
    must SPEAK that observation so an informational request is actually answered.
    """

    def test_surfaces_verified_observation(self) -> None:
        from jarvis.voice.action_phrases import cu_success_readback

        stdout = (
            "[cu] Start: open chrome\n"
            "[cu] step 1.1: open_app {name='chrome'}\n"
            "[cu] done at step 2.1 (verified: The browser is open showing tab 'Gmail')\n"
        )
        out = cu_success_readback("en", stdout=stdout)
        assert "Gmail" in out
        assert "browser is open" in out

    def test_extracts_proof_with_inner_parens(self) -> None:
        # The proof itself can contain parentheses ("Der Browser (Chrome) ...");
        # extraction must take everything up to the FINAL closing paren.
        from jarvis.voice.action_phrases import cu_success_readback

        stdout = "[cu] done at step 2.1 (verified: Der Browser (Chrome) ist offen, Tab 'X')\n"
        out = cu_success_readback("de", stdout=stdout)
        assert "Der Browser (Chrome) ist offen" in out
        assert "Tab 'X'" in out

    def test_falls_back_to_done_without_verified_proof(self) -> None:
        from jarvis.voice.action_phrases import action_phrase, cu_success_readback

        # done line without a "(verified: ...)" segment → the plain done phrase.
        assert cu_success_readback("en", stdout="[cu] done at step 1\n") == action_phrase(
            "cu_done", "en"
        )
        # empty stdout → fallback.
        assert cu_success_readback("de", stdout="") == action_phrase("cu_done", "de")
        # opaque / empty proof → fallback (es coverage).
        assert cu_success_readback(
            "es", stdout="[cu] done at step 2 (verified: )\n"
        ) == action_phrase("cu_done", "es")
