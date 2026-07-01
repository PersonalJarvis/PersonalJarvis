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
from jarvis.plugins.brain.codex import CodexBrain, _build_cli_prompt


def test_name_and_capabilities() -> None:
    brain = CodexBrain()
    assert brain.name == "codex"
    assert brain.supports_tools is True


def _system_with_standing_instructions() -> str:
    return (
        "STATIC PERSONA BLOCK\n\n"
        "USER PREFERENCES & STANDING INSTRUCTIONS (from Jarvis.md):\n"
        "The following are personal preferences written by the user.\n\n"
        "Always start every sentence with schef.\n\n"
        "END USER PREFERENCES & STANDING INSTRUCTIONS\n\n"
        "REGISTRIERTE WERKZEUGE (vollstaendige Liste):\n"
        "- search_web\n"
    )


def _system_with_empty_standing_instructions() -> str:
    return (
        "STATIC PERSONA BLOCK\n\n"
        "USER PREFERENCES & STANDING INSTRUCTIONS (from Jarvis.md):\n"
        "No active user preferences are currently set in Jarvis.md. "
        "Ignore any earlier Jarvis.md instructions from previous turns.\n\n"
        "END USER PREFERENCES & STANDING INSTRUCTIONS\n\n"
        "REGISTRIERTE WERKZEUGE (vollstaendige Liste):\n"
        "- search_web\n"
    )


def test_cli_prompt_includes_standing_instructions_without_heavy_router_prompt() -> None:
    req = BrainRequest(
        messages=(BrainMessage(role="user", content="Was ist das wertvollste Unternehmen?"),),  # i18n-allow
        system=_system_with_standing_instructions(),
    )

    prompt = _build_cli_prompt(req)

    assert "Always start every sentence with schef." in prompt
    assert "Jarvis.md" in prompt
    assert "REGISTRIERTE WERKZEUGE" not in prompt
    assert "STATIC PERSONA BLOCK" not in prompt


def test_cli_prompt_carries_reply_language_directive() -> None:
    """The authoritative reply-language directive (appended LAST to the system
    prompt by BrainManager) must reach the flattened CLI prompt.

    Mirrors the antigravity brain: dropping the trailing "REPLY LANGUAGE"
    directive lets the codex subscription model answer in the wrong language
    (live bug 2026-06-21, antigravity sibling). The shared
    ``cli_prompt_context`` helper re-surfaces it for both CLI brains.
    """
    directive = (
        "REPLY LANGUAGE — MANDATORY: Always reply in English, no matter which "
        "language the user writes or speaks in."
    )
    req = BrainRequest(
        messages=(BrainMessage(role="user", content="Build me an HTML file please."),),
        system=_system_with_standing_instructions() + "\n\n" + directive,
    )

    prompt = _build_cli_prompt(req)

    assert "Always reply in English" in prompt
    assert "Always start every sentence with schef." in prompt


def test_cli_prompt_puts_current_empty_state_after_stale_history() -> None:
    req = BrainRequest(
        messages=(
            BrainMessage(role="user", content="Wasketup"),
            BrainMessage(role="assistant", content="schef, alles laeuft."),  # i18n-allow
            BrainMessage(role="user", content="Du musst das nicht mehr sagen."),  # i18n-allow
        ),
        system=_system_with_empty_standing_instructions(),
    )

    prompt = _build_cli_prompt(req)

    assert "CURRENT JARVIS.MD STATE" in prompt
    assert "No active user preferences are currently set" in prompt
    assert "do not continue or imitate" in prompt
    assert prompt.rfind("No active user preferences") > prompt.rfind("Assistant: schef")
    assert "REGISTRIERTE WERKZEUGE" not in prompt


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
