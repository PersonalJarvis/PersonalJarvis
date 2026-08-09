from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.realtime.local_server import smoke


class _Session:
    def __init__(self, events: list[object]) -> None:
        self._events = events
        self.closed = False

    async def send_text(self, text: str) -> None:
        assert text

    async def receive(self):
        for event in self._events:
            yield event

    async def close(self) -> None:
        self.closed = True


def _event(kind: str, *, text: str = "", pcm: bytes = b"", error: str = ""):
    return SimpleNamespace(
        type=kind,
        text=text,
        audio=SimpleNamespace(pcm=pcm) if pcm else None,
        error=error,
    )


@pytest.mark.asyncio
async def test_roundtrip_requires_audio_transcript_and_turn_boundary(monkeypatch) -> None:
    session = _Session(
        [
            _event("output_transcript_delta", text="The voice test works."),
            _event("audio_delta", pcm=b"\0" * 4_800),
            _event("turn_complete"),
        ]
    )

    class _Provider:
        def __init__(self, **kwargs) -> None:
            assert kwargs["base_url"] == "http://127.0.0.1:8765"

        async def open_session(self, config):
            assert config.language == "en"
            return session

    monkeypatch.setattr(
        "jarvis.plugins.realtime.openai_realtime.LocalRealtimeProvider",
        _Provider,
    )

    result = await smoke.probe_voice_roundtrip("http://127.0.0.1:8765")

    assert result["audio_bytes"] == 4_800
    assert result["first_audio_ms"] is not None
    assert session.closed is True


@pytest.mark.asyncio
async def test_roundtrip_rejects_the_text_only_failure_mode(monkeypatch) -> None:
    session = _Session(
        [
            _event("output_transcript_delta", text="The voice test works."),
            _event("turn_complete"),
        ]
    )

    class _Provider:
        def __init__(self, **kwargs) -> None:
            pass

        async def open_session(self, config):
            return session

    monkeypatch.setattr(
        "jarvis.plugins.realtime.openai_realtime.LocalRealtimeProvider",
        _Provider,
    )

    with pytest.raises(RuntimeError, match="no usable speech audio"):
        await smoke.probe_voice_roundtrip("http://127.0.0.1:8765")
    assert session.closed is True
