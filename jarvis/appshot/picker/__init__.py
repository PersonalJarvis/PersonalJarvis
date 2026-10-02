"""The area picker for region appshots — a one-shot PySide6 sidecar.

``python -m jarvis.appshot.picker --hint "<text>"`` dims every screen, lets
the user drag one rectangle and prints the result as one JSON line on stdout,
then exits. The main process never imports PySide6 (AP-26); it talks to this
process through :mod:`jarvis.appshot.region`.

Wire format (one JSON object per line):

stdout::

    {"event": "ready"}                                  # overlay is up
    {"event": "selection", "screen": {"x":…, "y":…,     # the Qt screen,
     "w":…, "h":…, "dpr":…}, "rect": [fx, fy, fw, fh]}  # fractions of it
    {"event": "selection", "cancelled": true}           # Esc / right-click

stdin::

    {"cmd": "layout", "monitors": [...],                # mss monitors and the
     "windows": [[l, t, w, h], ...]}                    # snap targets, top first
    {"cmd": "cancel"}                                   # e.g. a global Esc

Stdin EOF (the parent died) cancels as well.
"""

from __future__ import annotations

import json
from typing import Any

#: Exit code when no usable GUI stack exists (PySide6 missing, no display).
EXIT_NO_GUI = 3

EVENT_READY = "ready"
EVENT_SELECTION = "selection"
CMD_CANCEL = "cancel"
CMD_LAYOUT = "layout"


def encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def decode(line: str) -> dict[str, Any] | None:
    """One parsed line, or ``None`` for blank or garbled input."""
    try:
        payload = json.loads(line.strip())
    except (ValueError, TypeError):  # a garbled picker line is skipped, not an error
        return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "CMD_CANCEL",
    "CMD_LAYOUT",
    "EVENT_READY",
    "EVENT_SELECTION",
    "EXIT_NO_GUI",
    "decode",
    "encode",
]
