"""Codex-fleet actions shared by voice and REST entry points."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any

READY_TIMEOUT_S = 45.0
READY_POLL_S = 0.25
_INPUT_MARKERS = ("›", "❯", ">")
log = logging.getLogger(__name__)


def ready_for_prompt(term: Any) -> bool:
    """Is this pane far enough along to be typed into?

    Every CLI observed so far swallows a prompt that arrives while it is still
    booting — the keystrokes go nowhere and the user is told the work was sent.
    So waiting for a real input line is the DEFAULT, and an entry only skips it
    by declaring so: one CLI has a launch-and-prompt path measured to be stable
    without the wait, and making it wait would slow down behaviour that already
    works.

    Getting that default the wrong way round is what a new provider cannot
    afford. Opting in silently means every CLI registered later loses its first
    prompt until somebody notices; opting out silently costs one CLI a little
    speed. The cheap mistake is the one worth defaulting to.
    """
    if getattr(term, "status", "") != "live" or not getattr(term, "pty_id", None):
        return False
    from jarvis.workspace import agents as workspace_agents

    spec = workspace_agents.get_agent(getattr(term, "agent", ""))
    if spec is not None and not spec.needs_input_line_wait:
        return True
    transcript = getattr(term, "transcript", None)
    if transcript is None:
        return False
    try:
        # Read the visible bottom of the current screen when it is available.
        # Transcript history can contain old prompt sigils from the conversation
        # being resumed; treating one of those as the new input box is the same
        # early-write bug with a more convincing false positive.
        screen = getattr(transcript, "screen", None)
        display = getattr(screen, "display", None)
        lines = display()[-10:] if callable(display) else transcript.tail(10)
    except Exception:  # noqa: BLE001 - a test double may expose no real buffer
        return False
    markers = (spec.input_markers if spec else ()) or _INPUT_MARKERS
    if spec is not None and spec.requires_visible_input_cursor:
        # Codex renders the same sigil when its composer is disabled, but its
        # Ratatui frame hides the cursor in that state. The visible cursor is
        # placed back on the editable row only when key events are accepted.
        try:
            visible_cursor = getattr(screen, "visible_cursor", None)
            row_text = getattr(screen, "row_text", None)
            if visible_cursor is None or not callable(row_text):
                return False
            row, column = visible_cursor
            line = row_text(row)
        except Exception:  # noqa: BLE001 - malformed extensions fail closed
            return False
        stripped = line.lstrip()
        for marker in markers:
            if stripped.startswith(marker):
                marker_column = len(line) - len(stripped)
                return column > marker_column
        return False
    return any(line.strip().startswith(markers) for line in lines)


def _ready_for_prompt(term: Any) -> bool:
    """Backward-compatible private name for older callers and extensions."""
    return ready_for_prompt(term)


async def wait_for_prompt_ready(
    session: Any,
    names: Sequence[str],
    *,
    timeout_s: float = READY_TIMEOUT_S,
) -> tuple[str, ...]:
    """Wait for newly mounted panes to display their real input line."""
    wanted = tuple(dict.fromkeys(name for name in names if name))
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        ready = tuple(
            name
            for name in wanted
            if (term := session.find(name)) is not None and ready_for_prompt(term)
        )
        if len(ready) == len(wanted) or time.monotonic() >= deadline:
            return ready
        await asyncio.sleep(READY_POLL_S)


async def close_agent_terminals(registry: Any, agent: str | None) -> list[Any]:
    """Close every matching terminal in the front workspace."""
    session = registry.session
    if session is None:
        return []
    names = [
        term.name
        for term in session.terminals
        if agent is None or term.agent == agent
    ]
    closed: list[Any] = []
    # Use the long-standing one-pane operation so this remains compatible with
    # registries that do not yet expose the newer batch convenience method.
    for name in names:
        try:
            closed.append(await registry.close_terminal(name))
        except Exception:  # noqa: BLE001 - continue closing the remaining fleet
            log.warning("Agentic IDE: could not close terminal %s", name, exc_info=True)
    return closed


def terminals_closed_event(
    session: Any,
    closed: Sequence[Any],
    *,
    source_layer: str,
) -> Any:
    """Build the typed bus event that refreshes workspace clients."""
    from jarvis.core.events import AgenticIdeTerminalsClosed

    return AgenticIdeTerminalsClosed(
        session_id=session.id,
        names=tuple(term.name for term in closed),
        folder=session.folder,
        source_layer=source_layer,
    )


__all__ = [
    "READY_POLL_S",
    "READY_TIMEOUT_S",
    "close_agent_terminals",
    "ready_for_prompt",
    "terminals_closed_event",
    "wait_for_prompt_ready",
]
