"""The first-audio budget judges the USER-perceived wait, not session age.

``first_audio_ms`` counts from session start and therefore includes the
user's own speaking/listening time: a live codex call measured 8 311 ms from
start while the wait after the user's final was 923 ms, and the old
assertion flagged that healthy call as a budget breach. The budget now
prefers ``first_final_to_first_audio_ms`` (first user FINAL → first audible
provider frame) and falls back to ``first_audio_ms`` only where that newer
metric is absent — otherwise pre-metric recordings and adapters that never
stamp it would escape the budget entirely.
"""

from __future__ import annotations

from jarvis.diagnostics.realtime_forensics import (
    SPAWN_FIRST_AUDIO_BUDGET_MS,
    SPAWN_READY_BUDGET_MS,
    evaluate_postmortem,
)


def _healthy() -> dict[str, object]:
    return {
        "session_id": "first-final-budget",
        "provider": "codex-subscription-realtime",
        "turns_completed": 2,
        "ready_ms": 2400,
        "first_audio_ms": 4100,
        "close_clean": True,
    }


def _kinds(pm: dict[str, object]) -> set[str]:
    return {finding.kind for finding in evaluate_postmortem(pm)}


def test_response_latency_over_budget_is_the_spawn_finding() -> None:
    over = _healthy() | {
        "first_final_to_first_audio_ms": SPAWN_FIRST_AUDIO_BUDGET_MS + 1
    }
    findings = evaluate_postmortem(over)
    assert [(f.severity, f.kind) for f in findings] == [
        ("warn", "spawn-over-budget")
    ]


def test_the_codex_8311ms_case_no_longer_reads_as_a_breach() -> None:
    # The measured live shape: first audio 8.3 s from session start, but only
    # 923 ms after the user's final — a healthy call, not a slow spawn.
    healthy_codex = _healthy() | {
        "first_audio_ms": 8_311,
        "first_final_to_first_audio_ms": 923,
    }
    assert _kinds(healthy_codex) == set()


def test_a_measured_wait_after_the_final_overrides_a_large_session_age() -> None:
    # The session-start measurement is inflated by design, so once the newer
    # metric is present it alone decides — no double-flagging via the
    # fallback.
    healthy_after_long_listen = _healthy() | {
        "first_audio_ms": 30_000,
        "first_final_to_first_audio_ms": 900,
    }
    assert _kinds(healthy_after_long_listen) == set()


def test_a_legacy_recording_is_still_judged_against_the_budget() -> None:
    # No ``first_final_to_first_audio_ms`` at all (old capture, or an adapter
    # that never stamps it): the legacy metric is judged so a 30 s first
    # audio cannot pass forensics unjudged.
    legacy_breach = _healthy() | {"first_audio_ms": 30_000}
    findings = evaluate_postmortem(legacy_breach)
    assert [(f.severity, f.kind) for f in findings] == [
        ("warn", "spawn-over-budget")
    ]


def test_a_legacy_recording_inside_the_budget_stays_clean() -> None:
    legacy_ok = _healthy() | {"first_audio_ms": SPAWN_FIRST_AUDIO_BUDGET_MS}
    assert _kinds(legacy_ok) == set()


def test_no_measurement_at_all_stays_quiet() -> None:
    # Both metrics zero means nothing was ever measured — the missing audio
    # is reported by other counters, never as a fabricated spawn overrun.
    unmeasured = _healthy() | {
        "first_audio_ms": 0,
        "first_final_to_first_audio_ms": 0,
    }
    assert _kinds(unmeasured) == set()


def test_ready_budget_is_untouched() -> None:
    slow_ready = _healthy() | {"ready_ms": SPAWN_READY_BUDGET_MS + 1}
    assert _kinds(slow_ready) == {"spawn-over-budget"}


def test_detail_line_carries_both_measurements_for_continuity() -> None:
    over = _healthy() | {
        "first_audio_ms": 8_311,
        "first_final_to_first_audio_ms": SPAWN_FIRST_AUDIO_BUDGET_MS + 500,
    }
    (finding,) = evaluate_postmortem(over)
    assert "after the first user final" in finding.detail
    assert "8311 ms from session start" in finding.detail


def test_detail_line_names_the_legacy_metric_when_it_was_judged() -> None:
    (finding,) = evaluate_postmortem(_healthy() | {"first_audio_ms": 30_000})
    assert "30000 ms from session start" in finding.detail
    assert "after the first user final" not in finding.detail
