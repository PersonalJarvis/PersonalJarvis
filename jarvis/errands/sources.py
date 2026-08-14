"""Where context could live — the code-assembled inventory for C13 move 1.

The gather phase used to hand the model the full tool map and hope it looked.
This module makes the looking-places explicit: CODE lists the context-bearing
tools that are actually wired, how past errands went, and which applications
are installed on this machine — and the prompt then requires the model to walk
that list before it may conclude "there was nowhere to look".

Everything degrades quietly: a missing tool drops its line, an empty errand
history contributes nothing, a headless server has no apps. The inventory is
advice rendered by code, never a gate — the gate lives in ``context_gate``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from typing import Final

# The sweep lives beside the per-name resolver so both trust the same roots.
# plugins→errands imports exist already (the start_errand tool); this is the
# reverse module edge and cycles are impossible because app_inventory imports
# nothing from jarvis.errands.
from jarvis.plugins.tool.app_inventory import installed_app_names

from .schema import TERMINAL_STATES
from .store import ErrandStore

log = logging.getLogger(__name__)

#: Context-bearing tools, keyed by their REGISTERED names, with what each one
#: can answer. Filtered against the live tool map at render time, so a tool
#: that is not wired (or was renamed) simply drops its line — degradation,
#: never breakage. The names are the same literals the plugins register.
_CONTEXT_TOOLS: Final[dict[str, str]] = {
    "wiki-recall": "the user's wiki — preferences, people, standing facts, past decisions",
    "wiki-page-read": "one wiki page in full, when recall found the right one",
    "awareness-recall": "what the user was doing on this machine, searchable by text and time",
    "contact-lookup": "people: numbers, addresses, relations",
    "gmail": "mail — bookings, confirmations, correspondence",
    "google_calendar": "dates, clashes, where the user has to be",
    "google_drive": "the user's documents and files",
    "credentials": "which logins exist for which service (never the secrets themselves)",
    "browser": "any web service without a dedicated integration, including webmail and chats",
}

#: How many finished errands the inventory mentions. Recent outcomes carry the
#: "how did this go last time" signal; older ones are noise.
_RECENT_ERRANDS: Final[int] = 6

#: Apps named in the prompt before the list is summarised to a count.
_APPS_SHOWN: Final[int] = 40


async def render_context_sources(
    *, tool_names: Collection[str], store: ErrandStore | None = None
) -> str:
    """The SOURCES block for ``context_prompt``. Empty string when nothing is known."""
    lines: list[str] = []

    for name, answers in _CONTEXT_TOOLS.items():
        if name in tool_names:
            lines.append(f"- tool `{name}`: {answers}")

    if store is not None:
        try:
            for errand in await store.list_recent(limit=_RECENT_ERRANDS * 3):
                if errand.state not in TERMINAL_STATES:
                    continue  # a live errand is not "how it went last time"
                outcome = errand.outcome.replace("\n", " ")[:90]
                lines.append(f'- past errand "{errand.goal[:60]}" — {errand.state}: {outcome}')
                if sum(line.startswith("- past errand") for line in lines) >= _RECENT_ERRANDS:
                    break
        except Exception:  # noqa: BLE001 — history is a bonus, never a blocker
            log.debug("errand sources: could not list past errands", exc_info=True)

    try:
        apps = await asyncio.to_thread(installed_app_names)
    except Exception:  # noqa: BLE001 — the sweep must never break a gather
        log.debug("errand sources: app inventory unavailable", exc_info=True)
        apps = ()
    if apps:
        shown = ", ".join(apps[:_APPS_SHOWN])
        more = f" … and {len(apps) - _APPS_SHOWN} more" if len(apps) > _APPS_SHOWN else ""
        lines.append(
            "- applications installed on this machine (a service with a desktop "
            "app usually also has a web page the `browser` tool can reach): "
            f"{shown}{more}"
        )

    return "\n".join(lines)


__all__ = ["render_context_sources"]
