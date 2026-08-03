from jarvis.core.protocols import PLUGIN_GROUPS


def test_realtime_group_registered():
    assert "jarvis.realtime" in PLUGIN_GROUPS


def test_protocol_types_importable():
    from jarvis.realtime.protocol import (
        RealtimeEvent,
        RealtimeProvider,
        RealtimeSession,
        RealtimeSessionConfig,
    )

    ev = RealtimeEvent(type="audio_delta")
    assert ev.type == "audio_delta"
    cfg = RealtimeSessionConfig(instructions="hi", language="en")
    assert cfg.language == "en"
    # Protocols are runtime_checkable.
    assert hasattr(RealtimeProvider, "_is_runtime_protocol")
    assert hasattr(RealtimeSession, "_is_runtime_protocol")


def test_session_config_has_selectable_model():
    from jarvis.realtime.protocol import RealtimeSessionConfig

    # Default "" -> the adapter's hardcoded fallback model (no regression).
    assert RealtimeSessionConfig().model == ""
    cfg = RealtimeSessionConfig(model="gpt-realtime-2.1", voice="echo")
    assert cfg.model == "gpt-realtime-2.1"
    assert cfg.voice == "echo"


def test_session_config_defaults_to_provider_native_turn_detection():
    from jarvis.realtime.protocol import RealtimeSessionConfig

    # None = the provider's native turn detection decides the turn end; the
    # Settings "Thinking pause" endpoints the classic pipeline only
    # (maintainer directive 2026-07-21).
    assert RealtimeSessionConfig().silence_duration_ms is None


def test_session_config_history_defaults_empty():
    from jarvis.realtime.protocol import RealtimeSessionConfig

    # The first open of a call carries no history; only a mid-call reopen
    # (transport rebuild / cross-family fallback) seeds it (BUG-088).
    assert RealtimeSessionConfig().history == ()


def test_event_declares_the_usage_payload_the_adapters_already_emit():
    """The shipped API-billed adapters emit ``type="usage"`` with a payload.

    The contract described neither the event type nor the field, so an adapter
    built against this dataclass — rather than against a private one — raised
    AttributeError inside the receive pump the moment it reported usage.
    """
    from jarvis.realtime.protocol import RealtimeEvent, RealtimeEventType

    assert "usage" in RealtimeEventType.__args__
    assert RealtimeEvent(type="audio_delta").usage is None
    event = RealtimeEvent(type="usage", usage={"input_tokens": 12})
    assert event.usage == {"input_tokens": 12}


def test_event_can_mark_a_self_initiated_interruption():
    """Jarvis's own interrupt() must be distinguishable from a barge-in.

    Without the flag the orchestrator reads its own cancellation as "the user
    started speaking" and arms the user-speech state against a user who never
    said anything.
    """
    from jarvis.realtime.protocol import RealtimeEvent

    assert RealtimeEvent(type="interrupted").self_initiated is False
    assert RealtimeEvent(type="interrupted", self_initiated=True).self_initiated
