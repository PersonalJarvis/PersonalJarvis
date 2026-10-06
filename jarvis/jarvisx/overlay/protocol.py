"""JSON-lines IPC between the Jarvis X controller and its overlay sidecar.

One JSON object per line on the sidecar's stdin (commands)::

    {"cmd": "select", "req": 7, "purpose": "capture"|"record"}
        # dim every screen, let the user drag a rectangle
    {"cmd": "cancel_select"}                    # abort a running selection
    {"cmd": "card", "id": "...", "monitor": [l, t, w, h], "rect": [fx, fy, fw, fh],
     "thumb": "<base64 jpeg>", "persist": bool, "dismiss_s": int,
     "badge": "" | "0:12"}                      # flash + fly into a corner card
    {"cmd": "remove_card", "id": "..."}         # the item was deleted
    {"cmd": "blank"} / {"cmd": "unblank"}       # hide cards around a grab
    {"cmd": "rec_show", "monitor": [l, t, w, h], "rect": [fx, fy, fw, fh] | null}
        # recording chrome: border around the area + "Stop" pill with timer
    {"cmd": "rec_hide"}
    {"cmd": "quit"}

The sidecar acknowledges each command with ``{"ok": "<cmd>"}`` and reports
user actions as events::

    {"event": "selection", "req": 7, "screen": {"x", "y", "w", "h", "dpr"},
     "rect": [fx, fy, fw, fh]}                  # or "cancelled": true
    {"event": "card_click", "id": "..."}        # open the editor
    {"event": "card_closed", "id": "..."}       # dismissed with its (x)
    {"event": "stop_clicked"}                   # the recording pill was clicked

It exits on stdin EOF (parent gone) even without ``quit``. Garbled lines are
ignored on both sides.
"""

from __future__ import annotations

import json
from typing import Any

CMD_SELECT = "select"
CMD_CANCEL_SELECT = "cancel_select"
CMD_CARD = "card"
CMD_REMOVE_CARD = "remove_card"
CMD_BLANK = "blank"
CMD_UNBLANK = "unblank"
CMD_REC_SHOW = "rec_show"
CMD_REC_HIDE = "rec_hide"
CMD_QUIT = "quit"

ALL_COMMANDS = frozenset(
    {
        CMD_SELECT,
        CMD_CANCEL_SELECT,
        CMD_CARD,
        CMD_REMOVE_CARD,
        CMD_BLANK,
        CMD_UNBLANK,
        CMD_REC_SHOW,
        CMD_REC_HIDE,
        CMD_QUIT,
    }
)

EVENT_SELECTION = "selection"
EVENT_CARD_CLICK = "card_click"
EVENT_CARD_CLOSED = "card_closed"
EVENT_STOP_CLICKED = "stop_clicked"

ALL_EVENTS = frozenset({EVENT_SELECTION, EVENT_CARD_CLICK, EVENT_CARD_CLOSED, EVENT_STOP_CLICKED})

#: Sidecar exit code when no GUI stack exists (PySide6 missing, no display).
EXIT_NO_GUI = 3


def encode_command(cmd: str, **fields: Any) -> str:
    return json.dumps({"cmd": cmd, **fields}, ensure_ascii=False) + "\n"


def decode_command(line: str) -> dict[str, Any] | None:
    payload = _load(line)
    if payload is None or payload.get("cmd") not in ALL_COMMANDS:
        return None
    return payload


def encode_ack(cmd: str) -> str:
    return json.dumps({"ok": cmd}) + "\n"


def encode_event(event: str, **fields: Any) -> str:
    return json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n"


def decode_reply(line: str) -> tuple[str, dict[str, Any]] | None:
    """``("ack", {"ok": cmd})`` or ``("event", payload)``; ``None`` if neither."""
    payload = _load(line)
    if payload is None:
        return None
    if isinstance(payload.get("ok"), str):
        return "ack", payload
    if payload.get("event") in ALL_EVENTS:
        return "event", payload
    return None


def _load(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):  # Malformed helper messages are rejected, never executed.
        return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "ALL_COMMANDS",
    "ALL_EVENTS",
    "CMD_BLANK",
    "CMD_CANCEL_SELECT",
    "CMD_CARD",
    "CMD_QUIT",
    "CMD_REC_HIDE",
    "CMD_REC_SHOW",
    "CMD_REMOVE_CARD",
    "CMD_SELECT",
    "CMD_UNBLANK",
    "EVENT_CARD_CLICK",
    "EVENT_CARD_CLOSED",
    "EVENT_SELECTION",
    "EVENT_STOP_CLICKED",
    "EXIT_NO_GUI",
    "decode_command",
    "decode_reply",
    "encode_ack",
    "encode_command",
    "encode_event",
]
