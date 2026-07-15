"""JSON-lines IPC vocabulary between the indicator controller (main
process) and the renderer sidecar.

One JSON object per line on the sidecar's stdin::

    {"cmd": "show", "hint": "Esc to cancel"}   # fade the border in
    {"cmd": "hide"}                            # fade the border out
    {"cmd": "blank"}                           # hide INSTANTLY (capture guard)
    {"cmd": "unblank"}                         # restore after a frame grab
    {"cmd": "quit"}                            # exit the sidecar

The sidecar answers each command with one JSON line on stdout::

    {"ok": "<cmd>"}

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

ALL_COMMANDS = frozenset(
    {CMD_SHOW, CMD_HIDE, CMD_BLANK, CMD_UNBLANK, CMD_QUIT}
)

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
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("cmd") not in ALL_COMMANDS:
        return None
    return payload


def encode_ack(cmd: str) -> str:
    return json.dumps({"ok": cmd}) + "\n"


def decode_ack(line: str) -> str | None:
    """Parse one ack line from the sidecar; ``None`` if it isn't one."""
    try:
        payload = json.loads(line.strip())
    except (ValueError, TypeError):
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
    "CMD_UNBLANK",
    "EXIT_NO_GUI",
    "decode_ack",
    "decode_command",
    "encode_ack",
    "encode_command",
]
