"""Keep working when this PC closes: move running panes to a computer on quit.

Opt-in, one setting: a connected computer's id (or nothing). When the app
quits while it is set, every open workspace with an agent running on THIS
machine is moved there first (``Registry.place_workspace``) — folder,
uncommitted edits and conversations included — so the agents carry on in tmux
on the server while the PC is off, and the next start shows them running there.

Bounded by the quit sequence's timeout: a quit is never held open for long,
and a move that could not finish leaves the pane where it was (the placement
is recorded only after the move completed).

A Windows computer is never a target: it has no tmux, so its panes end with
the app's SSH connection (``jarvis.computers.remote_terminal``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger


def _path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agentic_ide" / "offload_on_quit.json"


def target() -> str | None:
    """The computer running panes move to on quit, or None (off)."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:  # never set: the feature is off
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Agentic IDE: unreadable offload-on-quit setting: {}", exc)
        return None
    value = data.get("computer_id") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def set_target(computer_id: str | None) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"computer_id": computer_id or None}), encoding="utf-8")
    os.replace(tmp, path)


def _is_windows(computer_id: str) -> bool:
    """From the last check's facts: no network call while the app quits."""
    from jarvis.computers.store import ComputerStore

    computer = ComputerStore().get(computer_id)
    facts = computer.facts if computer is not None else None
    return facts is not None and facts.os_id == "windows"


async def offload_before_quit(registry: Any) -> list[str]:
    """Move every workspace with a locally running agent. Returns their ids."""
    computer_id = target()
    if not computer_id:
        return []
    if _is_windows(computer_id):
        # No tmux there: a pane lives exactly as long as this app's SSH channel,
        # so moving it at quit would end the agent instead of saving it.
        logger.warning(
            "Agentic IDE: not moving panes on quit — {} runs Windows, where they would stop",
            computer_id,
        )
        return []
    moved: list[str] = []
    for workspace in list(registry.sessions):
        running_here = [
            term for term in workspace.terminals if term.pty_id and not term.computer_id
        ]
        if not running_here:
            continue
        try:
            await registry.place_workspace(workspace.id, computer_id=computer_id)
        except Exception as exc:  # noqa: BLE001 - one workspace failing must not block the rest
            logger.warning("Agentic IDE: moving {} before quit failed: {}", workspace.name, exc)
            continue
        moved.append(workspace.id)
        logger.info("Agentic IDE: moved {} to {} before quitting", workspace.name, computer_id)
    return moved
