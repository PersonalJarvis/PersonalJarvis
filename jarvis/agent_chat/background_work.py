"""Detect concrete future-work promises; scheduling is always a tool effect."""

from __future__ import annotations

import re
from typing import Any

_PROMISE = re.compile(
    r"\b(?:i(?:['’]ll| will)\s+(?:check|review|monitor|watch|research|report|follow)|"
    r"ich\s+(?:werde|prüfe|schaue|kontrolliere|beobachte|melde)|"  # i18n-allow
    r"voy\s+a\s+(?:revisar|comprobar|vigilar))\b"  # i18n-allow
    r"[^.!?\n]{0,240}\b(?:later|tomorrow|daily|every|background|back|"
    r"später|morgen|täglich|regelmäßig|hintergrund|wieder|luego|mañana)\b",  # i18n-allow
    re.I,
)


def promises_background_work(request: str, response: str) -> bool:
    from jarvis.society.autonomous_routines import is_informational_request

    if is_informational_request(request):
        return False
    return bool(_PROMISE.search(response))


def without_background_promises(response: str) -> str:
    """Keep already delivered findings when stripping an unsupported promise."""
    clauses = re.split(r"(?<=[.!?])\s+|\n+", response)
    return "\n".join(clause for clause in clauses if not _PROMISE.search(clause)).strip()


async def has_scheduled_work(session_id: str, task_ids: set[str]) -> bool:
    """A successful tool receipt is checked against its current durable state."""
    if not task_ids:
        return False
    from jarvis.society.routines import is_agent_routine
    from jarvis.society.runtime import current_runtime
    from jarvis.society.surface import agent_id_of

    runtime = current_runtime()
    agent_id = agent_id_of(session_id)
    if runtime is None or agent_id is None:
        return False
    store, _ = runtime.task_services()
    if store is None:
        return False
    for task_id in task_ids:
        row: dict[str, Any] | None = await store.get(task_id)
        if row and row["state"] in {"scheduled", "running"} and is_agent_routine(row, agent_id):
            return True
    return False
