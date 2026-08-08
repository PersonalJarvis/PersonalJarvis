"""The first-audio budget judges the USER-perceived wait, not session age.

``first_audio_ms`` counts from session start and therefore includes the
user's own speaking/listening time: a live codex call measured 8 311 ms from
start while the wait after the user's final was 923 ms, and the old
assertion flagged that healthy call as a budget breach. The budget now
judges ``first_final_to_first_audio_ms`` (first user FINAL → first audible
provider frame); ``first_audio_ms`` stays in the payload and the detail
line for continuity with old recordings but is never asserted against the
budget anymore.
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


def test_legacy_first_audio_alone_never_breaches_the_budget() -> None:
    # An old recording without the new metric must not fabricate a breach
    # from the misleading session-start measurement.
    legacy = _healthy() | {"first_audio_ms": SPAWN_FIRST_AUDIO_BUDGET_MS + 1}
    assert _kinds(legacy) == set()


def test_zero_means_never_measured_and_stays_quiet() -> None:
    assert _kinds(_healthy() | {"first_final_to_first_audio_ms": 0}) == set()


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
