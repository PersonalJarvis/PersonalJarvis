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
