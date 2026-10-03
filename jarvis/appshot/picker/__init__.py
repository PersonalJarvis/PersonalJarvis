"""The area picker for region appshots — a one-shot PySide6 sidecar.

``python -m jarvis.appshot.picker --lang de`` dims every screen, lets the
user drag one rectangle, mark it up in place with a toolbar and prints
the result as one JSON line on stdout, then exits. The main process never
imports PySide6 (AP-26); it talks to this process through
:mod:`jarvis.appshot.region`.

Wire format (one JSON object per line):

stdout::

    {"event": "ready"}                                  # overlay is up
    {"event": "marking"}                                # area chosen, toolbar up
    {"event": "selection", "screen": {"x":…, "y":…,     # the Qt screen,
     "w":…, "h":…, "dpr":…}, "rect": [fx, fy, fw, fh],  # fractions of it,
     "action": "done" | "copy" | "save" | "edit",       # what the user pressed,
     "markup": {"overlay": "<b64 PNG>",                 # markings (optional):
                "hides": [{"kind": "blur" | "pixelate", # transparent overlay of
                           "rect": [fx, fy, fw, fh]}]}} # the area + hide patches
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
EVENT_MARKING = "marking"
EVENT_SELECTION = "selection"
#: What finishing the selection asks for besides taking the appshot.
ACTION_DONE = "done"
ACTION_COPY = "copy"
ACTION_SAVE = "save"
ACTION_EDIT = "edit"
ACTIONS = (ACTION_DONE, ACTION_COPY, ACTION_SAVE, ACTION_EDIT)
CMD_CANCEL = "cancel"
CMD_LAYOUT = "layout"


def encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def decode(line: str) -> dict[str, Any] | None:
    """One parsed line, or ``None`` for blank or garbled input."""
    try:
        payload = json.loads(line.strip())
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "ACTIONS",
    "ACTION_COPY",
    "ACTION_DONE",
    "ACTION_EDIT",
    "ACTION_SAVE",
    "CMD_CANCEL",
    "CMD_LAYOUT",
    "EVENT_MARKING",
    "EVENT_READY",
    "EVENT_SELECTION",
    "EXIT_NO_GUI",
    "decode",
    "encode",
]
