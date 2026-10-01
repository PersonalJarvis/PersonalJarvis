"""The society surface: per-session hands, the grant filter and the briefing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_chat.approval_bridge import ChatApprovalBridge, ChatGrant, approval_ref
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.protocols import ToolResult
from jarvis.core.response_style import KEEP_GOING_ON_TOOL_FAILURE
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor
from jarvis.society import runtime as runtime_mod
from jarvis.society.agent_tools import (
    MEMORY_RECALL_TOOL_NAME,
    MESSAGE_TOOL_NAME,
    PROPOSE_TOOL_NAME,
    SHELL_TOOL_NAME,
    WIKI_NOTE_TOOL_NAME,
)
from jarvis.society.ask_tool import ASK_USER_TOOL_NAME
from jarvis.society.learning import RUN_SKILL_TOOL_NAME
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.shell import ShellResult
from jarvis.society.surface import (
    agent_id_of,
    build_briefing,
    capability_epoch,
    society_system_extra,
    society_tool_filter,
    society_tools,
)

FOLDER = ["Read", "Write", "Edit", "Ls", "Glob", "Grep", RUN_SKILL_TOOL_NAME]


def _tool(name: str, desc: str = "x.") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier="monitor", schema={})


TOOLS = {
    "gmail": _tool("gmail", "Read and send mail."),
    "search-web": _tool("search-web", "Search the web."),
    "wiki-recall": _tool("wiki-recall", "Search the wiki."),
    "wiki-ingest": _tool("wiki-ingest", "Write the wiki."),
    "spawn-worker": _tool("spawn-worker", "Spawn."),
    "cli_gh": _tool("cli_gh", "GitHub."),
}


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False, brain_tools=lambda: TOOLS)
    await runtime.ensure_started()
    try:
        yield runtime
    finally:
        await runtime.close()


def test_agent_id_of():
    assert agent_id_of("society:scout") == "scout"
    assert agent_id_of("society:") is None
    assert agent_id_of("abc123") is None


def test_without_runtime_everything_is_empty():
    runtime_mod.set_current_runtime(None)  # another test's runtime may still be registered
    assert runtime_mod.current_runtime() is None
    session = SimpleNamespace(session_id="society:scout")
    assert society_tools(None, None, session) == {}
    assert society_tool_filter(session) is None


async def test_tools_and_filter_follow_the_roster_row(rt: SocietyRuntime, tmp_path: Path):
    await rt.roster.create(
        name="Mailbox",
        title="Gmail agent",
        description="Handle my mail.",
        focus=["plugin:gmail"],
        denies=["cli:gh"],
    )
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    session = SimpleNamespace(session_id="society:mailbox")
    own = society_tools(cfg, None, session)
    assert set(own) == {
        "coding-session",
        MESSAGE_TOOL_NAME,
        WIKI_NOTE_TOOL_NAME,
        SHELL_TOOL_NAME,
        MEMORY_RECALL_TOOL_NAME,
        PROPOSE_TOOL_NAME,
        "society_conversation_recall",
        "society_routines",
        "society_invoke_routine",
        "society_ask_user",
        *FOLDER,
    }
    assert "RunCommand" not in own

    # The briefing fills the cache the sync filter reads.
    extra = await society_system_extra(cfg, None, session)
    assert "## You are Mailbox — Gmail agent" in extra
    merged = {**TOOLS, **own}
    filt = society_tool_filter(session)
    assert filt is not None
    picked = list(filt(merged))
    own_names = {
        MESSAGE_TOOL_NAME,
        WIKI_NOTE_TOOL_NAME,
        SHELL_TOOL_NAME,
        MEMORY_RECALL_TOOL_NAME,
        PROPOSE_TOOL_NAME,
        "society_conversation_recall",
        "society_routines",
        "society_invoke_routine",
        "society_ask_user",
        *FOLDER,
    }
    assert set(picked[: len(own_names)]) == own_names
    assert picked[len(own_names)] == "gmail"  # focus first
    assert "spawn-worker" not in picked and "cli_gh" not in picked
    assert "wiki-ingest" not in picked  # writes go through the namespaced note tool
    assert "wiki-recall" in picked
    assert own[WIKI_NOTE_TOOL_NAME].schema["properties"]["kind"]["enum"] == ["note", "memory"]
    assert "shared to propose" not in extra
    assert "task explicitly calls for the user's wiki" in extra


async def test_allowlist_mode_keeps_only_grants(rt: SocietyRuntime, tmp_path: Path):
    await rt.roster.create(
        name="Narrow", grant_mode="allowlist", grants=["core:search-web"], focus=[]
    )
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    session = SimpleNamespace(session_id="society:narrow")
    await society_system_extra(cfg, None, session)
    filt = society_tool_filter(session)
    assert filt is not None
    picked = list(filt({**TOOLS, **society_tools(cfg, None, session)}))
    assert picked[-1] == "search-web"
    assert {MESSAGE_TOOL_NAME, WIKI_NOTE_TOOL_NAME, SHELL_TOOL_NAME, *FOLDER} <= set(picked)
    assert "gmail" not in picked and "cli_gh" not in picked


async def test_unknown_agent_gets_no_briefing(rt: SocietyRuntime):
    session = SimpleNamespace(session_id="society:ghost")
    assert await society_system_extra(None, None, session) == ""


async def test_briefing_is_deterministic_and_complete(rt: SocietyRuntime):
    await rt.roster.create(name="Scout", title="Research scout", tier="orchestrator")
    mailbox, _ = await rt.roster.create(
        name="Mailbox",
        title="Gmail agent",
        description="Read and answer mail; external mail only after approval.",
        focus=["plugin:gmail"],
    )
    catalog = rt.catalog()
    roster = await rt.roster.list()
    a = build_briefing(mailbox, catalog, roster)
    b = build_briefing(mailbox, catalog, roster)
    assert a == b
    assert "## Standing instructions" in a and "external mail only after approval" in a
    assert "Reach for these first:\n- gmail (plugin:gmail): Read and send mail." in a
    assert "Also available" in a
    assert all(
        name in a
        for name in ("browser", "coding-session", "search-web", "wiki-ingest", "wiki-recall")
    )
    assert "spawn-worker" not in a
    assert f"Capability epoch: {capability_epoch(catalog)}" in a
    assert "## When a tool fails" in a and KEEP_GOING_ON_TOOL_FAILURE in a
    assert a.index("## When a tool fails") < a.index("## Standing instructions")
    assert "## The Jarvis ecosystem" in a and "society_message_agent" in a
    assert "## Teammates\n- Jarvis — Lead (lead)\n- Scout — Research scout (orchestrator)" in a
    assert "Mailbox" not in a.split("## Teammates")[1]
    assert "You do not assign work" in a


async def test_capability_epoch_changes_when_hands_change(rt: SocietyRuntime):
    before = capability_epoch(rt.catalog())
    rt._get_tools = lambda: {**TOOLS, "spotify": _tool("spotify")}  # noqa: SLF001 — test seam
    assert capability_epoch(rt.catalog()) != before


async def test_a_granted_tool_is_gated_by_the_agents_rules(rt: SocietyRuntime, tmp_path: Path):
    """Every granted hand obeys the roster row's approval rules and ceiling
    through the executor's per-call tier hook (agent-definition §3.4)."""
    await rt.roster.create(
        name="Mailbox",
        title="Gmail agent",
        description="Handle my mail.",
        focus=["plugin:gmail"],
        permission_ceiling="monitor",
        approval_rules={"require_approval": ["plugin:gmail:send"], "always_allow": []},
    )
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    session = SimpleNamespace(session_id="society:mailbox")
    await society_system_extra(cfg, None, session)
    filt = society_tool_filter(session)
    assert filt is not None
    picked = filt({**TOOLS, **society_tools(cfg, None, session)})
    gmail = picked["gmail"]
    assert gmail.name == "gmail" and gmail.schema == TOOLS["gmail"].schema
    assert gmail.risk_tier_for_args({"action": "list"}) in (None, "monitor")
    assert gmail.risk_tier_for_args({"action": "send"}) == "ask"  # require_approval
    # Under an explicit approval mode even the own hands ride the gate, so
    # Always ask can card a read; asking the user is never gated twice.
    assert hasattr(picked[SHELL_TOOL_NAME], "_capability_id")
    if ASK_USER_TOOL_NAME in picked:
        assert not hasattr(picked[ASK_USER_TOOL_NAME], "_capability_id")
    await rt.roster.update("mailbox", {"approval_mode": "always_ask"})
    await society_system_extra(cfg, None, session)
    careful = society_tool_filter(session)({**TOOLS, **society_tools(cfg, None, session)})  # type: ignore[misc]
    assert careful["gmail"].risk_tier_for_args({"action": "list"}) == "ask"
    assert careful[SHELL_TOOL_NAME].risk_tier_for_args({}) == "ask"
    # A pre-migration row (approval_mode NULL) keeps its own hands unwrapped:
    # the society tools gate themselves, as before the migration.
    await rt.store.update_agent("mailbox", {"approval_mode": None})
    await society_system_extra(cfg, None, session)
    legacy = society_tool_filter(session)({**TOOLS, **society_tools(cfg, None, session)})  # type: ignore[misc]
    assert not hasattr(legacy[SHELL_TOOL_NAME], "_capability_id")
    assert legacy["gmail"].risk_tier_for_args({"action": "send"}) == "ask"  # require_approval
    await rt.roster.update("mailbox", {"approval_mode": "bypass"})
    # An always-allow rule is the person's standing yes: an ask-tier call runs.
    await rt.roster.update(
        "mailbox",
        {"approval_rules": {"require_approval": [], "always_allow": ["plugin:gmail:send"]}},
    )
    await society_system_extra(cfg, None, session)
    gmail = society_tool_filter(session)({**TOOLS})["gmail"]  # type: ignore[misc]
    gmail.risk_tier = "ask"
    assert gmail.risk_tier_for_args({"action": "send"}) == "monitor"


async def test_bound_chat_always_ask_cards_a_safe_read(rt: SocietyRuntime, tmp_path: Path):
    await rt.roster.create(name="Reader", approval_mode="bypass", permission_ceiling="safe")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("hello", encoding="utf-8")
    session = SimpleNamespace(
        session_id="society:reader", permission_mode="always_ask", cwd=str(workspace)
    )
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    await society_system_extra(cfg, None, session)
    filt = society_tool_filter(session)
    assert filt is not None
    read = filt(society_tools(cfg, None, session))["Read"]
    assert read.risk_tier == "safe"
    assert read.risk_tier_for_args({"file_path": "note.txt"}) == "ask"
    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus))
    bridge = ChatApprovalBridge(bus)
    asked = asyncio.Event()
    release = asyncio.Event()
    seen: list[str] = []

    async def card(_call_id: str, name: str, _args: dict, _summary: str) -> str:
        seen.append(name)
        asked.set()
        await release.wait()
        return "allow"

    ref = approval_ref(session.session_id)
    bridge.arm(
        ref,
        ChatGrant(
            session_id=session.session_id,
            turn_id="turn-1",
            stance="always_ask",
            always_allowed=set(),
            ask=card,
        ),
    )
    running = asyncio.create_task(
        executor.execute(
            read,
            {"file_path": "note.txt"},
            config_snapshot={
                "approval_surface": "interactive",
                "approval_ref": ref,
                "approval_timeout_s": 5,
            },
        )
    )
    try:
        await asyncio.wait_for(asked.wait(), timeout=5)
        assert seen == ["Read"]
        assert not running.done(), "the tool waits for the person's card"
    finally:
        release.set()
    result = await asyncio.wait_for(running, timeout=5)
    assert result.success, result.error


async def test_bound_chat_ask_gates_monitor_without_widening_roster(rt: SocietyRuntime):
    await rt.roster.create(name="Mailbox", approval_mode="bypass", permission_ceiling="monitor")
    for mode, expected in (("bypass", "monitor"), ("ask", "ask"), ("always_ask", "ask")):
        session = SimpleNamespace(session_id="society:mailbox", permission_mode=mode)
        await society_system_extra(None, None, session)
        filt = society_tool_filter(session)
        assert filt is not None
        assert filt(TOOLS)["gmail"].risk_tier_for_args({"action": "list"}) == expected

    await rt.roster.update("mailbox", {"approval_mode": "ask"})
    session = SimpleNamespace(session_id="society:mailbox", permission_mode="bypass")
    await society_system_extra(None, None, session)
    filt = society_tool_filter(session)
    assert filt is not None
    assert filt(TOOLS)["gmail"].risk_tier_for_args({"action": "list"}) == "ask"


async def test_in_flight_tool_stops_after_kill_or_agent_pause(rt: SocietyRuntime):
    await rt.roster.create(name="Mailer", approval_mode="bypass")
    session = SimpleNamespace(session_id="society:mailer", permission_mode="bypass")
    await society_system_extra(None, None, session)
    calls: list[dict] = []

    async def execute(args: dict, _ctx: object) -> ToolResult:
        calls.append(args)
        return ToolResult(True, "ok", None)

    tool = _tool("gmail")
    tool.execute = execute
    filt = society_tool_filter(session)
    assert filt is not None
    gated = filt({"gmail": tool})["gmail"]
    ctx = SimpleNamespace(approved_by="auto")
    assert (await gated.execute({"action": "list"}, ctx)).success
    await rt.store.set_kill_switch(True)
    killed = await gated.execute({"action": "list"}, ctx)
    assert not killed.success and killed.output["reason"] == "kill_switch"
    await rt.store.set_kill_switch(False)
    await rt.roster.update("mailer", {"state": "paused"})
    paused = await gated.execute({"action": "list"}, ctx)
    assert not paused.success and paused.output["reason"] == "blocked_by_policy"
    assert calls == [{"action": "list"}]


async def test_shell_uses_one_chat_approval_for_one_command(rt: SocietyRuntime, tmp_path: Path):
    await rt.roster.create(name="Runner", approval_mode="ask")
    workspace = tmp_path / "workspace"
    session = SimpleNamespace(
        session_id="society:runner", permission_mode="ask", cwd=str(workspace)
    )
    await society_system_extra(None, None, session)
    from jarvis.society.agent_tools import ShellTool

    class Backend:
        name = "fake"

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ShellResult:
            self.calls.append(command)
            return ShellResult(output="ok", exit_code=0, seconds=0.01)

    backend = Backend()
    shell = ShellTool(rt, "runner", workspace=workspace, backend=backend)
    filt = society_tool_filter(session)
    assert filt is not None
    gated = filt({SHELL_TOOL_NAME: shell})[SHELL_TOOL_NAME]
    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus))
    bridge = ChatApprovalBridge(bus)
    cards: list[str] = []

    async def card(_call_id: str, name: str, _args: dict, _summary: str) -> str:
        cards.append(name)
        return "allow"

    ref = approval_ref(session.session_id)
    bridge.arm(
        ref,
        ChatGrant(
            session_id=session.session_id,
            turn_id="turn-1",
            stance="ask",
            always_allowed=set(),
            ask=card,
        ),
    )
    result = await executor.execute(
        gated,
        {"command": "echo hi"},
        config_snapshot={"approval_surface": "interactive", "approval_ref": ref},
    )
    assert result.success, result.error
    assert cards == [SHELL_TOOL_NAME]
    assert backend.calls == ["echo hi"]
    assert await rt.approvals.pending() == []


async def test_always_allow_writes_the_agents_own_rule(rt: SocietyRuntime):
    from jarvis.society.surface import remember_always_allow

    await rt.roster.create(name="Mailbox", title="Gmail agent", focus=["plugin:gmail"])
    session = SimpleNamespace(session_id="society:mailbox")
    assert await remember_always_allow(session, "gmail", {"action": "send"}) is True
    agent = await rt.roster.get("mailbox")
    assert agent is not None and agent.approval_rules["always_allow"] == ["plugin:gmail:send"]
    # Idempotent, and a tool without a capability id (an own hand) is not remembered.
    assert await remember_always_allow(session, "gmail", {"action": "send"}) is True
    assert (await rt.roster.get("mailbox")).approval_rules["always_allow"] == ["plugin:gmail:send"]
    assert await remember_always_allow(session, "spawn-worker", {}) is False
    assert await remember_always_allow(SimpleNamespace(session_id="agent:x"), "gmail", {}) is False


async def test_a_cache_miss_keeps_the_agents_own_reporting_tools(
    rt: SocietyRuntime, tmp_path: Path
):
    """#255: a turn without the briefing (a CLI/MCP turn, a runtime restart)
    finds no cached record. Its mode and grants are unknown, so no granted
    hand and no write — but stripping its own safe tools left it unable to
    report back or ask the user, and the job stalled silently."""
    await rt.roster.create(name="Courier", title="Runner", description="Run errands.")
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    session = SimpleNamespace(session_id="society:courier")
    own = society_tools(cfg, None, session)
    assert rt.cached_agent("courier") is None, "no briefing ran"

    filt = society_tool_filter(session)
    assert filt is not None
    picked = set(filt({**TOOLS, **own}))

    assert {MESSAGE_TOOL_NAME, "society_ask_user", MEMORY_RECALL_TOOL_NAME} <= picked
    assert SHELL_TOOL_NAME not in picked and WIKI_NOTE_TOOL_NAME not in picked, "no writes"
    assert not picked & set(TOOLS), "no granted hands while the grants are unknown"
