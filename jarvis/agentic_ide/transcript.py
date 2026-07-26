"""Readable transcript of what a coding agent is doing in its terminal.

The problem: a PTY carries a *terminal protocol*, not a log. Claude Code and
Codex are full-screen TUIs — they position the cursor absolutely, erase regions,
and repaint rows in place. Handing that stream to a language model verbatim
wastes context on control codes, and merely filtering the escape sequences out
is worse than useless: the leftovers arrive in an order nobody read. Measured on
a real Claude Code startup, 872 characters of output reduced to one usable line.

So a terminal screen is replayed instead (:mod:`.screen`), and the transcript is
read off it in two parts:

* **history** — rows that scrolled off the top, i.e. what already happened,
* **display** — the rows currently on screen, i.e. what is happening now.

On top of that sits one light editorial pass for the benefit of a model reading
it: blank and decoration-only rows are dropped, and consecutive identical rows
are folded (a repainted spinner row is one event, not two hundred).

Honest limits: no double-width glyph handling, so a CJK character can shift a
box border by a column; and a two-column TUI layout still reads left-to-right
per row, exactly as it looks on screen. Both are irrelevant for answering "what
is this agent working on?", and both keep the module dependency-free.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from .screen import ScreenBuffer

# Kept for callers that want plain text out of a coloured string (the prompt
# sanitizer uses it). Screen replay does NOT go through this.
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC_RE = re.compile(r"\x1b[@-Z\\-_]")
_MISC_RE = re.compile(r"\x1b[<>=]")

# A row made only of these draws a frame or an animation; it carries no
# information for a model summarizing the session.
_DECORATION_CHARS = set("─│┌┐└┘├┤┬┴┼━┃╭╮╯╰═║╔╗╚╝╠╣╦╩╬▀▄█▌▐░▒▓▔▁·.-_=*#~ ")
_SPINNER_CHARS = set("|/\\-◐◓◑◒✻✽✢·✳✶⣿")


def strip_ansi(text: str) -> str:
    """Remove terminal escape sequences, leaving printable text."""
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _MISC_RE.sub("", text)
    text = _ESC_RE.sub("", text)
    return "".join(ch for ch in text if ch >= " " or ch in "\t\n\r")


def _is_decorative(ch: str) -> bool:
    """True for a glyph that only ever draws a frame or an animation.

    Braille patterns (U+2800..U+28FF) get a range check rather than a character
    list: every modern CLI spinner cycles through dozens of them, and listing
    the ones a given agent uses today would go stale when it changes its
    spinner.
    """
    if ch in _DECORATION_CHARS or ch in _SPINNER_CHARS:
        return True
    return 0x2800 <= ord(ch) <= 0x28FF


def is_noise(line: str) -> bool:
    """True for a line that carries no information worth keeping."""
    stripped = line.strip()
    if not stripped:
        return True
    return all(_is_decorative(ch) for ch in stripped)


@dataclass(slots=True)
class Transcript:
    """Readable view of one terminal, backed by a replayed screen."""

    cols: int = 100
    rows: int = 30
    max_lines: int = 600
    # Characters of raw PTY output seen — a cheap "is this agent doing
    # anything?" signal that survives every row being filtered as noise.
    raw_chars: int = 0
    screen: ScreenBuffer = field(init=False)

    def __post_init__(self) -> None:
        self.screen = ScreenBuffer(self.cols, self.rows, scrollback=self.max_lines)

    # ------------------------------------------------------------------ input
    def feed(self, chunk: str) -> None:
        """Replay a raw PTY chunk (escape sequences and all)."""
        if not chunk:
            return
        self.raw_chars += len(chunk)
        self.screen.feed(chunk)

    def resize(self, cols: int, rows: int) -> None:
        """Follow the pane's size so the replayed screen matches the real one."""
        self.cols, self.rows = cols, rows
        self.screen.resize(cols, rows)

    # ----------------------------------------------------------------- output
    def lines(self) -> list[str]:
        """History plus the current screen, cleaned and de-duplicated."""
        out: list[str] = []
        for line in [*self.screen.history(), *self.screen.display()]:
            text = line.rstrip()
            if is_noise(text):
                continue
            if out and out[-1] == text:
                continue  # a repainted, unchanged row
            out.append(text)
        return out

    def tail(self, count: int = 40) -> list[str]:
        """Last ``count`` meaningful lines, oldest first."""
        if count <= 0:
            return []
        return self.lines()[-count:]

    def clear(self) -> None:
        self.screen.reset()
        self.raw_chars = 0


"""How much raw PTY output a pane keeps for its next viewer.

Enough to cover several full repaints of a coding agent's TUI, so a pane that
is looked at again shows what it was showing rather than a black rectangle —
and small enough that a dozen panes across a handful of workspaces stay a
rounding error in memory (12 panes x 6 workspaces x 128 KB is under 10 MB).
"""
REPLAY_LIMIT_CHARS = 128 * 1024


@dataclass(slots=True)
class ReplayBuffer:
    """The tail of a terminal's RAW output, kept for a viewer that comes back.

    Separate from :class:`Transcript` because the two answer different
    questions. The transcript is for a *reader*: escape sequences replayed onto
    a screen and turned into readable rows. This is for a *terminal*: the exact
    bytes, in order, so xterm on the other side can be handed them verbatim and
    end up in the state the pane was in.

    That distinction is load-bearing for switching workspaces. The agent keeps
    running while nobody watches, so reconnecting has to reconstruct the screen
    from something — and the only thing that reconstructs a full-screen TUI is
    the stream that drew it. Cleaned-up text would come back as a wall of
    prose where a framed, coloured interface used to be.

    Bounded by total characters, dropping whole chunks from the front. A TUI
    repaints itself constantly, so an old partial escape sequence at the very
    front is overwritten within a frame; cutting mid-sequence instead would
    leave the terminal in a broken colour/cursor state.
    """

    limit: int = REPLAY_LIMIT_CHARS
    _chunks: deque[str] = field(default_factory=deque, init=False)
    _size: int = field(default=0, init=False)

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self._chunks.append(chunk)
        self._size += len(chunk)
        while self._size > self.limit and len(self._chunks) > 1:
            self._size -= len(self._chunks.popleft())

    def text(self) -> str:
        """Everything kept, oldest first — ready to be written to a terminal."""
        return "".join(self._chunks)

    def clear(self) -> None:
        self._chunks.clear()
        self._size = 0


__all__ = [
    "REPLAY_LIMIT_CHARS",
    "ReplayBuffer",
    "Transcript",
    "is_noise",
    "strip_ansi",
]
