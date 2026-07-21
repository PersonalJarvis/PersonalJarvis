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
