"""Unit tests for the Gemini Live realtime adapter (google-genai Live API).

All google-genai types are faked with ``SimpleNamespace`` / plain async
generators -- these tests never touch the network or the real SDK.
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest

from jarvis.plugins.realtime.gemini_live import GeminiLiveProvider, _GeminiLiveSession
from jarvis.realtime.protocol import RealtimeSessionConfig


@pytest.mark.asyncio
async def test_can_open_duplex_session_reflects_key(monkeypatch):
    monkeypatch.setattr(
        "jarvis.plugins.realtime.gemini_live.get_provider_secret",
        lambda name: "AIza-test" if name == "gemini" else "",
    )
    assert await GeminiLiveProvider().can_open_duplex_session() is True

    monkeypatch.setattr(
        "jarvis.plugins.realtime.gemini_live.get_provider_secret", lambda name: ""
    )
    assert await GeminiLiveProvider().can_open_duplex_session() is False


def test_provider_shape():
    p = GeminiLiveProvider()
    assert p.name == "gemini-live" and p.supports_realtime is True
    assert p.input_sample_rate == 16000 and p.output_sample_rate == 24000


def _fake_message(*, data=None, server_content=None):
    return SimpleNamespace(data=data, server_content=server_content)


@pytest.mark.asyncio
async def test_receive_maps_all_five_event_types():
    """A synthetic google-genai receive() stream maps 1:1 to RealtimeEvent."""

    messages = [
        # audio_delta
        _fake_message(data=b"\x01\x02\x03\x04"),
        # output_transcript_delta
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=SimpleNamespace(text="hello there"),
                input_transcription=None,
                interrupted=False,
                turn_complete=False,
            )
        ),
        # input_transcript (final)
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=None,
                input_transcription=SimpleNamespace(text="what the user said"),
                interrupted=False,
                turn_complete=False,
            )
        ),
        # speech_started (interrupted flag)
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=None,
                input_transcription=None,
                interrupted=True,
                turn_complete=False,
            )
        ),
        # turn_complete
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=None,
                input_transcription=None,
                interrupted=False,
                turn_complete=True,
            )
        ),
    ]

    async def fake_receive():
        for msg in messages:
            yield msg

    fake_session = SimpleNamespace(receive=fake_receive)
    session = _GeminiLiveSession(
        session=fake_session,
        cm=SimpleNamespace(),
        cfg=RealtimeSessionConfig(),
        session_id="s1",
    )

    events = [ev async for ev in session.receive()]

    assert [ev.type for ev in events] == [
        "audio_delta",
        "output_transcript_delta",
        "input_transcript",
        "speech_started",
        "turn_complete",
    ]
    assert events[0].audio is not None
    assert events[0].audio.pcm == b"\x01\x02\x03\x04"
    assert events[0].audio.sample_rate == 24000
    assert events[1].text == "hello there"
    assert events[2].text == "what the user said"
    assert events[2].is_final is True


@pytest.mark.asyncio
async def test_close_calls_context_manager_aexit():
    aexit_calls = []

    class _FakeCM:
        async def __aexit__(self, *args):
            aexit_calls.append(args)

    session = _GeminiLiveSession(
        session=SimpleNamespace(),
        cm=_FakeCM(),
        cfg=RealtimeSessionConfig(),
        session_id="s1",
    )
    await session.close()
    assert aexit_calls == [(None, None, None)]


# ── open_session: selectable model + speech_config voice ─────────────────────


class _FakeConnectCM:
    async def __aenter__(self):
        return SimpleNamespace(name="fake-live-session")

    async def __aexit__(self, *_a):
        return None


class _FakeLiveAPI:
    def __init__(self) -> None:
        self.connect_calls: list[tuple[str, object]] = []

    def connect(self, *, model, config):
        self.connect_calls.append((model, config))
        return _FakeConnectCM()


class _FakeAio:
    def __init__(self) -> None:
        self.live = _FakeLiveAPI()


class _FakeGenaiClient:
    def __init__(self, *, api_key=None) -> None:
        self.api_key = api_key
        self.aio = _FakeAio()


def _patch_genai_client(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch ``google.genai.Client`` (the module attribute the adapter's lazy
    ``from google import genai`` resolves at call time) and stash the fake
    client instance created so the test can inspect what was passed to
    ``client.aio.live.connect(...)``."""
    holder: dict = {}

    def _make_client(*, api_key=None):
        client = _FakeGenaiClient(api_key=api_key)
        holder["client"] = client
        return client

    from google import genai

    monkeypatch.setattr(genai, "Client", _make_client)
    monkeypatch.setattr(
        "jarvis.plugins.realtime.gemini_live.get_provider_secret",
        lambda _name: "AIza-test",
    )
    return holder


@pytest.mark.asyncio
async def test_open_session_uses_cfg_model_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = _patch_genai_client(monkeypatch)
    prov = GeminiLiveProvider()

    await prov.open_session(
        RealtimeSessionConfig(model="gemini-2.5-flash-native-audio-preview-12-2025")
    )

    model, _config = holder["client"].aio.live.connect_calls[0]
    assert model == "gemini-2.5-flash-native-audio-preview-12-2025"


@pytest.mark.asyncio
async def test_open_session_falls_back_to_hardcoded_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    prov = GeminiLiveProvider()

    await prov.open_session(RealtimeSessionConfig(model=""))

    model, _config = holder["client"].aio.live.connect_calls[0]
    assert model == "gemini-3.1-flash-live-preview"


@pytest.mark.asyncio
async def test_open_session_builds_speech_config_when_voice_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    prov = GeminiLiveProvider()

    await prov.open_session(RealtimeSessionConfig(voice="Puck"))

    _model, config = holder["client"].aio.live.connect_calls[0]
    assert config.speech_config is not None
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"


@pytest.mark.asyncio
async def test_open_session_omits_speech_config_when_voice_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    prov = GeminiLiveProvider()

    await prov.open_session(RealtimeSessionConfig(voice=""))

    _model, config = holder["client"].aio.live.connect_calls[0]
    assert config.speech_config is None


def test_module_does_not_import_google_genai_at_top_level():
    # AP-26: the SDK import is lazy inside methods, not at module import.
    src = pathlib.Path("jarvis/plugins/realtime/gemini_live.py").read_text("utf-8")
    tree = ast.parse(src)
    top_imports = [
        n
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for n in (getattr(node, "names", []) or [])
    ]
    assert not any("google" in (a.name or "") for a in top_imports)

    # Belt and suspenders: importing the module must not pull google.genai
    # into sys.modules either.
    import sys

    sys.modules.pop("jarvis.plugins.realtime.gemini_live", None)
    had_genai_before = "google.genai" in sys.modules
    import jarvis.plugins.realtime.gemini_live  # noqa: F401

    if not had_genai_before:
        assert "google.genai" not in sys.modules
