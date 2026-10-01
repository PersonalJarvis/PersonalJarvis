"""A coding-agent hand-off the user asked for runs without a second question.

Live voice session 2026-10-01 18:11: the user asked Jarvis to hand the voice-bar
flicker to an agent in the workspace, Jarvis asked "soll ich ihn starten?",
then asked again for the follow-up agent the user requested by name — and the
user's "Ja, aber das hättest du einfach loslaufen lassen können" was read as a
veto, so the second agent never started. The workspace send now confirms only
when the user's own turn did not ask for agent work.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from jarvis.brain.workspace_tool import WorkspaceOrchestrationTool
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.explicit_intent import utterance_requests_agent_work
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL, ToolExecutor

_SEND = {
    "action": "send",
    "project_id": "p",
    "workspace_id": "w",
    "terminal_id": "pane:1",
    "request_id": "0" * 32,
    "prompt": "Smooth out the voice bar indicators.",
}


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, args: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
        self.calls.append(dict(args))
        return {"status": "live", "success": True}


class _Approval(ApprovalWorkflow):
    async def wait(self, trace_id: UUID, timeout_s: float) -> tuple[bool, str]:  # type: ignore[override]
        raise AssertionError("a conversational turn never blocks on the UI approval")


def _executor() -> ToolExecutor:
    bus = EventBus()
    return ToolExecutor(
        bus=bus,
        evaluator=RiskTierEvaluator(SafetyConfig()),
        approval=_Approval(bus),
    )


@pytest.mark.parametrize(
    "utterance",
    [
        # The two requests from the live session, verbatim.
        "Hmm, kannst du bitte mal nen Appshot machen und analysieren, da unten bei "
        "diesen Indikatoren mit Jarvis spricht, ähm, geh einfach in Jarvis Workspace "
        "rein ähm meinem Personal Jarvis Ordner. Und zwar siehst du das, dieses "
        "Flackern. Ich möchte, dass du nen Agenten da beauftragst, dass es smoother "
        "gemacht wird",
        ". Und der soll dann bitte darauf schauen, dass ähm... Du, wenn man ähm wenn "
        "man mit dir spricht, dass man dich zum Beispiel nicht wie beim vorigen "
        "Beispiel, dass du dann erstmal fragst, ob das freigegeben werden muss. Das "
        "kannst du automatisch machen. Da sollte ich nen anderer Agent drum kümmern, "
        "im selben Workspace",
        "Öffne ein neues Terminal mit einer Claude Code Session",
        "Sag T2, er soll die Tests reparieren",
        "Spawn a Codex agent that profiles the voice path",
        "have the coding session fix the flicker",
        "pídele al agente que revise el código",
        "Nicht so, lass das einen Agenten machen",
    ],
)
def test_agent_work_requests_are_recognized(utterance: str) -> None:
    assert utterance_requests_agent_work(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "",
        "   ",
        "Erzähl mir einen Witz",
        "Was steht heute in meinem Kalender?",
        "Schick das nicht an den Agenten",
        "Kein Terminal bitte, mach es selbst",
        "don't send it to the agent",
        "what's the weather in Berlin",
    ],
)
def test_other_turns_are_not_agent_work(utterance: str) -> None:
    assert utterance_requests_agent_work(utterance) is False


def test_hook_only_waives_the_send_action() -> None:
    tool = WorkspaceOrchestrationTool(_Gateway())
    assert tool.intent_confirms_args(_SEND, "lass das einen Agenten machen") is True
    assert tool.intent_confirms_args(
        {**_SEND, "action": "inspect"}, "lass das einen Agenten machen"
    ) is False
    assert tool.intent_confirms_args(_SEND, "erzähl mir einen Witz") is False


@pytest.mark.asyncio
async def test_requested_hand_off_runs_without_confirmation() -> None:
    gateway = _Gateway()
    result = await _executor().execute(
        WorkspaceOrchestrationTool(gateway),
        dict(_SEND),
        user_utterance="Beauftrag einen Agenten im Workspace, das zu glätten",
        config_snapshot={"voice_confirm": True},
    )
    assert result.success is True
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_unrequested_hand_off_still_confirms() -> None:
    gateway = _Gateway()
    result = await _executor().execute(
        WorkspaceOrchestrationTool(gateway),
        dict(_SEND),
        user_utterance="Wie spät ist es?",
        config_snapshot={"voice_confirm": True},
    )
    assert result.error == VOICE_CONFIRM_SENTINEL
    assert gateway.calls == []
