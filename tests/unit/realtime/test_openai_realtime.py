"""Unit tests for the OpenAI GA realtime adapter's model/voice selection.

The ``openai`` SDK client is faked with plain objects (no network) so these
tests only assert what :func:`OpenAIRealtimeProvider.open_session` forwards
to ``client.realtime.connect(...)`` and ``conn.session.update(...)``.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
from jarvis.realtime.protocol import RealtimeSessionConfig


class _FakeConn:
    def __init__(self) -> None:
        self.session_updates: list[dict[str, Any]] = []

    @property
    def session(self) -> _FakeConn:
        return self

    async def update(self, session: dict[str, Any]) -> None:
        self.session_updates.append(session)


class _FakeConnectCM:
    """What ``client.realtime.connect(model=...)`` returns — an async CM."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_a: object) -> None:
        return None


class _FakeRealtimeAPI:
    def __init__(self) -> None:
        self.connect_calls: list[str] = []
        self.last_conn = _FakeConn()

    def connect(self, *, model: str) -> _FakeConnectCM:
        self.connect_calls.append(model)
        return _FakeConnectCM(self.last_conn)


class _FakeAsyncOpenAI:
    def __init__(self, *, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.realtime = _FakeRealtimeAPI()


def _patch_openai_client(monkeypatch: pytest.MonkeyPatch) -> _FakeAsyncOpenAI:
    """Patch ``openai.AsyncOpenAI`` (the module attribute the adapter's lazy
    ``from openai import AsyncOpenAI`` resolves at call time) and stash the
    single fake client instance created so the test can inspect it."""
    holder: dict[str, _FakeAsyncOpenAI] = {}

    def _make_client(*, api_key: str | None = None) -> _FakeAsyncOpenAI:
        client = _FakeAsyncOpenAI(api_key=api_key)
        holder["client"] = client
        return client

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _make_client)
    monkeypatch.setattr(
        "jarvis.plugins.realtime.openai_realtime.get_provider_secret",
        lambda _name: "sk-test",
    )
    return holder  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_open_session_uses_cfg_model_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = _patch_openai_client(monkeypatch)
    prov = OpenAIRealtimeProvider()

    await prov.open_session(RealtimeSessionConfig(model="gpt-realtime-2.1"))

    client = holder["client"]  # type: ignore[index]
    assert client.realtime.connect_calls == ["gpt-realtime-2.1"]


@pytest.mark.asyncio
async def test_open_session_falls_back_to_hardcoded_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    prov = OpenAIRealtimeProvider()

    # Empty model ("" -- nothing pinned) must fall back to the adapter's own
    # hardcoded _MODEL constant, not an empty string over the wire.
    await prov.open_session(RealtimeSessionConfig(model=""))

    client = holder["client"]  # type: ignore[index]
    assert client.realtime.connect_calls == ["gpt-realtime"]


@pytest.mark.asyncio
async def test_open_session_passes_voice_through_session_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    prov = OpenAIRealtimeProvider()

    await prov.open_session(RealtimeSessionConfig(voice="echo"))

    client = holder["client"]  # type: ignore[index]
    payload = client.realtime.last_conn.session_updates[0]
    assert payload["audio"]["output"]["voice"] == "echo"


@pytest.mark.asyncio
async def test_open_session_omits_voice_key_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    prov = OpenAIRealtimeProvider()

    await prov.open_session(RealtimeSessionConfig(voice=""))

    client = holder["client"]  # type: ignore[index]
    payload = client.realtime.last_conn.session_updates[0]
    assert "voice" not in payload["audio"]["output"]
