"""What a chat surface brings to a turn: the runner, the ladder, the hands.

A *surface* is one place a person types to Jarvis: the front page (``jarvis``)
and the Agentic IDE's chat mode (``agent``). They share the session store, the
WebSocket stream and the approval card; they differ in what answers and with
which tools. Before this module those differences were ``if surface == "jarvis"``
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
from pathlib import Path
from typing import Any, Final

from jarvis.core.protocols import Tool

log = logging.getLogger(__name__)

__all__ = [
    "SurfaceKit",
    "kit_for",
]

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
    ``cli_seats``
        Whether a vendor CLI may run a turn here. ``False`` means every seat
        on this surface is an API endpoint behind a key: the picker lists
        only providers with a brain plugin, and the CLI rows (Claude Code,
        Codex, Antigravity, Grok Build) are not offered at all.
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
    ``workspace_dir``
        Where a new session starts when nobody picked a folder; ``None`` =
        the service's own fallback.
    """

    surface: str
    brain_runner: bool = False
    cli_seats: bool = True
    ladder: str | None = None
    uses_stance: bool = False
    tools: ToolsBuilder | None = None
    system_extra: ExtraBuilder | None = None
    tool_origin: str = "agent-chat"
    credential_scope: str = "agent"
    max_turns: int | None = None
    #: Applied to the merged tool set (the brain's own plus ``tools``): a
    #: surface that only ever needs its own hands keeps the request small
    #: and free of foreign schemas a provider may refuse. ``None`` = all.
    tool_filter: Callable[[dict[str, Tool]], dict[str, Tool]] | None = None
    #: The folder a new session of this surface starts in when the person
    #: picked none. ``None`` = the service's fallback (the home directory),
    #: which is what the IDE's chat wants: a coding agent is pointed at a
    #: project, and its composer shows the folder chip so it can be moved.
    workspace_dir: Callable[[], Path] | None = None


def _chat_workspace() -> Path:
    """The Jarvis chat's own working folder — imported lazily (AP-26)."""
    from jarvis.core.paths import chat_workspace_dir

    return chat_workspace_dir()


def _local_models_tools(cfg: Any, _brain: Any) -> dict[str, Tool]:
    """The setup assistant's hands — the ``lm_*`` tools (lazy, AP-26)."""
    from jarvis.local_models.assistant_tools import build_tools
    from jarvis.local_models.health_monitor import server_root

    return build_tools(cfg, root=server_root(cfg))


async def _local_models_extra(cfg: Any, _brain: Any) -> str:
    """What the assistant can already see: machine, inventory, roles, catalogue.

    Read per turn rather than once per session, because the whole point of the
    assistant is that things change while it works — a model finishes pulling,
    the server comes up, a role gets filled.
    """
    from jarvis.local_models.assistant_prompt import build_system_extra
    from jarvis.local_models.health_monitor import server_root

    return await build_system_extra(cfg, root=server_root(cfg))


def _only_local_models(tools: dict[str, Tool]) -> dict[str, Tool]:
    """Its own hands and nothing else.

    The assistant sets up local models; the wiki, the calendar and the folder
    tools are foreign schemas that only make the request bigger and give a
    provider more to refuse.
    """
    from jarvis.local_models.assistant_tools import TOOL_PREFIX

    return {name: tool for name, tool in tools.items() if name.startswith(TOOL_PREFIX)}


# ── the table ─────────────────────────────────────────────────────────────

_KITS: Final[dict[str, SurfaceKit]] = {
    "agent": SurfaceKit(surface="agent"),
    "jarvis": SurfaceKit(
        surface="jarvis",
        brain_runner=True,
        # No vendor CLI here (maintainer, 2026-08-26). What answers on the
        # front page is Jarvis' own harness on a provider API behind a key —
        # one pipeline, one place the model is picked, one usage ledger. A
        # subscription CLI seat is a coding agent and belongs to the IDE's
        # chat, where its own loop and its own tools are the point.
        cli_seats=False,
        ladder=_JARVIS_LADDER,
        uses_stance=True,
        # Not the home directory: this surface hands out the folder tools, and
        # the read-only four are tier ``safe`` — they run without a card. The
        # composer hides the chip here (a person talks to Jarvis, they do not
        # point it at a checkout), so the default is also the only folder most
        # of these chats ever see. It has to be a small one.
        workspace_dir=_chat_workspace,
    ),
    # The Local models section's setup assistant. It runs on Jarvis' own
    # harness over the Agents tier (an API key, never a vendor CLI and never
    # the voice brain) so that a person whose voice runs on Ollama still gets
    # a capable model to set Ollama UP. Session policy — one chat per install,
    # re-created when the tier moves — lives in
    # ``jarvis/local_models/assistant_session.py``.
    "local-models": SurfaceKit(
        surface="local-models",
        brain_runner=True,
        cli_seats=False,
        ladder=_JARVIS_LADDER,
        uses_stance=True,
        tools=_local_models_tools,
        system_extra=_local_models_extra,
        tool_origin="local-models-assistant",
        tool_filter=_only_local_models,
        # No folder: this surface never touches a checkout. Its hands talk to
        # the local server and the config, and the composer shows no chip.
        workspace_dir=_chat_workspace,
    ),
}


def kit_for(surface: str) -> SurfaceKit:
    """The kit of ``surface``; an unknown name behaves like the agent surface."""
    return _KITS.get(surface or "", _KITS["agent"])
