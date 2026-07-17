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

    def test_bare_mission_profile_line_alone_is_not_spoken(self) -> None:
        # Live bug 2026-06-22 (Ed-Sheeran "Perfect" turn): the spoken readback was
        #   "That didn't work on screen: steps=3 total=9.5s act=3.0s observe=0.3s
        #    plan=1.6s think=4.6s".
        # When the detail is ONLY the "[cu] mission profile:" line (no preceding
        # diagnostic line), _CU_REASON_PREFIX_RE strips the "[cu] mission profile:"
        # PREFIX — deleting the very "[cu]"/"mission profile" markers the diagnostic
        # gate keys on — so the BARE telemetry stats sailed through and were spoken.
        # The profile is machine telemetry; detect it STRUCTURALLY and degrade to
        # the generic exit-code phrase.
        detail = (
            "[cu] mission profile: steps=3 total=9.5s act=3.0s observe=0.3s "
            "plan=1.6s think=4.6s"
        )
        out = cu_failure_readback("en", error="exit 5", exit_code=5, detail=detail)
        assert "steps=" not in out, out
        assert "total=" not in out, out
        assert "9.5s" not in out, out
        assert out == action_phrase("cu_exit_gave_up", "en")

    def test_screenshot_path_never_reaches_readback(self) -> None:
        # The harness temp capture path ("C:\\...\\pythonw_xxxx.png") rode the
        # failure detail into the spoken text on the same 2026-06-22 turn. A spoken
        # answer must never contain a filesystem path or an image filename.
        detail = (
            "steps=3 total=9.5s act=3.0s observe=0.3s plan=1.6s think=4.6s\n"
            "'C:\\Users\\Administrator\\Desktop\\Personal Jarvis\\pythonw_TPMHbe4vdZ.png'"
        )
        out = cu_failure_readback("en", error="exit 5", exit_code=5, detail=detail)
        assert ".png" not in out.lower(), out
        assert "C:\\" not in out, out
        assert "pythonw" not in out.lower(), out
        assert out == action_phrase("cu_exit_gave_up", "en")

    def test_telemetry_on_error_field_is_not_spoken(self) -> None:
        # The bare profile arriving via the ``error`` field (path 2) is rejected
        # too — same structural detection, with no "[cu]" prefix to strip.
        err = "steps=6 total=30.1s act=5.0s observe=1.4s plan=3.0s think=7.2s"
        out = cu_failure_readback("de", error=err, exit_code=5)
        assert "steps=" not in out, out
        assert out == action_phrase("cu_exit_gave_up", "de")

    def test_real_reason_with_a_number_is_still_forwarded(self) -> None:
        # False-positive guard: a genuine human reason that merely contains a
        # number (but no key=value telemetry run and no path) must still be
        # forwarded verbatim — the structural gate must not eat real answers.
        reason = "The login form returned HTTP 403 after 2 attempts."
        out = cu_failure_readback("en", error=reason, exit_code=5)
        assert reason in out


class TestNoHardcodedDashes:
    """A fixed phrase must NOT carry an em dash, en dash, or a " -- " dash-aside.

    These phrases run OFF the LLM. The announcement path humanizes + scrubs them
    (the 2026-06-29 em-dash -> comma scrub), but our OWN canned strings must not
    RELY on that downstream scrubber to honor the persona's "never use the em
    dash or dash-asides" rule — defense in depth. Live forensic 2026-06-30: the
    CU dispatch ACK "Mach ich — ich erledige das ..." was shown/spoken with a
    hard em dash (spoken twice verbatim), and the persona explicitly forbids it
    because a dash renders as a hard stop / trailing half-sentence in TTS.
    """

    def test_no_phrase_contains_a_dash_aside(self) -> None:
        from jarvis.voice.action_phrases import _PHRASES

        offenders: list[str] = []
        for key, variants in _PHRASES.items():
            for lang, template in variants.items():
                if "—" in template or "–" in template or " -- " in template:
                    offenders.append(f"{key}/{lang}: {template!r}")
        assert not offenders, (
            "fixed phrases must not carry an em/en dash or ' -- ' aside "
            "(use a comma, full stop, or connective):\n" + "\n".join(offenders)
        )


class TestNoCapableProviderPhrase:
    """Exit 3 == the Computer-Use vision-provider chain was exhausted: NO
    screen-capable AI model was reachable (every candidate keyless / depleted /
    rate-limited / no-vision). Live forensic 2026-06-30: the user heard the
    generic "couldn't get a valid screen-control response" (exit 2) while the
    real cause was a dead provider chain (Gemini credits depleted, Claude 502,
    OpenAI no key, OpenRouter model no-vision). Exit 3 must be an HONEST,
    ACTIONABLE sentence — distinct from the misleading exit-2 parse phrase — so
    the user knows to fix keys/credit, not that the model "got confused".
    """

    def test_exit_3_is_the_no_provider_phrase(self) -> None:
        for lang in ("de", "en", "es"):
            out = cu_failure_readback(lang, error="exit 3", exit_code=3)
            assert not _EXIT_TOKEN_RE.search(out), out
            assert out == action_phrase("cu_exit_no_provider", lang)
            # distinct from the exit-2 parse phrase (the misleading one).
            assert out != cu_failure_readback(lang, error="exit 2", exit_code=2)

    def test_exit_3_is_actionable_about_keys_or_credit(self) -> None:
        en = action_phrase("cu_exit_no_provider", "en")
        assert len(en) > 10
        assert "screen" in en.lower()
        # points the user at the real fix: keys / credit / settings.
        assert any(w in en.lower() for w in ("key", "credit", "settings"))

    def test_exit_3_unknown_language_falls_back_to_german(self) -> None:
        assert action_phrase("cu_exit_no_provider", "fr") == action_phrase(
            "cu_exit_no_provider", "de"
        )


class TestElevationPausePhrases:
    """Phrases for the UAC / privilege-prompt pause-and-resume flow.

    When Computer-Use hits an OS elevation prompt (Windows Secure Desktop & co.)
    it can neither see nor click it (UIPI). Instead of the misleading generic
    "couldn't see the screen" abort (exit 1), it asks the user for the one
    unavoidable confirmation click and continues — or, if no confirmation comes,
    stops with an HONEST elevation-specific message (exit 9). Both surfaces must
    exist in all supported languages.
    """

    def test_awaiting_elevation_localized_and_distinct(self) -> None:
        de = action_phrase("cu_awaiting_elevation", "de")
        en = action_phrase("cu_awaiting_elevation", "en")
        es = action_phrase("cu_awaiting_elevation", "es")
        for text in (de, en, es):
            assert len(text) > 10
        assert de != en != es
        # It must tell the user WHAT to do: confirm an admin/security prompt.
        assert "admin" in en.lower()

    def test_awaiting_elevation_unknown_language_falls_back_to_german(self) -> None:
        assert action_phrase("cu_awaiting_elevation", "fr") == action_phrase(
            "cu_awaiting_elevation", "de"
        )

    def test_exit_9_is_the_needs_elevation_phrase(self) -> None:
        # exit 9 == waited for the admin confirmation, none came. It must be an
        # elevation-specific human sentence — NOT the generic "didn't work" and
        # NOT the misleading "couldn't see the screen" (exit 1).
        for lang in ("de", "en", "es"):
            out = cu_failure_readback(lang, error="exit 9", exit_code=9)
            assert not _EXIT_TOKEN_RE.search(out), out
            assert out == action_phrase("cu_exit_needs_elevation", lang)
            assert out != action_phrase("cu_failed", lang)
            assert out != action_phrase("cu_exit_no_view", lang)

    def test_exit_9_distinct_from_no_view_and_gave_up(self) -> None:
        needs_elev = cu_failure_readback("en", error="exit 9", exit_code=9)
        no_view = cu_failure_readback("en", error="exit 1", exit_code=1)
        gave_up = cu_failure_readback("en", error="exit 5", exit_code=5)
        assert needs_elev != no_view != gave_up
        assert needs_elev != gave_up


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

    def test_blocks_raw_verifier_ui_structure_dump(self) -> None:
        """The read-goal verifier sometimes PROVES done by describing the UI
        structurally instead of answering the user. Such a dump is an internal
        evidence artifact — it must NOT be spoken verbatim. Live bug 2026-06-21
        (travel-guide HTML turn): "look at my screen and tell me which app is open"
        read out the full structural dump, including the verbatim content of an
        unrelated parallel Claude-Code AskUserQuestion box.
        """
        from jarvis.voice.action_phrases import action_phrase, cu_success_readback

        stdout = (
            "[cu] done at step 2.1 (verified: Foreground window (right, orange "
            "border, on top): title 'Eine Detail-Erkenntnis (deine Entscheidung)', "
            "content starts 'Der Staedte-Guide rendert unter der strengen "
            "Sicherheits-CSP inhaltlich und optisch vollstaendig. Nur ein kleines "
            "inline-<script>...' followed by bullet points on CSP, JS, and 'Die "
            "Aenderungen sind uncommitted...'. Bottom status bar: 'Personal "
            "Jarvis % main Opus 4.8 (1M context)')\n"
        )
        out = cu_success_readback("de", stdout=stdout)
        # The dump (and the leaked foreign box content) must NOT be spoken.
        assert out == action_phrase("cu_done", "de"), out
        assert "Detail-Erkenntnis" not in out
        assert "Foreground window" not in out
        assert "status bar" not in out.lower()

    def test_blocks_individual_ui_structure_markers(self) -> None:
        from jarvis.voice.action_phrases import action_phrase, cu_success_readback

        done_de = action_phrase("cu_done", "de")
        for proof in (
            "Foreground window shows the settings page",
            "The active window is the editor",
            "Bottom status bar: Ready",
            "Statusleiste zeigt Bereit",
            "title 'X', content starts with a heading and a paragraph",
            "a list followed by bullet points on three topics",
        ):
            stdout = f"[cu] done at step 1 (verified: {proof})\n"
            assert cu_success_readback("de", stdout=stdout) == done_de, proof

    def test_blocks_bare_telemetry_profile_and_paths_on_success(self) -> None:
        """The user reported the giant dump on SUCCESS too. The shared structural
        gate must protect the success readback: a verified-proof that is really a
        telemetry profile, or that carries a leaked screenshot path, degrades to
        the plain done phrase instead of being spoken."""
        from jarvis.voice.action_phrases import action_phrase, cu_success_readback

        done_en = action_phrase("cu_done", "en")
        for proof in (
            "steps=3 total=9.5s act=3.0s observe=0.3s plan=1.6s think=4.6s",
            "saved C:\\Users\\Administrator\\Desktop\\Personal Jarvis\\pythonw_x.png",
            "see screenshot pythonw_TPMHbe4vdZ.png",
        ):
            stdout = f"[cu] done at step 3 (verified: {proof})\n"
            assert cu_success_readback("en", stdout=stdout) == done_en, proof
