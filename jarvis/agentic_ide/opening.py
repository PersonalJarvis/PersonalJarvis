"""The first thing a person said in a pane's CLI — remembered, never re-read.

A coding pane has three places its title can come from, and two of them are
gone exactly when the title is needed most. The instruction Jarvis sent
(``Terminal.last_prompt``) lives in the backend's memory and does not survive
a restart. The echo of what the user typed lives in the pane's scrollback and
scrolls out of the replay buffer as soon as the agent has printed a screen or
two — which is why a working pane's header read "Claude Code — running since
11:29" while the pane was an hour into the task it had been typed
(maintainer report 2026-08-27).

The CLI's own transcript has neither problem. It is on disk, it is the same
file the chat stage draws the conversation from, and its first user record IS
the request that opened the session — the closest thing a terminal has to a
chat's first message. This module reads that one record once per pane and
keeps it: the opening of a conversation never changes, so a hit is permanent
and a miss (the CLI has not written its file yet) is retried on a slow clock.

The read happens off the event loop when one is running: the callers here
(:func:`jarvis.agentic_ide.recap.summarize`, the session list's ``to_row``)
are polled for every pane of every open workspace, and a directory walk per
pane per tick is the difference between a list and a stutter. The first poll
after a pane opens therefore answers with "" and the next one has the text —
a delay nobody notices against a pane that has just started.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from . import agent_transcript

#: How long a pane whose file is not there yet waits before it is looked for
#: again. A CLI writes its first record within a second of the first message,
#: so anything shorter spends directory walks on a pane nobody has typed into.
RETRY_S = 20.0

#: The opening is a title's raw material, not a transcript: this is more than
#: any headline shows and less than a pasted brief runs to.
MAX_CHARS = 400

#: Entries are dropped by pane close (:func:`forget`); this bound only keeps a
#: process that never closes anything from growing without limit.
MAX_ENTRIES = 512


@dataclass
class _Entry:
    text: str = ""
    checked_at: float = 0.0
    inflight: bool = False


#: Keyed by pane key AND session id: T1 is handed on to a replacement pane,
#: and a pane that is resumed into a different conversation must not open
#: under the last conversation's first line.
_cache: dict[tuple[str, str], _Entry] = {}


def opening_for(term: Any) -> str:
    """What the person first said in ``term``'s CLI session, or "" if unknown.

    "" for a pane with no resume handle, a CLI whose record cannot be read, a
    file that is not there yet, and for the first call that schedules the read
    (see the module docstring). Never raises — this is on the path of every
    state read, and a title is never worth a broken workspace.
    """
    handle = getattr(term, "resume", None)
    session_id = str(getattr(handle, "id", "") or "").strip() if handle is not None else ""
    agent = str(getattr(term, "agent", "") or "").strip().lower()
    if not session_id or not agent_transcript.can_read(agent):
        return ""
    key = (str(getattr(term, "key", "") or ""), session_id)
    entry = _cache.get(key)
    if entry is None:
        if len(_cache) >= MAX_ENTRIES:
            _cache.pop(next(iter(_cache)))
        entry = _cache[key] = _Entry()
    if entry.text:
        return entry.text
    now = time.monotonic()
    if entry.inflight or (entry.checked_at and now - entry.checked_at < RETRY_S):
        return ""
    entry.inflight = True
    home = _home(term)

    def _read() -> None:
        try:
            text = agent_transcript.first_user_text(agent, session_id, home=home)
        except Exception as exc:  # noqa: BLE001 - a title must never break a state read
            logger.debug("Agentic IDE: opening of {} unreadable: {}", session_id, exc)
            text = None
        entry.text = " ".join(str(text or "").split())[:MAX_CHARS]
        entry.checked_at = time.monotonic()
        entry.inflight = False

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop (a test, a CLI): nothing to keep free, so answer now.
        _read()
        return entry.text
    loop.run_in_executor(None, _read)
    return ""


def forget(key: str) -> None:
    """Drop what is remembered about one pane — it has been closed."""
    for cached in [item for item in _cache if item[0] == key]:
        _cache.pop(cached, None)


def reset_for_tests() -> None:
    _cache.clear()


def _home(term: Any) -> Path | None:
    """The config dir this pane's CLI keeps its history in, when redirected.

    Same rule as the transcript routes: each subscription of one CLI keeps its
    conversations in its own directory, and the machine default would answer
    with a different account's chat. ``None`` keeps the reader on its default.
    """
    account = str(getattr(term, "account", "") or "")
    agent = str(getattr(term, "agent", "") or "")
    if not account:
        return None
    try:
        from jarvis import agent_accounts

        if agent not in agent_accounts.platforms():
            return None
        return agent_accounts.config_dir_for(agent, account)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - the default home is the honest fallback
        logger.debug("Agentic IDE: account folder for {} is unknown: {}", agent, exc)
        return None


__all__ = ["MAX_CHARS", "RETRY_S", "forget", "opening_for", "reset_for_tests"]
