"""Tests for MissionReadback — DE templates + name-neutral tone + 280-cap."""
from __future__ import annotations

from jarvis.missions.voice.readback import (
    MAX_VOICE_CHARS,
    READBACK_TEMPLATES,
    MissionReadback,
)

# --- Tone-Anchor: name-neutral mission-status templates ---
# The mission-status templates carry NO hardcoded owner name so a fresh
# clone never speaks the maintainer's name. The persona rule still holds:
# never "Sir"/"Mr. Stark"/"Tony"/"boss". Each test asserts the right status
# phrase is present AND no owner name leaked in.


def _assert_no_owner_name(out: str) -> None:
    """Guard: the spoken text must be name-neutral and never use 'Sir'."""
    for forbidden in ("Alex", "Sir", "Mr. Stark", "Tony", "boss"):
        assert forbidden not in out, f"owner/forbidden name leaked: {out!r}"


def test_approved_is_name_neutral() -> None:
    rb = MissionReadback()
    out = rb.render_approved(summary="X")
    # Approved templates: "Fertig./Erledigt./Abgeschlossen. {summary}".  # i18n-allow: quotes the actual German TTS readback templates
    assert "Fertig" in out or "Erledigt" in out or "Abgeschlossen" in out, (  # i18n-allow: asserts the German TTS readback text
        f"expected an approved status phrase in {out!r}"
    )
    _assert_no_owner_name(out)


def test_failed_is_name_neutral() -> None:
    rb = MissionReadback()
    out = rb.render_failed(reason="kaputt")
    # Failed templates frame it as "gescheitert" / "nicht geklappt".  # i18n-allow: quotes the actual German TTS readback templates
    assert "gescheitert" in out.lower() or "nicht geklappt" in out.lower(), (  # i18n-allow: asserts the German TTS readback text
        f"expected a failure status phrase in {out!r}"
    )
    _assert_no_owner_name(out)


# --- #7 (2026-05-27 hardening audit): render_failed must never speak a raw
#     snake_case reason token, and crash_recovery is not a failure. The reason
#     map is shared with the MissionAnnouncer so the two voice paths can't
#     drift (FAILURE_REASON_PHRASES, single source).


def test_render_failed_maps_known_reason_to_human_phrase() -> None:
    rb = MissionReadback()
    out = rb.render_failed(reason="critic_loop_exhausted", language="de")
    assert "critic_loop_exhausted" not in out, f"raw reason leaked: {out!r}"
    assert "Drei Versuche" in out, f"mapped phrase missing: {out!r}"


def test_render_failed_maps_known_reason_en() -> None:
    rb = MissionReadback()
    out = rb.render_failed(reason="budget_exceeded", language="en")
    assert "budget_exceeded" not in out
    assert "cost limit" in out.lower()


def test_render_failed_crash_recovery_uses_dedicated_template() -> None:
    """crash_recovery is a swept previous mission, not a task failure — it
    must speak the dedicated non-alarming template, never 'Grund:
    crash_recovery' or framing it as 'gescheitert'."""
    rb = MissionReadback()
    out = rb.render_failed(reason="crash_recovery", language="de")
    assert "crash_recovery" not in out
    assert "gescheitert" not in out.lower(), (
        f"crash_recovery must not be framed as a failure: {out!r}"
    )
    assert "abgebrochen" in out.lower() or "vorherige" in out.lower(), (
        f"expected the dedicated crash-recovery wording, got {out!r}"
    )


def test_render_failed_unknown_reason_still_interpolated() -> None:
    """An unmapped reason falls back to the raw text (no regression for
    free-form failure reasons)."""
    rb = MissionReadback()
    out = rb.render_failed(reason="kaputt", language="de")
    assert "kaputt" in out


def test_render_failed_maps_worktree_setup_failed() -> None:
    """#8 (2026-05-27): a worktree-create failure (path cap / git index lock)
    must speak an actionable cause, not the generic 'Der Worker ist  # i18n-allow: quotes the actual German TTS readback phrase
    abgebrochen.' that a real worker crash produces."""
    rb = MissionReadback()
    out = rb.render_failed(reason="worktree_setup_failed", language="de")
    assert "worktree_setup_failed" not in out
    assert "Arbeitsbereich" in out, f"expected actionable cause, got {out!r}"


def test_render_failed_maps_git_missing_to_actionable_phrase() -> None:
    """AP-23 wave-2 audit finding 1: a missing git binary must speak an
    actionable cause, not the generic worktree_setup_failed phrase."""
    rb = MissionReadback()
    out_de = rb.render_failed(reason="git_missing", language="de")
    assert "git_missing" not in out_de
    assert "Git" in out_de, f"expected actionable git cause, got {out_de!r}"

    out_en = rb.render_failed(reason="git_missing", language="en")
    assert "git_missing" not in out_en
    assert "git" in out_en.lower() and "path" in out_en.lower(), (
        f"expected actionable git cause, got {out_en!r}"
    )


def test_render_failed_maps_git_not_a_repository_to_zip_install_phrase() -> None:
    """Facet of finding 1: the ZIP/no-.git install must be distinguished
    from a missing git binary with its own actionable phrase."""
    rb = MissionReadback()
    out_de = rb.render_failed(reason="git_not_a_repository", language="de")
    assert "git_not_a_repository" not in out_de
    assert "Git" in out_de, f"expected actionable git cause, got {out_de!r}"

    out_en = rb.render_failed(reason="git_not_a_repository", language="en")
    assert "git_not_a_repository" not in out_en
    assert "zip" in out_en.lower(), f"expected the ZIP-install hint, got {out_en!r}"


def test_render_failed_maps_missing_source_checkout_to_capability_phrase() -> None:
    """A copied/container install is healthy for standalone missions, so its
    source-task failure must describe the missing capability rather than tell
    the user that the installation itself is broken."""
    rb = MissionReadback()
    reason = "source_checkout_unavailable"

    out_de = rb.render_failed(reason=reason, language="de")
    assert reason not in out_de
    assert "Quellcode" in out_de, f"expected source capability cause, got {out_de!r}"

    out_en = rb.render_failed(reason=reason, language="en")
    assert reason not in out_en
    assert "source checkout" in out_en.lower(), (
        f"expected source capability cause, got {out_en!r}"
    )


def test_git_setup_reasons_carry_spanish() -> None:
    """CLAUDE.md §1: Spanish is an equal supported product-surface language,
    so every portable workspace-setup reason added here must carry an ``es``
    phrase rather than perpetuating the de/en-only gap."""
    from jarvis.missions.voice.readback import FAILURE_REASON_PHRASES

    es = FAILURE_REASON_PHRASES.get("es", {})
    assert "git_missing" in es, "git_missing missing from the es phrase map"
    assert "git_not_a_repository" in es, (
        "git_not_a_repository missing from the es phrase map"
    )
    assert "source_checkout_unavailable" in es, (
        "source_checkout_unavailable missing from the es phrase map"
    )
    # Natural Spanish, not a snake_case token or a de/en copy.
    assert "git" in es["git_missing"].lower()
    assert "git_missing" not in es["git_missing"]
    assert "zip" in es["git_not_a_repository"].lower()
    assert "código" in es["source_checkout_unavailable"].lower()


def test_render_failed_maps_attempts_timed_out_to_honest_timeout_phrase() -> None:
    """Live deep-dive 2026-06-07 (mission 019ea1da): a Computer-Use mission
    whose final iteration hit the 630s wall-clock cap was failed with the
    generic ``task_error`` reason, so the user heard a "worker aborted" phrase
    for a timeout. A worker that ran out of time on every attempt must speak an
    HONEST timeout phrase — never the alarming worker-abort wording that a real
    worker crash produces."""
    rb = MissionReadback()
    out = rb.render_failed(reason="attempts_timed_out", language="de")
    assert "attempts_timed_out" not in out, f"raw reason leaked: {out!r}"
    assert "Zeitlimit" in out, f"expected an honest timeout phrase, got {out!r}"
    assert "abgebrochen" not in out.lower(), (
        f"a timeout must NOT be framed as a worker abort: {out!r}"
    )


def test_render_failed_maps_attempts_timed_out_en() -> None:
    rb = MissionReadback()
    out = rb.render_failed(reason="attempts_timed_out", language="en")
    assert "attempts_timed_out" not in out
    assert "time limit" in out.lower(), f"expected an honest timeout phrase, got {out!r}"
    assert "aborted" not in out.lower()


# --- interrupted recovery reason (2026-06-07, commit 13b86605) ---
# startup_recover now emits MissionFailed(reason="interrupted") for stale
# missions that produced real partial work. The voice layer must render a
# friendly, non-alarming phrase — not a KeyError and not "Grund: interrupted".
# It must also be suppressed at announce time (same as crash_recovery) so the
# user is not woken up by a boot-time housekeeping event.


def test_render_failed_interrupted_renders_non_empty_de() -> None:
    """'interrupted' must map to a non-empty DE phrase — no KeyError, no raw
    snake_case token spoken."""
    rb = MissionReadback()
    out = rb.render_failed(reason="interrupted", language="de")
    assert "interrupted" not in out, f"raw reason leaked: {out!r}"
    assert out.strip(), "interrupted must produce a non-empty phrase in DE"


def test_render_failed_interrupted_renders_non_empty_en() -> None:
    """'interrupted' must map to a non-empty EN phrase — no raw snake_case
    framing like 'Reason: interrupted' or 'The task failed. interrupted'."""
    rb = MissionReadback()
    out = rb.render_failed(reason="interrupted", language="en")
    # The phrase may contain the English word "interrupted" (it's in the human
    # text), but must NOT be the raw "Reason: interrupted" / task-failed frame.
    assert "Reason: interrupted" not in out, f"raw reason leaked (EN): {out!r}"
    assert "task failed" not in out.lower(), f"failure frame must not appear (EN): {out!r}"
    assert out.strip(), "interrupted must produce a non-empty phrase in EN"


def test_render_failed_interrupted_uses_non_alarming_template() -> None:
    """'interrupted' is a swept mission with partial results — it must NOT be
    framed as a catastrophic failure (no 'gescheitert'/'not geklappt' wording
    that implies everything went wrong)."""
    rb = MissionReadback()
    out = rb.render_failed(reason="interrupted", language="de")
    assert "gescheitert" not in out.lower(), (
        f"interrupted must not be framed as a failure: {out!r}"
    )


def test_failure_reason_phrases_de_en_parity() -> None:
    """BUG-008 discipline: every key in FAILURE_REASON_PHRASES['de'] must
    also exist in ['en'] and vice-versa. A symmetric add-or-remove in one
    lang only cannot silently survive this gate."""
    from jarvis.missions.voice.readback import FAILURE_REASON_PHRASES

    de_keys = set(FAILURE_REASON_PHRASES["de"])
    en_keys = set(FAILURE_REASON_PHRASES["en"])
    assert de_keys == en_keys, (
        f"FAILURE_REASON_PHRASES parity broken — "
        f"only-DE: {de_keys - en_keys}, only-EN: {en_keys - de_keys}"
    )
    # Pin 'interrupted' explicitly so a future symmetric removal from BOTH
    # langs cannot silently regress the 2026-06-07 fix.
    assert "interrupted" in de_keys, "'interrupted' missing from DE map"
    assert "interrupted" in en_keys, "'interrupted' missing from EN map"


def test_failure_reason_phrases_shared_with_announcer() -> None:
    """Drift guard (BUG-008 five-layer pattern): the announcer must use the
    SAME reason->phrase map as the readback, so the listener and announcer
    voice paths never diverge."""
    from jarvis.missions.voice.readback import FAILURE_REASON_PHRASES

    # Both voice components resolve a known reason to the identical phrase.
    assert (
        FAILURE_REASON_PHRASES["de"]["critic_unavailable"]
        == "Der Prüfer ist abgestürzt, die Arbeit liegt im Worktree."  # i18n-allow: asserts the actual German TTS readback phrase
    )
    # Pin the timeout key so a symmetric removal from BOTH de+en (which the
    # set-equality check below would NOT catch) can never silently regress the
    # 2026-06-07 fix back to a raw snake_case token on the voice path.
    assert "attempts_timed_out" in FAILURE_REASON_PHRASES["de"]
    assert "attempts_timed_out" in FAILURE_REASON_PHRASES["en"]
    assert set(FAILURE_REASON_PHRASES["de"]) == set(FAILURE_REASON_PHRASES["en"])


def test_budget_warn_is_name_neutral() -> None:
    """Budget-Warns are name-neutral (they run in the background)."""
    rb = MissionReadback()
    out = rb.render_budget_warn(pct=50)
    assert "Budget" in out
    _assert_no_owner_name(out)


def test_budget_exceeded_is_name_neutral() -> None:
    rb = MissionReadback()
    out = rb.render_budget_exceeded()
    assert "Budget" in out or "Limit" in out
    _assert_no_owner_name(out)


def test_injection_blocked_is_name_neutral() -> None:
    rb = MissionReadback()
    out = rb.render_injection_blocked()
    assert "Injection" in out or "geblockt" in out.lower()
    _assert_no_owner_name(out)


def test_no_template_contains_sir_anywhere() -> None:
    """Strict A1: not a single template may contain 'Sir'/'sir'."""
    from jarvis.missions.voice.readback import READBACK_TEMPLATES
    for key, lang_map in READBACK_TEMPLATES.items():
        for lang, templates in lang_map.items():
            for tpl in templates:
                assert "Sir" not in tpl, f"{key}/{lang}: 'Sir' in {tpl!r}"
                assert "sir" not in tpl.lower().split(), (
                    f"{key}/{lang}: 'sir' standalone in {tpl!r}"
                )


# --- 280-char Cap ---


def test_approved_truncates_long_summary() -> None:
    rb = MissionReadback()
    huge = "x" * 1000
    out = rb.render_approved(summary=huge)
    assert len(out) <= MAX_VOICE_CHARS


def test_failed_truncates_long_reason() -> None:
    rb = MissionReadback()
    huge = "y" * 1000
    out = rb.render_failed(reason=huge)
    assert len(out) <= MAX_VOICE_CHARS


def test_destructive_confirm_truncates_long_target() -> None:
    rb = MissionReadback()
    huge = "Z" * 1000
    out = rb.render_destructive_confirm(target=huge)
    assert len(out) <= MAX_VOICE_CHARS


def test_max_voice_chars_is_280() -> None:
    assert MAX_VOICE_CHARS == 280


# --- Anti-Repeat (PhrasePicker-Pattern) ---


def test_anti_repeat_two_consecutive_approved_differ() -> None:
    """With multiple templates per key, two consecutive
    render calls should return different templates (anti-repeat-window=3).
    """
    rb = MissionReadback(anti_repeat_window=3)
    out1 = rb.render_approved(summary="X")
    out2 = rb.render_approved(summary="X")
    # With at least 2 templates per key, these should differ
    assert out1 != out2


def test_anti_repeat_window_zero_allows_repeats() -> None:
    """anti_repeat_window=1 -> only the last 1 is remembered -> with 3 templates
    it starts over again after 2 calls."""
    rb = MissionReadback(anti_repeat_window=1)
    outputs = [rb.render_approved(summary="X") for _ in range(5)]
    # at least one repeat may occur when pool > window
    assert len(set(outputs)) <= 3


# --- Render-Variants ---


def test_render_timeout_de() -> None:
    rb = MissionReadback()
    out = rb.render_timeout(language="de")
    assert out
    assert any(w in out.lower() for w in ("timeout", "zeit", "stop"))


def test_render_cancelled_de() -> None:
    rb = MissionReadback()
    out = rb.render_cancelled(language="de")
    assert out
    assert "abgebrochen" in out.lower() or "gestoppt" in out.lower() or "stop" in out.lower()


def test_render_crash_recovery_de() -> None:
    rb = MissionReadback()
    out = rb.render_crash_recovery(language="de")
    assert "crash" in out.lower() or "abgebrochen" in out.lower()


def test_render_destructive_confirm_includes_target() -> None:
    rb = MissionReadback()
    out = rb.render_destructive_confirm(target="prod_users")
    assert "prod_users" in out


def test_render_iteration_running_includes_n() -> None:
    rb = MissionReadback()
    out = rb.render_iteration_running(n=2)
    assert "2" in out


def test_render_budget_warn_50_diff_from_80() -> None:
    rb = MissionReadback()
    out_50 = rb.render_budget_warn(pct=50)
    rb2 = MissionReadback()
    out_80 = rb2.render_budget_warn(pct=80)
    # Both buckets are name-neutral but use different wording.
    assert out_50 != out_80


def test_render_budget_warn_exact_50() -> None:
    rb = MissionReadback()
    out = rb.render_budget_warn(pct=50)
    assert "Budget" in out
    _assert_no_owner_name(out)


def test_render_budget_warn_above_80_picks_80() -> None:
    rb = MissionReadback()
    out = rb.render_budget_warn(pct=95)
    # 80er-Bucket — name-neutral, with "achtzig" or "knapp".
    assert "achtzig" in out.lower() or "knapp" in out.lower()
    _assert_no_owner_name(out)


# --- Empty / fallback ---


def test_render_approved_empty_summary_uses_default() -> None:
    rb = MissionReadback()
    out = rb.render_approved(summary="")
    assert "erledigt" in out.lower() or "aufgabe" in out.lower()
    _assert_no_owner_name(out)


def test_render_failed_empty_reason_uses_default() -> None:
    rb = MissionReadback()
    out = rb.render_failed(reason="")
    assert out
    # Default reason is "unbekannter Fehler"  # i18n-allow: quotes the actual German TTS readback default phrase
    assert "fehler" in out.lower() or "unbekannt" in out.lower()  # i18n-allow: asserts the German TTS readback text
    _assert_no_owner_name(out)


def test_render_en_fallback_when_lang_unknown() -> None:
    rb = MissionReadback()
    # Test that en works at all
    out = rb.render_approved(summary="done", language="en")
    assert out
    assert "done" in out.lower() or "completed" in out.lower()
    _assert_no_owner_name(out)


# --- LLM-Narrative-Leak Defense ---


def test_correction_instruction_not_in_iteration_template() -> None:
    """ADR-0009 §1: render_iteration_running must NOT pass through the
    LLM correction_instruction — only "Iteration N laeuft.".  # i18n-allow: quotes the actual German TTS readback phrase
    """
    rb = MissionReadback()
    # API: render_iteration_running only takes `n`, NO correction_instruction param.
    # Verify this stays true via template inspection.
    pool = READBACK_TEMPLATES["iteration_running"]["de"]
    for tmpl in pool:
        assert "{correction" not in tmpl
        assert "{instruction" not in tmpl


def test_template_keys_complete() -> None:
    """Ensure all critical template keys are defined."""
    required_keys = {
        "approved", "failed", "timeout", "cancelled",
        "budget_warn_50", "budget_warn_80", "budget_exceeded",
        "injection_blocked", "path_guard_blocked",
        "destructive_confirm", "crash_recovery", "iteration_running",
    }
    for key in required_keys:
        assert key in READBACK_TEMPLATES
        assert READBACK_TEMPLATES[key].get("de"), f"DE pool empty for {key}"
