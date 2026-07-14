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


def test_session_config_defaults_to_shared_thinking_pause():
    from jarvis.core.config import SpeechConfig
    from jarvis.realtime.protocol import RealtimeSessionConfig

    assert RealtimeSessionConfig().silence_duration_ms == (
        SpeechConfig().vad_silence_ms
    ) == 1_500
