"""Inherited environment variables that falsely claim colour is impossible.

One answer for the whole app, because two layers need it and a second copy
would drift apart the first time either is touched:

* the **PTY boundary** (`jarvis.terminal.pty_manager`), where a child is
  attached to a pane drawn by xterm.js, and
* **app start-up** (`jarvis.ui.web.launcher`), which covers everything else the
  app spawns — including the external terminals opened by
  `jarvis.clis.external_terminal`, which inherit this process's environment
  wholesale because they pass no ``env`` of their own.

Why this exists at all: a desktop app launched once from a coding agent's or a
CI shell inherits that shell's honest self-description — ``NO_COLOR=1``, an
empty ``COLORTERM`` — and then hands it to every terminal it hosts, on every
restart, indefinitely (the restart chain re-inherits it; see
`jarvis.ui.relauncher.fresh_user_env`, which deliberately refreshes only the
``JARVIS__*`` namespace). The statement was true about that shell's pipe. It is
false about a terminal pane, and every mainstream CLI colour library obeys it
without asking, so the whole workspace comes up monochrome.

Deliberate settings survive. ``FORCE_COLOR`` counts as stale only when it
DISABLES colour — a positive value is an escalation the user meant.
``COLORTERM`` counts only when EMPTY, because the common colour libraries read
its mere presence as "sixteen colours": an empty one inherited from a pipe
DOWNGRADES a 256-colour terminal, while a real value is authoritative.

``NO_COLOR`` counts whenever it is PRESENT, empty value included. The published
convention asks consumers to act only on a non-empty value, but consumers
disagree in practice — several check presence alone — and the two readings
point opposite ways for ``NO_COLOR=``. That ambiguity cannot express a user's
intent either way: anyone who actually wants colour off writes a value, so an
empty one reaching a terminal host is an inheritance artefact by construction.
Dropping it removes the ambiguity instead of betting on which library reads it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: Values of ``FORCE_COLOR`` that mean "no colour". Anything else is a level.
_FORCE_COLOR_OFF = frozenset({"0", "false"})


def stale_colour_claims(source: Mapping[str, str]) -> tuple[str, ...]:
    """Names in ``source`` that wrongly declare colour impossible, in order.

    Returns an empty tuple for an environment that says nothing about colour,
    which lets callers skip copying it at all.
    """
    stale: list[str] = []
    if "NO_COLOR" in source:
        stale.append("NO_COLOR")
    if source.get("FORCE_COLOR", "").strip().lower() in _FORCE_COLOR_OFF:
        stale.append("FORCE_COLOR")
    if "COLORTERM" in source and not source["COLORTERM"].strip():
        stale.append("COLORTERM")
    return tuple(stale)


def sanitize_process_environment() -> tuple[str, ...]:
    """Drop the stale claims from THIS process's environment; report what went.

    Called once at start-up so that no child of the app can inherit them,
    whichever spawn path it takes. Returning the names rather than logging here
    keeps the decision to log — and in whose voice — with the caller. That
    caller owes the user the line: an app that silently rewrites its own
    environment and leaves no trace is the thing nobody can debug later, and at
    start-up the stdlib root logger discards INFO records, so it takes a sink
    that is live from import (`jarvis.ui.web.launcher` uses loguru).

    Cheap by construction: a few dict lookups, no import, no I/O, so it is safe
    on the boot critical path (AP-26).
    """
    stale = stale_colour_claims(os.environ)
    for name in stale:
        os.environ.pop(name, None)
    return stale
