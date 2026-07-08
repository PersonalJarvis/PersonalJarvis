from uuid import uuid4

from jarvis.core.events import LatencyPhase, LatencySpan


def test_realtime_phases_exist_and_are_accepted_by_the_span_guard():
    for phase in (
        LatencyPhase.REALTIME_INPUT_COMMITTED,
        LatencyPhase.REALTIME_FIRST_TRANSCRIPT,
        LatencyPhase.REALTIME_FIRST_AUDIO,
    ):
        span = LatencySpan(trace_id=uuid4(), phase=phase.value, duration_ms=1.0)
        assert span.phase == phase.value


def test_unknown_realtime_phase_still_rejected():
    import pytest

    with pytest.raises(ValueError):
        LatencySpan(trace_id=uuid4(), phase="realtime_not_a_phase")
