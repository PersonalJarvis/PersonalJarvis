"""Unit tests for the CodexBrain plugin (lost-module rebuild).

CodexBrain is a thin OpenAI-chat brain that authenticates with the **Codex**
API-key slot (``codex_openai_api_key``), falling back to the general OpenAI key.
This makes "Codex" selectable as an independent brain provider, separate from the
plain ``openai`` provider, so the user can run e.g. brain=codex + subagent=gemini.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain.codex import CodexBrain


def test_name_and_capabilities() -> None:
    brain = CodexBrain()
    assert brain.name == "codex"
    assert brain.supports_tools is True


@pytest.mark.asyncio
async def test_complete_raises_without_any_key_or_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.core.config.get_provider_secret", lambda _p: None)
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *_a, **_k: None)
    monkeypatch.setattr("jarvis.plugins.brain.codex._codex_oauth_connected", lambda: False)
    brain = CodexBrain()
    req = BrainRequest(messages=(BrainMessage(role="user", content="hello"),))

    with pytest.raises(RuntimeError, match="No Codex auth found"):
        async for _delta in brain.complete(req):
            pass


def test_ensure_client_uses_given_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class _FakeAsyncOpenAI:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    brain = CodexBrain()
    brain._ensure_client("sk-codex-key")
    assert captured["api_key"] == "sk-codex-key"


class _FakeStdin:
    def __init__(self) -> None:
        self.data = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.returncode: int | None = None
        self.communicate_started = asyncio.Event()
        self._release = asyncio.Event()
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicate_started.set()
        await self._release.wait()
        return b"", b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._release.set()

    async def wait(self) -> int | None:
        self.waited = True
        return self.returncode


@pytest.mark.asyncio
async def test_cli_subprocess_is_killed_when_stream_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proc = _FakeProcess()

    async def fake_create_subprocess_exec(*_args, **_kwargs) -> _FakeProcess:
        return proc

    monkeypatch.setattr(
        "jarvis.plugins.brain.codex._resolve_codex_binary",
        lambda: "codex",
    )
    monkeypatch.setattr(
        "jarvis.plugins.brain.codex.tempfile.mkdtemp",
        lambda prefix: str(tmp_path),
    )
    monkeypatch.setattr(
        "jarvis.plugins.brain.codex.shutil.rmtree",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "jarvis.plugins.brain.codex.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    brain = CodexBrain()
    req = BrainRequest(messages=(BrainMessage(role="user", content="hello"),))
    stream: AsyncIterator[BrainDelta] = brain._complete_via_cli(req)
    task = asyncio.create_task(stream.__anext__())

    await asyncio.wait_for(proc.communicate_started.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed is True
    assert proc.waited is True
