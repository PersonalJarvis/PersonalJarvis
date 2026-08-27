"""Surface kits: one lookup per call site, and what a kit hands a turn."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.agent_chat import surface_kits
from jarvis.agent_chat.permissions import JARVIS_LADDER, ladder_key
from jarvis.agent_chat.runner_brain import build_override, kit_payload
from jarvis.agent_chat.service import resolve_runner
from jarvis.agent_chat.store import SURFACES, AgentChatSession
from jarvis.agent_chat.surface_kits import SurfaceKit, kit_for
from jarvis.core.protocols import Tool


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


class _Brain:
    def __init__(self) -> None:
        self._config = object()


def _probe_kit(monkeypatch: pytest.MonkeyPatch) -> SurfaceKit:
    """A throwaway surface with its own hands and briefing, registered for one test."""

    def _tools(cfg: Any, brain: Any) -> dict[str, Tool]:
        return {"probe_read": object()}  # type: ignore[dict-item]

    async def _extra(cfg: Any, brain: Any) -> str:
        return "BRIEFING"

    kit = SurfaceKit(
        surface="probe",
        brain_runner=True,
        ladder=JARVIS_LADDER,
        uses_stance=True,
        tools=_tools,
        system_extra=_extra,
        tool_origin="probe-origin",
        tool_filter=lambda tools: {n: t for n, t in tools.items() if n.startswith("probe_")},
        max_turns=7,
    )
    monkeypatch.setitem(surface_kits._KITS, "probe", kit)
    return kit


def test_every_surface_has_a_kit_and_the_table_matches_the_store() -> None:
    assert set(surface_kits._KITS) == set(SURFACES)
    assert kit_for("nope").surface == "agent"
    assert kit_for("jarvis").brain_runner
    assert not kit_for("agent").brain_runner


def test_the_four_call_sites_read_the_kit() -> None:
    assert resolve_runner("openai", surface="jarvis") == "brain"
    assert resolve_runner("openai", surface="agent") == "api"
    assert ladder_key("jarvis", "brain") == JARVIS_LADDER
    assert ladder_key("agent", "api") == "api"


def test_build_override_uses_the_kit_tools_and_briefing(monkeypatch: pytest.MonkeyPatch) -> None:
    _probe_kit(monkeypatch)
    session = _session("probe")
    tools = {"probe_read": object()}
    override = build_override(
        session,
        brain=None,
        stance="ask",
        cwd=Path("."),
        ref="r",
        kit_tools=tools,  # type: ignore[arg-type]
        system_extra="BRIEFING",
    )
    assert set(override.tools_extra) == {"probe_read"}
    assert override.system_extra == "BRIEFING"
    assert override.tool_context["tool_origin"] == "probe-origin"
    assert override.tool_context["approval_surface"] == "interactive"
    assert override.max_turns == 7

    plain = build_override(_session("jarvis"), brain=None, stance="ask", cwd=Path("."), ref="r")
    assert "Read" in plain.tools_extra and plain.system_extra == ""
    assert plain.tool_context["tool_origin"] == "agent-chat"


async def test_kit_payload_builds_the_kit_tools_and_the_briefing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _probe_kit(monkeypatch)
    tools, extra = await kit_payload(_session("probe"), _Brain())
    assert tools is not None and set(tools) == {"probe_read"}
    assert extra == "BRIEFING"

    none_tools, none_extra = await kit_payload(_session("jarvis"), _Brain())
    assert none_tools is None and none_extra == ""


def test_a_kit_filter_keeps_only_its_own_hands(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = _probe_kit(monkeypatch)

    class _T:
        def __init__(self, name: str) -> None:
            self.name = name

    tools = {n: _T(n) for n in ("probe_read", "search_web", "patch", "spawn-worker")}
    assert kit.tool_filter is not None
    assert sorted(kit.tool_filter(tools)) == ["probe_read"]  # type: ignore[arg-type]
    assert kit_for("jarvis").tool_filter is None
