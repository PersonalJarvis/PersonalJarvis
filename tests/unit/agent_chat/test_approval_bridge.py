"""The chat's approval card answers the executor's ticket — with the REAL executor.

No mocks: a real ``EventBus``, ``ToolExecutor``, ``ApprovalWorkflow`` and
``RiskTierEvaluator``, the chat's folder tools, and the bridge in between.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jarvis.agent_chat import folder_tools as ft
from jarvis.agent_chat.approval_bridge import ChatApprovalBridge, ChatGrant, approval_ref
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionApprovalRequired, ActionDenied
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import APPROVAL_DENIED_PREFIX, ToolExecutor

SESSION = "sess-1"
REF = approval_ref(SESSION)


class _Card:
    """The chat's card, scripted: records what it was asked, answers what it is told."""

    def __init__(self, answer: str = "allow") -> None:
        self.answer = answer
        self.asked: list[tuple[str, str, dict[str, Any], str]] = []
        self.release = asyncio.Event()
        self.release.set()

    async def __call__(self, call_id: str, name: str, args: dict[str, Any], summary: str) -> str:
        self.asked.append((call_id, name, dict(args), summary))
        await self.release.wait()
        return self.answer


def _stack(
    *, timeout_s: float = 5.0, safety: SafetyConfig | None = None
) -> tuple[ToolExecutor, ChatApprovalBridge, EventBus]:
    bus = EventBus()
    executor = ToolExecutor(
        bus,
        RiskTierEvaluator(safety or SafetyConfig()),
        ApprovalWorkflow(bus, timeout_s=timeout_s),
        default_timeout_s=timeout_s,
    )
    return executor, ChatApprovalBridge(bus), bus


def _snapshot(timeout_s: float = 600.0) -> dict[str, Any]:
    return {
        "approval_surface": "interactive",
        "approval_ref": REF,
        "approval_timeout_s": timeout_s,
    }


def _grant(card: _Card, stance: str, always: set[str] | None = None) -> ChatGrant:
    return ChatGrant(
        session_id=SESSION,
        turn_id="turn-1",
        stance=stance,
        always_allowed=always if always is not None else set(),
        ask=card,
        call_id_for=lambda name: f"row-{name}",
    )


def _denials(bus: EventBus) -> list[ActionDenied]:
    seen: list[ActionDenied] = []

    async def _capture(event: ActionDenied) -> None:
        seen.append(event)

    bus.subscribe(ActionDenied, _capture)
    return seen


# ------------------------------------------------------------------ ask


async def test_ask_stance_runs_the_tool_only_after_the_card_allows(tmp_path: Path):
    executor, bridge, _ = _stack()
    card = _Card("allow")
    bridge.arm(REF, _grant(card, "ask"))
    write = ft.folder_tools(tmp_path)["Write"]

    result = await executor.execute(
        write, {"file_path": "a.txt", "content": "hi"}, config_snapshot=_snapshot()
    )

    assert result.success, result.error
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hi"
    assert card.asked == [("row-Write", "Write", {"file_path": "a.txt", "content": "hi"}, "a.txt")]


async def test_deny_on_the_card_yields_approval_denied(tmp_path: Path):
    executor, bridge, bus = _stack()
    denials = _denials(bus)
    card = _Card("deny")
    bridge.arm(REF, _grant(card, "ask"))
    write = ft.folder_tools(tmp_path)["Write"]

    result = await executor.execute(
        write, {"file_path": "a.txt", "content": "hi"}, config_snapshot=_snapshot()
    )

    assert not result.success
    assert result.error and result.error.startswith(APPROVAL_DENIED_PREFIX)
    assert not (tmp_path / "a.txt").exists()
    assert denials and denials[-1].reason == "declined by the person"


async def test_reads_never_ask(tmp_path: Path):
    (tmp_path / "r.txt").write_text("x", encoding="utf-8")
    executor, bridge, _ = _stack()
    card = _Card("deny")
    bridge.arm(REF, _grant(card, "ask"))
    read = ft.folder_tools(tmp_path)["Read"]
    result = await executor.execute(read, {"file_path": "r.txt"}, config_snapshot=_snapshot())
    assert result.success and card.asked == []


async def test_the_bridge_never_blocks_the_executors_publish(tmp_path: Path):
    """The card may take minutes; the executor's publish must return at once."""
    executor, bridge, _ = _stack()
    card = _Card("allow")
    card.release.clear()  # the person has not clicked yet
    bridge.arm(REF, _grant(card, "ask"))
    write = ft.folder_tools(tmp_path)["Write"]

    running = asyncio.create_task(
        executor.execute(write, {"file_path": "b.txt", "content": "x"}, config_snapshot=_snapshot())
    )
    for _ in range(20):
        await asyncio.sleep(0)
    assert card.asked, "the card is open while the executor waits on its ticket"
    assert not running.done()
    card.release.set()
    result = await asyncio.wait_for(running, timeout=5.0)
    assert result.success


# --------------------------------------------------------------- stances


async def test_accept_edits_waves_writes_through_but_cards_commands(tmp_path: Path):
    executor, bridge, _ = _stack()
    card = _Card("deny")
    bridge.arm(REF, _grant(card, "accept-edits"))
    tools = ft.folder_tools(tmp_path)

    written = await executor.execute(
        tools["Write"], {"file_path": "c.txt", "content": "x"}, config_snapshot=_snapshot()
    )
    assert written.success and card.asked == []

    ran = await executor.execute(
        tools["RunCommand"], {"command": "echo hi"}, config_snapshot=_snapshot()
    )
    assert not ran.success and ran.error and ran.error.startswith(APPROVAL_DENIED_PREFIX)
    assert [a[1] for a in card.asked] == ["RunCommand"]


async def test_bypass_asks_nothing(tmp_path: Path):
    executor, bridge, _ = _stack()
    card = _Card("deny")
    bridge.arm(REF, _grant(card, "bypass"))
    tools = ft.folder_tools(tmp_path)
    ran = await executor.execute(
        tools["RunCommand"], {"command": "echo hi"}, config_snapshot=_snapshot()
    )
    assert ran.success, ran.error
    assert card.asked == []


async def test_blacklist_wins_in_every_stance(tmp_path: Path):
    safety = SafetyConfig()
    safety.blacklist.commands = ["*forbidden-thing*"]  # fnmatch, as in jarvis.toml
    for stance in ("ask", "accept-edits", "bypass"):
        executor, bridge, _ = _stack(safety=safety)
        card = _Card("allow")
        bridge.arm(REF, _grant(card, stance))
        run = ft.folder_tools(tmp_path)["RunCommand"]
        result = await executor.execute(
            run, {"command": "echo forbidden-thing"}, config_snapshot=_snapshot()
        )
        assert not result.success, stance
        assert card.asked == [], f"{stance}: a blacklisted call must never reach a card"


async def test_always_allow_remembers_the_tool_for_the_session(tmp_path: Path):
    executor, bridge, _ = _stack()
    always: set[str] = set()
    card = _Card("allow_always")
    bridge.arm(REF, _grant(card, "ask", always))
    write = ft.folder_tools(tmp_path)["Write"]

    first = await executor.execute(
        write, {"file_path": "d.txt", "content": "1"}, config_snapshot=_snapshot()
    )
    second = await executor.execute(
        write, {"file_path": "e.txt", "content": "2"}, config_snapshot=_snapshot()
    )
    assert first.success and second.success
    assert always == {"Write"}
    assert len(card.asked) == 1, "the second write was waved through"


async def test_a_cli_approval_pre_answers_the_executor_gate(tmp_path: Path):
    executor, bridge, _ = _stack()
    card = _Card("deny")
    bridge.arm(REF, _grant(card, "ask"))
    bridge.note_cli_approval(REF, "mcp__jarvis__Write")
    write = ft.folder_tools(tmp_path)["Write"]
    result = await executor.execute(
        write, {"file_path": "f.txt", "content": "x"}, config_snapshot=_snapshot()
    )
    assert result.success and card.asked == []


async def test_another_sessions_calls_are_not_answered(tmp_path: Path):
    """A grant answers ITS session's calls only; an unarmed ref is left to the executor."""
    executor, bridge, _ = _stack(timeout_s=0.2)
    card = _Card("allow")
    bridge.arm(approval_ref("other"), _grant(card, "bypass"))
    write = ft.folder_tools(tmp_path)["Write"]
    result = await executor.execute(
        write, {"file_path": "g.txt", "content": "x"}, config_snapshot=_snapshot(timeout_s=0.2)
    )
    assert not result.success and card.asked == []


# --------------------------------------------------------------- executor


async def test_the_surface_declares_the_timeout_within_the_cap():
    executor, _, _ = _stack(timeout_s=5.0)
    assert executor._approval_timeout(None) == 5.0
    assert executor._approval_timeout({}) == 5.0
    assert executor._approval_timeout({"approval_timeout_s": 600}) == 600.0
    assert executor._approval_timeout({"approval_timeout_s": 2}) == 5.0, "never below the default"
    assert executor._approval_timeout({"approval_timeout_s": 5000}) == 900.0, "never above the cap"
    assert executor._approval_timeout({"approval_timeout_s": "nonsense"}) == 5.0


async def test_the_approval_event_carries_the_chat_ref(tmp_path: Path):
    executor, bridge, bus = _stack()
    seen: list[ActionApprovalRequired] = []

    async def _capture(event: ActionApprovalRequired) -> None:
        seen.append(event)

    bus.subscribe(ActionApprovalRequired, _capture)
    bridge.arm(REF, _grant(_Card("allow"), "ask"))
    write = ft.folder_tools(tmp_path)["Write"]
    await executor.execute(
        write, {"file_path": "h.txt", "content": "x"}, config_snapshot=_snapshot()
    )
    assert seen and seen[0].approval_ref == REF
    assert seen[0].expires_at_ns > 0
