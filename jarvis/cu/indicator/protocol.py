"""JSON-lines IPC vocabulary between the indicator controller (main
process) and the renderer sidecar.

One JSON object per line on the sidecar's stdin::

    {"cmd": "show", "hint": "Esc to cancel"}   # fade the border in
    {"cmd": "hide"}                            # fade the border out
    {"cmd": "blank"}                           # hide INSTANTLY (capture guard)
    {"cmd": "unblank"}                         # restore after a frame grab
    {"cmd": "quit"}                            # exit the sidecar
    {"cmd": "snap", "monitor": [l, t, w, h],   # appshot shutter effect:
     "rect": [fx, fy, fw, fh],                 # flash the captured rect and
     "thumb": "<base64 jpeg>",                 # fly its thumbnail to a corner,
     "hint": "Click to edit · Drag to share"}  # where it rests as a card
    {"cmd": "snap_image", "image": "<b64>"}    # the finished (redacted) picture
                                               # a drag from the card hands out

The sidecar answers each command with one JSON line on stdout::

    {"ok": "<cmd>"}

and reports what the user does with the resting card::

    {"event": "card", "open": true|false}      # keep the sidecar alive meanwhile
    {"event": "snap_open"}                     # the card was clicked: edit it

and exits on stdin EOF (parent death) even without a ``quit``. Everything
is best-effort: the controller treats a missing/late ack as "sidecar gone"
and degrades; the sidecar ignores lines it cannot parse.
"""

from __future__ import annotations

import json
from typing import Any

CMD_SHOW = "show"
CMD_HIDE = "hide"
CMD_BLANK = "blank"
CMD_UNBLANK = "unblank"
CMD_QUIT = "quit"
CMD_SNAP = "snap"
CMD_SNAP_IMAGE = "snap_image"

ALL_COMMANDS = frozenset(
    {CMD_SHOW, CMD_HIDE, CMD_BLANK, CMD_UNBLANK, CMD_QUIT, CMD_SNAP, CMD_SNAP_IMAGE}
)

EVENT_CARD = "card"
EVENT_SNAP_OPEN = "snap_open"
ALL_EVENTS = frozenset({EVENT_CARD, EVENT_SNAP_OPEN})

#: Sidecar exit code when no usable GUI stack exists (PySide6 missing or
#: no display). The controller logs it as an expected degradation.
EXIT_NO_GUI = 3


def encode_command(cmd: str, **fields: Any) -> str:
    """Serialize one command line (newline-terminated)."""
    payload = {"cmd": cmd, **fields}
    return json.dumps(payload, ensure_ascii=False) + "\n"


def decode_command(line: str) -> dict[str, Any] | None:
    """Parse one stdin line; ``None`` for blank/garbled/unknown input."""
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except (ValueError, TypeError):  # a garbled sidecar line is no message
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("cmd") not in ALL_COMMANDS:
        return None
    return payload


def encode_ack(cmd: str) -> str:
    return json.dumps({"ok": cmd}) + "\n"


def encode_event(event: str, **fields: Any) -> str:
    """Serialize one sidecar → controller event line."""
    return json.dumps({"event": event, **fields}) + "\n"


def decode_event(line: str) -> dict[str, Any] | None:
    """Parse one stdout line as a known event; ``None`` otherwise."""
    try:
        payload = json.loads(line.strip())
    except (ValueError, TypeError):  # a garbled line is no event
        return None
    if isinstance(payload, dict) and payload.get("event") in ALL_EVENTS:
        return payload
    return None


def decode_ack(line: str) -> str | None:
    """Parse one ack line from the sidecar; ``None`` if it isn't one."""
    try:
        payload = json.loads(line.strip())
    except (ValueError, TypeError):  # a garbled line is no ack
        return None
    if isinstance(payload, dict) and isinstance(payload.get("ok"), str):
        return payload["ok"]
    return None


__all__ = [
    "ALL_COMMANDS",
    "CMD_BLANK",
    "CMD_HIDE",
    "CMD_QUIT",
    "CMD_SHOW",
    "CMD_SNAP",
    "CMD_SNAP_IMAGE",
    "EVENT_CARD",
    "EVENT_SNAP_OPEN",
    "CMD_UNBLANK",
    "EXIT_NO_GUI",
    "decode_ack",
    "decode_command",
    "encode_ack",
    "decode_event",
    "encode_command",
    "encode_event",
]
