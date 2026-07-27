"""Answer a pane's terminal-protocol queries here, where the PTY is.

A coding CLI asks its terminal emulator what it is and which colours it draws
on while it starts — device attributes (``ESC [ c``) and the foreground and
background of the screen (``ESC ] 10;?`` / ``ESC ] 11;?``) — then reads the
answer within milliseconds and gives up. In a web terminal that answer would
otherwise be produced by xterm in the BROWSER, so it travels PTY → WebSocket →
browser → WebSocket → PTY. Across a grid of panes that round trip regularly
outlasts the CLI's own wait, and the reply lands after the prompt editor has
opened, where bytes such as ``ESC ] 11;rgb:1212/1414/1a1a`` show up as a line
of text the user never typed.

Two halves, and both are needed:

* :class:`TerminalQueryResponder` answers in the process that owns the PTY, so
  the reply is written before the CLI can finish waiting. No network is in that
  path, which is the whole point — a faster browser would not have fixed this,
  only made it rarer.
* :func:`is_terminal_report_only` drops the browser's own copy of those replies
  on the way in. Unconditionally, because once the backend answers, a report
  arriving from a browser is by definition a duplicate. An earlier version
  allowed them during a two-second window after a query, which could not work:
  elapsed time cannot distinguish "the CLI is still reading" from "the CLI
  stopped reading a millisecond ago", and a REPLAYED screen (a pane re-joining
  a running agent is handed the raw stream that drew it) makes the browser
  answer a query from minutes ago — corruption arriving on a path no timer
  covers. Nobody types these sequences, so nothing legitimate is lost.

The colours below mirror ``frontend/src/components/agentic/terminalThemes.ts``
and are pinned to it by ``tests/unit/agentic_ide/test_terminal_input.py``: the
CLI must be told the colours the pane actually draws with, or it picks a
palette for the wrong ground.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: What a pane looks like when the client did not say — the app's own default.
DEFAULT_APPEARANCE = "dark"

#: (foreground, background) per appearance, mirroring ``terminalThemes.ts``.
THEME_COLOURS: dict[str, tuple[str, str]] = {
    "dark": ("#e8e8ec", "#12141a"),
    "light": ("#2b2b33", "#fcfbf8"),
}

#: What xterm answers a primary device-attributes query with: a VT100 with the
#: advanced video option. Kept identical to the browser's answer so that moving
#: the reply here changes nothing a CLI can observe except when it arrives.
DEVICE_ATTRIBUTES = "\x1b[?1;2c"

#: Longest query worth remembering across a chunk boundary (``ESC ] 11 ; ? ST``
#: is nine characters); a PTY read can split anywhere.
_TAIL_KEEP = 32

_QUERY_RE = re.compile(
    r"\x1b\](?P<osc>10|11);\?(?P<terminator>\x07|\x1b\\)"
    r"|\x1b\[0?(?P<da>c)"
)

_REPORT_RE = re.compile(
    r"(?:"
    r"\x1b\](?:10|11);rgb:[0-9a-fA-F/]+(?:\x07|\x1b\\)"
    r"|\x1b\[[?=>]?[0-9;]*c"
    r")"
)


def is_terminal_report_only(data: str) -> bool:
    """True only when the entire input consists of known emulator replies."""
    payload = data or ""
    return bool(payload) and not _REPORT_RE.sub("", payload)


def _rgb(colour: str) -> str:
    """``#e8e8ec`` → ``rgb:e8e8/e8e8/ecec`` — the 16-bit form terminals use."""
    raw = colour.lstrip("#").lower()
    return "rgb:" + "/".join(raw[i : i + 2] * 2 for i in (0, 2, 4))


@dataclass(slots=True)
class TerminalQueryResponder:
    """Turn the emulator queries in a PTY's output into replies for that PTY."""

    appearance: str = DEFAULT_APPEARANCE
    _tail: str = field(default="", repr=False)

    def feed(self, data: str) -> str:
        """Return what to write back for the queries contained in ``data``.

        Empty for the overwhelming majority of output. Safe to call on every
        chunk: a query split across two reads is still recognised, and a query
        already answered from the retained tail is never answered twice.
        """
        if not data:
            return ""
        combined = self._tail + data
        replies: list[str] = []
        consumed = 0
        for match in _QUERY_RE.finditer(combined):
            replies.append(self._reply(match))
            consumed = match.end()
        # Keep only what could still be the beginning of a query: everything up
        # to the last match is answered, and older bytes cannot grow into one.
        self._tail = combined[max(consumed, len(combined) - _TAIL_KEEP) :]
        return "".join(replies)

    def _reply(self, match: re.Match[str]) -> str:
        if match.group("da"):
            return DEVICE_ATTRIBUTES
        foreground, background = THEME_COLOURS.get(
            self.appearance, THEME_COLOURS[DEFAULT_APPEARANCE]
        )
        which = match.group("osc")
        colour = foreground if which == "10" else background
        return f"\x1b]{which};{_rgb(colour)}{match.group('terminator')}"


__all__ = [
    "DEFAULT_APPEARANCE",
    "DEVICE_ATTRIBUTES",
    "THEME_COLOURS",
    "TerminalQueryResponder",
    "is_terminal_report_only",
]
