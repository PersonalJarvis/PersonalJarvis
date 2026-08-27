"""Surface kits: one lookup per call site, and the local-models surface's recipe."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis.agent_chat import surface_kits
from jarvis.agent_chat.permissions import JARVIS_LADDER, ladder_key
from jarvis.agent_chat.runner_brain import build_override, kit_payload
from jarvis.agent_chat.service import resolve_runner
from jarvis.agent_chat.store import SURFACES, AgentChatSession
from jarvis.agent_chat.surface_kits import LOCAL_MODELS_SURFACE, kit_for
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.core.protocols import ExecutionContext, ToolResult


def _session(surface: str) -> AgentChatSession:
    return AgentChatSession(
        session_id="s1",
        title="",
        provider="openai",
        model="gpt-x",
        effort="",
        cwd=".",
        permission_mode="ask",
        vendor_session=None,
        created_ms=0,
        updated_ms=0,
        message_count=0,
        preview="",
        surface=surface,
    )


class _SearchWeb:
    name = "search_web"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.queries.append(str(args["query"]))
        return ToolResult(
            success=True,
            output={"results": [{"title": "t", "url": "u", "snippet": "s"}, "junk"]},
        )


class _Brain:
    def __init__(self, cfg: JarvisConfig, tools: dict[str, Any]) -> None:
        self._config = cfg
        self._tools = tools


def _cfg() -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.providers["ollama"] = BrainProviderConfig(model="qwen3.5:4b")
    return cfg


def test_every_surface_has_a_kit_and_the_table_matches_the_store() -> None:
    assert set(surface_kits._KITS) == set(SURFACES)
    assert kit_for("nope").surface == "agent"
    assert kit_for("jarvis").brain_runner and kit_for(LOCAL_MODELS_SURFACE).brain_runner
    assert not kit_for("agent").brain_runner


def test_the_four_call_sites_read_the_kit() -> None:
    assert resolve_runner("openai", surface=LOCAL_MODELS_SURFACE) == "brain"
    assert resolve_runner("openai", surface="agent") == "api"
    assert ladder_key(LOCAL_MODELS_SURFACE, "brain") == JARVIS_LADDER
    assert ladder_key("agent", "api") == "api"


def test_build_override_uses_the_kit_tools_and_briefing() -> None:
    session = _session(LOCAL_MODELS_SURFACE)
    tools = {"lm_inventory": object()}
    override = build_override(
        session,
        brain=None,
        stance="ask",
        cwd=Path("."),
        ref="r",
        kit_tools=tools,  # type: ignore[arg-type]
        system_extra="BRIEFING",
    )
    assert set(override.tools_extra) == {"lm_inventory"}
    assert override.system_extra == "BRIEFING"
    assert override.tool_context["tool_origin"] == "local-models-assistant"
    assert override.tool_context["approval_surface"] == "interactive"
    assert override.max_turns == 40

    plain = build_override(_session("jarvis"), brain=None, stance="ask", cwd=Path("."), ref="r")
    assert "Read" in plain.tools_extra and plain.system_extra == ""
    assert plain.tool_context["tool_origin"] == "agent-chat"


async def test_kit_payload_builds_lm_tools_and_the_briefing() -> None:
    search = _SearchWeb()
    brain = _Brain(_cfg(), {"search_web": search})
    tools, extra = await kit_payload(_session(LOCAL_MODELS_SURFACE), brain)
    assert tools is not None and "lm_test_plan" in tools and "lm_pull" in tools
    assert all(name.startswith("lm_") for name in tools)
    assert "LOCAL MODELS SETUP ASSISTANT" in extra
    assert "```jarvis-proposal" in extra

    none_tools, none_extra = await kit_payload(_session("jarvis"), brain)
    assert none_tools is None and none_extra == ""


async def test_search_fn_wraps_the_brains_search_web_tool() -> None:
    search = _SearchWeb()
    fn = surface_kits.local_models_search_fn(_Brain(_cfg(), {"search_web": search}))
    assert fn is not None
    rows = await fn("qwen3.5 LMArena")
    assert rows == [{"title": "t", "url": "u", "snippet": "s"}]
    assert search.queries == ["qwen3.5 LMArena"]
    assert surface_kits.local_models_search_fn(_Brain(_cfg(), {})) is None


def test_local_models_surface_drives_cli_seats_through_the_brain() -> None:
    """A coding agent's loop has none of the lm_* tools, so a seat whose
    provider also ships a brain plugin runs through Jarvis' brain here."""
    from jarvis.agent_chat.service import resolve_runner

    assert resolve_runner("antigravity", surface="local-models") == "brain"
    assert resolve_runner("gemini", surface="local-models") == "brain"
    # The Jarvis surface keeps the vendor CLI for a subscription seat.
    assert resolve_runner("antigravity", surface="jarvis") != "brain"


def test_local_models_surface_keeps_only_its_own_hands_and_web_search() -> None:
    """The router's 100+ declarations (some with schemas Gemini refuses) never
    reach this surface: only lm_* and web search survive the kit filter."""
    from jarvis.agent_chat.surface_kits import kit_for

    class _T:
        def __init__(self, name: str) -> None:
            self.name = name

    tools = {n: _T(n) for n in ("lm_hardware", "search_web", "patch", "spawn-worker", "lm_pull")}
    kept = kit_for("local-models").tool_filter(tools)  # type: ignore[misc]
    assert sorted(kept) == ["lm_hardware", "lm_pull", "search_web"]
    assert kit_for("jarvis").tool_filter is None
