"""What a chat surface brings to a turn: the runner, the ladder, the hands.

A *surface* is one place a person types to Jarvis: the front page (``jarvis``),
the Agentic IDE's chat mode (``agent``), and the Local models section's setup
assistant (``local-models``). They share the session store, the WebSocket
stream and the approval card; they differ in what answers and with which
tools. Before this module those differences were ``if surface == "jarvis"``
branches in four files. Now each surface is one :class:`SurfaceKit` here and
the four call sites do one lookup each — a new surface is a new kit, not four
new branches.

Import-light: the kit builders import their tool modules lazily so this file
costs nothing at boot (AP-26).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from jarvis.core.protocols import Tool

log = logging.getLogger(__name__)

__all__ = [
    "LOCAL_MODELS_SURFACE",
    "LOCAL_MODELS_TOOL_ORIGIN",
    "SurfaceKit",
    "kit_for",
    "local_models_search_fn",
]

LOCAL_MODELS_SURFACE: Final[str] = "local-models"
LOCAL_MODELS_TOOL_ORIGIN: Final[str] = "local-models-assistant"

#: The ladder key of the Jarvis ladder (``permissions.JARVIS_LADDER``) —
#: spelled here so the kit table does not import the permissions module.
_JARVIS_LADDER: Final[str] = "jarvis"

ToolsBuilder = Callable[[Any, Any], dict[str, Tool]]
ExtraBuilder = Callable[[Any, Any], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class SurfaceKit:
    """One surface's turn recipe.

    ``brain_runner``
        Whether an API provider is driven by Jarvis' own brain (``brain``)
        instead of the coding agent's loop (``api``).
    ``ladder``
        The permission ladder the composer shows, or ``None`` for the
        runner's own ladder.
    ``uses_stance``
        Whether the session's permission mode reaches the brain runner as a
        stance (it does on every brain-driven surface).
    ``tools`` / ``system_extra``
        Builders called per turn with ``(cfg, brain)``: the surface's own
        tools instead of the folder tools, and a per-turn system-prompt
        addendum (``TurnOverride.system_extra``). ``None`` = the folder
        tools / no addendum.
    ``tool_origin`` / ``credential_scope`` / ``max_turns``
        What the tool context and the override carry; ``max_turns=None``
        keeps the runner's default.
    """

    surface: str
    brain_runner: bool = False
    ladder: str | None = None
    uses_stance: bool = False
    tools: ToolsBuilder | None = None
    system_extra: ExtraBuilder | None = None
    tool_origin: str = "agent-chat"
    credential_scope: str = "agent"
    max_turns: int | None = None


# ── local-models builders ─────────────────────────────────────────────────


def _config_of(cfg: Any, brain: Any) -> Any:
    if cfg is not None:
        return cfg
    live = getattr(brain, "_config", None)
    if live is not None:
        return live
    from jarvis.core.config import load_config

    return load_config()


def local_models_search_fn(brain: Any) -> Callable[[str], Awaitable[list[dict[str, Any]]]] | None:
    """``search_web`` as a plain ``query -> [{title, url, snippet}]`` call.

    Read off the brain's attached tool surface — the same key-free tool a
    voice turn uses — so the benchmark refresh has no search backend of its
    own. ``None`` when the brain has no such tool (the table is read-only then).
    """
    tools = getattr(brain, "_tools", None) or {}
    tool = tools.get("search_web") if isinstance(tools, dict) else None
    if tool is None or not callable(getattr(tool, "execute", None)):
        return None

    async def _search(query: str) -> list[dict[str, Any]]:
        import uuid

        from jarvis.core.protocols import ExecutionContext

        ctx = ExecutionContext(
            trace_id=uuid.uuid4(), user_utterance=query, config={}, memory_read=None
        )
        result = await tool.execute({"query": query, "max_results": 8}, ctx)
        output = getattr(result, "output", None)
        rows = output.get("results") if isinstance(output, dict) else None
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    return _search


def _local_models_tools(cfg: Any, brain: Any) -> dict[str, Tool]:
    from jarvis.brain.ollama_pull import server_root
    from jarvis.local_models.assistant_tools import build_tools

    return build_tools(
        _config_of(cfg, brain), root=server_root(), search_fn=local_models_search_fn(brain)
    )


async def _local_models_extra(cfg: Any, brain: Any) -> str:
    from jarvis.brain.ollama_pull import server_root
    from jarvis.local_models.assistant_prompt import build_system_extra

    try:
        return await build_system_extra(_config_of(cfg, brain), root=server_root())
    except Exception as exc:  # noqa: BLE001 — a turn without the briefing beats no turn
        log.warning("local-models assistant: briefing not built: %s", exc, exc_info=True)
        return (
            "LOCAL MODELS SETUP ASSISTANT\nThe machine briefing could not be built "
            f"({type(exc).__name__}); read everything with the lm_* tools first."
        )


# ── the table ─────────────────────────────────────────────────────────────

_KITS: Final[dict[str, SurfaceKit]] = {
    "agent": SurfaceKit(surface="agent"),
    "jarvis": SurfaceKit(
        surface="jarvis",
        brain_runner=True,
        ladder=_JARVIS_LADDER,
        uses_stance=True,
    ),
    LOCAL_MODELS_SURFACE: SurfaceKit(
        surface=LOCAL_MODELS_SURFACE,
        brain_runner=True,
        ladder=_JARVIS_LADDER,
        uses_stance=True,
        tools=_local_models_tools,
        system_extra=_local_models_extra,
        tool_origin=LOCAL_MODELS_TOOL_ORIGIN,
        # A guided run pulls, sets five roles, tunes and tests: more tool
        # rounds than a chat answer needs.
        max_turns=40,
    ),
}


def kit_for(surface: str) -> SurfaceKit:
    """The kit of ``surface``; an unknown name behaves like the agent surface."""
    return _KITS.get(surface or "", _KITS["agent"])
