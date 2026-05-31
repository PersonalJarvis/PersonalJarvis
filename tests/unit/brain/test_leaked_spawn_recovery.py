"""Robustness net for provider function-calling leaks (2026-05-24).

Some providers (notably Gemini) intermittently emit a ``spawn_worker``
tool_use block as the response *text* instead of executing it. Without the
recovery path the raw JSON reaches TTS as "Es trat ein Fehler auf" and the
delegated Opus-4.7 sub-agent never runs — even though the brain decided to
delegate. These tests pin the detector + the recovery execution.

Live repro: voice mission "erstelle mir eine Datei test-opus.md …" on
2026-05-24 produced ``[{"type":"tool_use","name":"spawn_worker",…}]`` as the
spoken reply and created no mission.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.brain.manager import BrainManager, _extract_leaked_spawn_call
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


def test_extract_detects_leaked_spawn_variants() -> None:
    # Exact shape observed in the live log (list of tool_use blocks).
    assert _extract_leaked_spawn_call(
        '[{"type": "tool_use", "id": "gemini_49a94f6c", '
        '"name": "spawn_worker", "input": {"utterance": "x"}}]'
    ) == {"utterance": "x"}
    # Bare object, no input.
    assert _extract_leaked_spawn_call(
        '{"type":"tool_use","name":"spawn_worker","input":{}}'
    ) == {}
    # Markdown-fenced.
    assert _extract_leaked_spawn_call(
        '```json\n[{"type":"tool_use","name":"spawn_worker",'
        '"input":{"action":"y"}}]\n```'
    ) == {"action": "y"}
    # Prose-wrapped.
    assert _extract_leaked_spawn_call(
        'Klar! {"type":"tool_use","name":"spawn_worker",'
        '"input":{"target":"z"}} erledige ich.'
    ) == {"target": "z"}


def test_extract_ignores_normal_text_and_other_tools() -> None:
    assert _extract_leaked_spawn_call("Mach ich, im Hintergrund.") is None
    assert _extract_leaked_spawn_call("") is None
    # A different tool leak must NOT be treated as a spawn.
    assert _extract_leaked_spawn_call(
        '{"type":"tool_use","name":"wiki-recall","input":{}}'
    ) is None


def _manager_with_fake_spawn(captured: dict) -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"

    class _FakeExecutor:
        async def execute(self, tool, args, *, user_utterance, trace_id):  # type: ignore[no-untyped-def]
            captured["args"] = args
            captured["tool"] = tool
            captured["user_utterance"] = user_utterance
            return SimpleNamespace(
                success=True,
                output="Mach ich, ich kümmere mich im Hintergrund darum.",
                error=None,
            )

    fake_tool = SimpleNamespace(name="spawn_worker")
    return BrainManager(
        config=cfg,
        bus=EventBus(),
        tools={"spawn_worker": fake_tool},
        tool_executor=_FakeExecutor(),
    )


@pytest.mark.asyncio
async def test_recover_executes_leaked_spawn_and_returns_ack() -> None:
    captured: dict = {}
    mgr = _manager_with_fake_spawn(captured)
    leaked = (
        '[{"type":"tool_use","name":"spawn_worker",'
        '"input":{"utterance":"erstelle mir eine Datei test-opus.md"}}]'
    )
    out = await mgr._recover_leaked_spawn(
        leaked,
        user_text="erstelle mir eine Datei test-opus.md",
        trace_id=uuid4(),
    )
    assert out == "Mach ich, ich kümmere mich im Hintergrund darum."
    # The leaked utterance is forwarded to the spawn tool.
    assert captured["args"]["utterance"] == "erstelle mir eine Datei test-opus.md"
    assert captured["tool"].name == "spawn_worker"


@pytest.mark.asyncio
async def test_recover_returns_none_for_normal_response() -> None:
    captured: dict = {}
    mgr = _manager_with_fake_spawn(captured)
    out = await mgr._recover_leaked_spawn(
        "Alles erledigt, ich habe das im Hintergrund übernommen.",
        user_text="erstelle mir eine Datei",
        trace_id=uuid4(),
    )
    assert out is None
    assert "args" not in captured  # executor never called
