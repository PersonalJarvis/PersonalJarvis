"""Shared realtime postmortem verdicts (probe + diag script consume these).

The whole point of the module is that the live-call probe and the
flight-recorder audit judge a session identically — so these tests pin the
verdict boundaries, not the wording.
"""

from __future__ import annotations

from jarvis.diagnostics.realtime_forensics import (
    SPAWN_FIRST_AUDIO_BUDGET_MS,
    SPAWN_READY_BUDGET_MS,
    evaluate_postmortem,
    extract_postmortems,
)


def _healthy() -> dict[str, object]:
    return {
        "session_id": "healthy",
        "provider": "codex-subscription-realtime",
        "turns_completed": 4,
        "ready_ms": 2400,
        "first_audio_ms": 4100,
        "close_clean": True,
    }


def _kinds(pm: dict[str, object]) -> set[str]:
    return {finding.kind for finding in evaluate_postmortem(pm)}


def test_a_healthy_session_yields_no_findings() -> None:
    assert evaluate_postmortem(_healthy()) == []


def test_every_failure_class_maps_to_its_finding() -> None:
    sick = _healthy() | {
        "response_splices": 2,
        "quiescence_boundary_turns": 4,
        "ungrounded_captions_dropped": 3,
        "ungrounded_responses_refused": 7,
        "self_dialogue_rebuilds": 1,
        "handoff_obligation_misses": 2,
        "mute_emergency_releases": 2,
        "max_loop_stall_ms": 15_000,
        "sender_pacing_resyncs": 1,
        "sender_shed_frames": 40,
        "recv_dropped_frames": 5,
        "rebuilds": 2,
        "stun_retries": 1,
        "language_flips": 2,
        "ready_ms": SPAWN_READY_BUDGET_MS + 1,
        "first_audio_ms": SPAWN_FIRST_AUDIO_BUDGET_MS + 1,
        "close_clean": False,
    }
    assert _kinds(sick) == {
        "splice-seam",
        "turn-without-boundary",
        "ungrounded-caption-storm",
        "refusal-storm",
        "self-dialogue",
        "handoff-obligation-miss",
        "mute-emergency-release",
        "event-loop-stall",
        "sender-behind-wall-clock",
        "recv-overflow",
        "rebuild-loop",
        "language-mismatch",
        "spawn-over-budget",
        "unclean-close",
    }


def test_severity_boundaries_hold() -> None:
    # One ungrounded caption is a warn; two are the storm.
    one = evaluate_postmortem(_healthy() | {"ungrounded_captions_dropped": 1})
    assert [(f.severity, f.kind) for f in one] == [("warn", "ungrounded-caption")]
    # Refusals: quiet below 3, warn at 3-4, high at the adapter's storm verdict.
    assert _kinds(_healthy() | {"ungrounded_responses_refused": 2}) == set()
    assert _kinds(_healthy() | {"ungrounded_responses_refused": 3}) == {"refusal-run"}
    assert _kinds(_healthy() | {"ungrounded_responses_refused": 5}) == {
        "refusal-storm"
    }
    # A single rebuild is a warn; two (or any STUN retry) are the loop class.
    assert _kinds(_healthy() | {"rebuilds": 1}) == {"transport-rebuild"}
    assert _kinds(_healthy() | {"stun_retries": 1}) == {"rebuild-loop"}
    # A sub-second scheduling wobble is not a stall.
    assert _kinds(_healthy() | {"max_loop_stall_ms": 900}) == set()
    # first_audio_ms == 0 means "no audio ever" — reported by other counters,
    # never as a spawn overrun.
    assert _kinds(_healthy() | {"first_audio_ms": 0}) == set()
    # One language flip is normal conversation behaviour (de -> en on purpose).
    assert _kinds(_healthy() | {"language_flips": 1}) == set()
    one_handoff_miss = evaluate_postmortem(
        _healthy() | {"handoff_obligation_misses": 1}
    )
    assert [(f.severity, f.kind) for f in one_handoff_miss] == [
        ("warn", "handoff-obligation-miss")
    ]


def test_extract_postmortems_reads_both_capture_shapes() -> None:
    recorder_row = {
        "event": "RealtimeSessionPostmortem",
        "payload": {"session_id": "a", "turns_completed": 1},
    }
    probe_row = {"src": "postmortem", "data": {"session_id": "b"}}
    noise = [
        {"event": "VoiceTurnCompleted", "payload": {}},
        {"src": "notif", "data": {"method": "thread/realtime/x"}},
        {"src": "postmortem", "data": "not-a-mapping"},
    ]
    payloads = extract_postmortems([recorder_row, probe_row, *noise])
    assert [p.get("session_id") for p in payloads] == ["a", "b"]
