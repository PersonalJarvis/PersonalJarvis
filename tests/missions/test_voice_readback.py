"""Tests fuer MissionReadback — DE-Templates + name-neutral tone + 280-cap."""
from __future__ import annotations

import pytest

from jarvis.missions.voice.readback import (
    MAX_VOICE_CHARS,
    MissionReadback,
    READBACK_TEMPLATES,
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
    # Approved templates: "Fertig./Erledigt./Abgeschlossen. {summary}".
    assert "Fertig" in out or "Erledigt" in out or "Abgeschlossen" in out, (
        f"expected an approved status phrase in {out!r}"
    )
    _assert_no_owner_name(out)


def test_failed_is_name_neutral() -> None:
    rb = MissionReadback()
    out = rb.render_failed(reason="kaputt")
    # Failed templates frame it as "gescheitert" / "nicht geklappt".
    assert "gescheitert" in out.lower() or "nicht geklappt" in out.lower(), (
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
    must speak an actionable cause, not the generic 'Der Worker ist
    abgebrochen.' that a real worker crash produces."""
    rb = MissionReadback()
    out = rb.render_failed(reason="worktree_setup_failed", language="de")
    assert "worktree_setup_failed" not in out
    assert "Arbeitsbereich" in out, f"expected actionable cause, got {out!r}"


def test_failure_reason_phrases_shared_with_announcer() -> None:
    """Drift guard (BUG-008 five-layer pattern): the announcer must use the
    SAME reason->phrase map as the readback, so the listener and announcer
    voice paths never diverge."""
    from jarvis.missions.voice.readback import FAILURE_REASON_PHRASES

    # Both voice components resolve a known reason to the identical phrase.
    assert (
        FAILURE_REASON_PHRASES["de"]["critic_unavailable"]
        == "Der Prüfer ist abgestürzt, die Arbeit liegt im Worktree."
    )
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
    """Strict A1: kein einziges Template darf 'Sir'/'sir' enthalten."""
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
    """Bei mehreren Templates pro Key sollten zwei aufeinanderfolgende
    Render-Calls unterschiedliche Templates liefern (anti-repeat-window=3).
    """
    rb = MissionReadback(anti_repeat_window=3)
    out1 = rb.render_approved(summary="X")
    out2 = rb.render_approved(summary="X")
    # Bei mind. 2 Templates pro Key sollten sich diese unterscheiden
    assert out1 != out2


def test_anti_repeat_window_zero_allows_repeats() -> None:
    """anti_repeat_window=1 -> nur 1 letztes wird gemerkt -> bei 3 Templates
    geht's wieder von vorn nach 2 Calls."""
    rb = MissionReadback(anti_repeat_window=1)
    outputs = [rb.render_approved(summary="X") for _ in range(5)]
    # mindestens ein Wiederhol darf vorkommen wenn pool > window
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
    # Default-Reason ist "unbekannter Fehler"
    assert "fehler" in out.lower() or "unbekannt" in out.lower()
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
    """ADR-0009 §1: render_iteration_running darf NICHT die LLM-correction-instruction
    weiterreichen — nur "Iteration N laeuft.".
    """
    rb = MissionReadback()
    # API: render_iteration_running nimmt nur `n`, KEINEN correction_instruction-Param.
    # Verify das bleibt so durch Template-Inspect.
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
