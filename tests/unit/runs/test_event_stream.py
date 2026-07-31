"""The developer event stream: summaries, lanes, environment, realtime path.

These guard the fix for the Run Inspector's central defect — it derived a
handful of panels and dropped everything else, so a realtime turn (the default
mode) rendered an empty decision path and a timeline of blank rows.
"""
from __future__ import annotations

from jarvis.runs import analyzer
from jarvis.runs.constants import (
    EVENT_CAT_BRAIN,
    EVENT_CAT_LATENCY,
    EVENT_CAT_SYSTEM,
    EVENT_CAT_VISION,
    MAX_RAW_EVENTS_PER_TURN,
    RUN_EVENT_CATEGORIES,
)
from jarvis.runs.model import RunTurn
from jarvis.sessions.models import VoiceEventRow, VoiceSessionRow
from jarvis.sessions.recorder import _RAW_EVENT_KINDS


def _ev(kind: str, ts: int = 0, seq: int = 0, **payload) -> VoiceEventRow:
    return VoiceEventRow(seq=seq, session_id="s1", turn_id="t1", ts_ms=ts,
                         kind=kind, payload=payload)


# --- summaries --------------------------------------------------------


def test_every_recorded_kind_gets_a_non_empty_summary():
    """The regression that made the timeline unreadable.

    Only six kinds used to have a summary; everything else rendered as a bare
    kind name with an empty right-hand column — ~70% of rows in a real run.
    """
    blank = [k for k in sorted(_RAW_EVENT_KINDS)
             if not analyzer.summarize_event(_ev(k, phase="x", duration_ms=1))]
    assert blank == [], f"kinds with no summary: {blank}"


def test_latency_span_summary_names_the_phase_and_duration():
    s = analyzer.summarize_event(
        _ev("LatencySpan", phase="realtime_first_audio", duration_ms=45.9)
    )
    assert "realtime_first_audio" in s
    assert "46ms" in s


def test_latency_span_summary_drops_the_redundant_session_id_detail():
    # The realtime path stamps the session id into `detail`; repeating a UUID on
    # every span buries the phase name. The full payload stays in the raw view.
    s = analyzer.summarize_event(
        _ev("LatencySpan", phase="realtime_turn_complete", duration_ms=8127.6,
            detail="session_id=e97522ea-2c2e-4ae8-8b41-3441f257c96e")
    )
    assert "e97522ea" not in s
    assert "8.13s" in s


def test_brain_completed_summary_carries_provider_tokens_and_cost():
    s = analyzer.summarize_event(
        _ev("BrainTurnCompleted", provider="grok", model="grok-4.3",
            tokens_in=72729, tokens_out=142, cost_usd=0.0913, finish_reason="stop")
    )
    assert "grok/grok-4.3" in s
    assert "72729+142 tok" in s
    assert "$0.0913" in s


def test_provider_switch_summary_shows_the_direction():
    s = analyzer.summarize_event(
        _ev("BrainProviderSwitched", from_provider="gemini", to_provider="grok")
    )
    assert "gemini" in s and "grok" in s


def test_unknown_kind_falls_back_to_a_compact_payload_rendering():
    s = analyzer.summarize_event(_ev("SomeFutureEvent", alpha="one", beta=2))
    assert "alpha=one" in s and "beta=2" in s


# --- lanes ------------------------------------------------------------


def test_event_category_maps_known_kinds_and_degrades_unknown_ones():
    assert analyzer.event_category("BrainTurnStarted") == EVENT_CAT_BRAIN
    assert analyzer.event_category("LatencySpan") == EVENT_CAT_LATENCY
    assert analyzer.event_category("CUStepProfiled") == EVENT_CAT_VISION
    # Unknown kind degrades to a neutral lane, never raises (BUG-008 contract).
    assert analyzer.event_category("TotallyNewEvent") == EVENT_CAT_SYSTEM


def test_every_recorded_kind_has_a_valid_lane():
    for kind in _RAW_EVENT_KINDS:
        assert analyzer.event_category(kind) in RUN_EVENT_CATEGORIES


# --- raw stream -------------------------------------------------------


def test_raw_events_are_chronological_with_offsets_and_payloads():
    rows, truncated = analyzer.build_raw_events(
        [_ev("SpeechSpoken", ts=1200, seq=2, text="hi", spoken_kind="reply"),
         _ev("VoiceTurnStarted", ts=1000, seq=1, turn_index=0)],
        turn_started_ms=1000,
    )
    assert truncated is False
    assert [r.kind for r in rows] == ["VoiceTurnStarted", "SpeechSpoken"]
    assert [r.offset_ms for r in rows] == [0, 200]
    # The verbatim payload rides along — that is the whole point of the panel.
    assert rows[1].payload["text"] == "hi"


def test_raw_events_truncation_is_reported_never_silent():
    many = [_ev("LatencySpan", ts=i, seq=i, phase="p", duration_ms=1)
            for i in range(MAX_RAW_EVENTS_PER_TURN + 25)]
    rows, truncated = analyzer.build_raw_events(many)
    assert len(rows) == MAX_RAW_EVENTS_PER_TURN
    assert truncated is True


def test_event_counts_are_sorted_by_frequency():
    counts = analyzer.build_event_counts(
        [_ev("LatencySpan"), _ev("LatencySpan"), _ev("SpeechSpoken")]
    )
    assert list(counts) == ["LatencySpan", "SpeechSpoken"]
    assert counts["LatencySpan"] == 2


# --- realtime decision path -------------------------------------------


def test_realtime_turn_gets_a_decision_path_instead_of_nothing():
    """The single biggest blind spot: realtime emits no BrainTurnStarted, so
    build_decision_path returned [] for the default voice mode."""
    steps = analyzer.build_decision_path([
        _ev("RealtimeSessionReady", ts=1, provider="gemini-live",
            model="gemini-3.1-flash-live-preview", surface="desktop"),
        _ev("LatencySpan", ts=2, phase="realtime_routing_decision", duration_ms=41.0),
    ])
    kinds = [s.kind for s in steps]
    assert "brain" in kinds and "route" in kinds
    brain = next(s for s in steps if s.kind == "brain")
    assert "gemini-live" in brain.rationale
    route = next(s for s in steps if s.kind == "route")
    # Honest: the span proves WHEN and how long, never WHAT was decided.
    assert "41ms" in route.rationale


def test_explicit_provider_switch_becomes_a_fallback_step():
    steps = analyzer.build_decision_path([
        _ev("BrainProviderSwitched", ts=1, from_provider="gemini", to_provider="grok"),
    ])
    fb = next(s for s in steps if s.kind == "fallback")
    assert "gemini" in fb.rationale and "grok" in fb.rationale


def test_ensure_brain_step_falls_back_to_the_turn_aggregate():
    turn = RunTurn(idx=0, trace_id="t1", provider="gemini-live",
                   model="gemini-3.1-flash-live-preview", tier="realtime")
    steps = analyzer.ensure_brain_step([], turn)
    assert steps[0].kind == "brain"
    assert "gemini-live" in steps[0].rationale
    assert steps[0].rationale_source == "rule"


def test_ensure_brain_step_never_duplicates_a_captured_brain_step():
    turn = RunTurn(idx=0, trace_id="t1", provider="grok")
    steps = analyzer.build_decision_path([
        _ev("BrainTurnStarted", ts=1, provider="grok", model="grok-4.3"),
    ])
    before = len(steps)
    analyzer.ensure_brain_step(steps, turn)
    assert len(steps) == before


# --- environment ------------------------------------------------------


def test_environment_reads_the_recorded_setup_not_the_live_config():
    session = VoiceSessionRow(
        id="s1", started_ms=0, ended_ms=1000, hangup_reason="hotkey",
        providers_used=["gemini-live"], language="de", wake_keyword="ruben",
        voice_mode="realtime",
    )
    turn = RunTurn(idx=0, trace_id="t1", provider="grok", model="grok-4.3", tier="realtime")
    env = analyzer.build_environment(
        session,
        [_ev("RealtimeSessionReady", surface="desktop",
             input_sample_rate=16000, output_sample_rate=24000),
         _ev("SpeechSpoken", voice="Fenrir", text="hi")],
        [turn],
    )
    assert env.voice_mode == "realtime"
    assert env.surface == "desktop"
    assert env.wake_source == "voice"
    assert env.hangup_reason == "hotkey"
    assert env.input_sample_rate == 16000 and env.output_sample_rate == 24000
    assert env.voices == ["Fenrir"]
    # Providers/models seen on the turns are folded in beside the session's.
    assert "gemini-live" in env.providers and "grok" in env.providers
    assert env.models == ["grok-4.3"]


def test_wake_source_classification():
    assert analyzer.wake_source("hotkey") == "hotkey"
    assert analyzer.wake_source("telegram") == "channel:telegram"
    assert analyzer.wake_source("channel:discord") == "channel:discord"
    assert analyzer.wake_source("ruben") == "voice"
