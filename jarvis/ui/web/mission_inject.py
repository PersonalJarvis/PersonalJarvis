"""Compose the brain-turn directive for a mission dragged into the conversation.

A dropped Outputs card carries its own display text (utterance / status /
summary / error). We turn that into one clean, bounded, human-readable user
turn. Publishing it as ``MessageSent(role="user")`` reuses the whole existing
brain pipeline — the reply is spoken on the voice build and shown in chat, and
the text lands in the brain's history (the "context window") so follow-ups work.
"""
from __future__ import annotations

from typing import Any

# Hard cap so a huge worker summary can't blow the token budget or the
# ``_WS_SEND_TIMEOUT_S`` circuit-breaker on the event broadcast.
MISSION_INJECT_CAP = 4000

#: ``MessageSent.source_layer`` stamped on a drag-dropped mission recap turn.
#: The router exempts this source from force-spawn so a recap is DISCUSSED
#: inline, never re-dispatched as a new mission (the doom-loop fixed 2026-06-16:
#: a dropped card whose own text contains a spawn trigger / action verb leaked
#: that trigger into the directive -> force-spawn -> empty diff ->
#: critic_loop_exhausted). The brain mirrors this value in
#: ``jarvis.brain.manager._NON_SPAWN_SOURCE_LAYERS`` (parity test in
#: tests/unit/brain/test_routing.py).
MISSION_INJECT_SOURCE_LAYER = "ui.web.ws.mission_inject"


def compose_mission_inject_text(payload: dict[str, Any]) -> str | None:
    """Build the user-turn directive, or ``None`` if there is nothing to inject."""
    utterance = str(payload.get("utterance") or "").strip()
    slug = str(payload.get("slug") or "").strip()
    if not utterance and not slug:
        return None

    title = utterance or slug
    status = str(payload.get("status") or "unknown").strip() or "unknown"
    summary = str(payload.get("summary") or "").strip()
    error = str(payload.get("error") or "").strip()

    # Phrased as a recap request about an ALREADY-FINISHED job. It must not
    # contain the router's force-spawn triggers ("sub-agent"/"spawn"/"delegate"/
    # "openclaw") or action verbs — a dropped mission is discussed, never
    # re-dispatched (spec AP-5/AP-14). See test_compose_avoids_router_spawn_*.
    parts = [f'\U0001F4CE I\'ve pinned a finished task to our conversation: "{title}" (status: {status}).']
    if summary:
        parts.append(f"\nWhat it produced:\n{summary}")
    if error:
        parts.append(f"\nIt ended with this error:\n{error}")
    parts.append(
        "\nThis one is already complete, so no new work is needed — just give me "
        "a short, friendly recap of it and we'll talk it over."
    )

    text = "\n".join(parts).strip()
    if len(text) > MISSION_INJECT_CAP:
        suffix = " …"
        text = text[: MISSION_INJECT_CAP - len(suffix)].rstrip() + suffix
    return text
