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
# One COMPLETE escape sequence, anchored where a match is tried. Used to answer
# "did this chunk stop in the middle of one?" — see :func:`_ends_mid_escape`.
_ANY_ESC_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_]|[<>=])"
)
# The TAIL of a CSI sequence whose ESC was dropped: parameters, then optional
# intermediates, then the final byte. The leading "[" is optional because the
# split may have fallen either side of it.
_CSI_TAIL_RE = re.compile(r"\[?[0-?]*[ -/]*[@-~]")

#: ``ESC [ ? <params> h|l`` — how a CLI turns terminal features on and off.
_PRIVATE_MODE_RE = re.compile(r"\x1b\[\?([0-9;]+)([hl])")
# A private-mode prefix at the END of the bytes seen so far. PTY reads can cut
# after any byte, including between ``ESC[?10`` and ``00h``.
_PRIVATE_MODE_PARTIAL_RE = re.compile(r"\x1b(?:\[(?:\?(?:[0-9;]*))?)?$")
_PRIVATE_MODE_PARTIAL_MAX = 128

_DECORATION_CHARS = set("─│┌┐└┘├┤┬┴┼━┃╭╮╯╰═║╔╗╚╝╠╣╦╩╬▀▄█▌▐░▒▓▔▁·.-_=*#~ ")
_SPINNER_CHARS = set("|/\\-◐◓◑◒✻✽✢·✳✶⣿")


def strip_ansi(text: str) -> str:
    """Remove terminal escape sequences, leaving printable text."""
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _MISC_RE.sub("", text)
    text = _ESC_RE.sub("", text)
    return "".join(ch for ch in text if ch >= " " or ch in "\t\n\r")


def _ends_mid_escape(text: str) -> bool:
    """Does ``text`` stop INSIDE an escape sequence?

    A PTY read ends wherever the kernel filled its buffer, so an escape can be
    split across two chunks. The last ``ESC`` decides it: if what follows is a
    complete sequence the chunk is self-contained, and if it is not, the rest is
    at the front of the NEXT chunk.
    """
    esc = text.rfind("\x1b")
    return esc >= 0 and _ANY_ESC_RE.match(text, esc) is None


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

    Bounded by total characters, dropping whole chunks from the front — and
    ``truncated`` then says so, because **a cut stream does not repair
    itself**. This buffer used to assume it did ("a TUI repaints itself
    constantly"), and that assumption is what put empty rectangles in two panes
    of a live workspace (reported 2026-07-27). A coding agent built on Ink
    paints its interface ONCE and afterwards rewrites only the row that
    changed, addressed by RELATIVE cursor moves — so a tail that lost its front
    replays the spinner row the agent wrote last onto rows where the prompt box
    it drew twenty minutes ago is simply missing. Nothing later redraws it.

    Whoever replays a truncated buffer therefore has to ask the agent for a
    fresh paint afterwards; :meth:`SessionRegistry.attach` does it with a
    window-size change, the one request every TUI answers with a full redraw.

    **The modes are kept even when the bytes that set them are gone.** A CLI
    negotiates what kind of terminal it is talking to exactly ONCE, in its first
    few hundred bytes — measured against Claude Code 2.1.220: ``ESC[?1049h``
    (take the whole screen) and ``ESC[?1000h ESC[?1002h ESC[?1003h ESC[?1006h``
    (send me the mouse). Those bytes fall out of a 128 KB tail within minutes of
    an agent working, and every viewer that re-joined afterwards built a
    terminal that did not know any of it: xterm stayed on the normal buffer and
    kept the wheel to itself, while the agent went on believing it owned both.
    A wheel turn then scrolled the viewer's own stale buffer — which the agent
    overwrote on its next repaint, since it addresses the screen absolutely — so
    scrolling a Claude Code pane did nothing at all, in the app, forever, while
    a Codex pane (which negotiates neither) was unaffected. Reported four times
    over as "the scrollbar only works in Codex"; the scrollbar was the symptom.

    So every private mode the agent has set is remembered as it goes past, and a
    truncated replay is prefixed with the ones still in force. Cheap — a regex
    over each chunk, a dict of at most a dozen entries — and it puts the viewer
    in the state the agent thinks it is in, which is the whole job of a replay.
    """

    limit: int = REPLAY_LIMIT_CHARS
    _chunks: deque[str] = field(default_factory=deque, init=False)
    _size: int = field(default=0, init=False)
    #: Has anything been dropped since the last :meth:`clear`? Read by the
    #: viewer path to decide whether the replay alone can be trusted.
    truncated: bool = field(default=False, init=False)
    #: Did the last dropped chunk stop inside an escape sequence? Then the rest
    #: of that sequence is at the front of what remains.
    _open_escape: bool = field(default=False, init=False)
    #: Every private mode seen, in the order it was first negotiated, with the
    #: value it was last given. Insertion order matters: a screen switch has to
    #: be replayed before what was drawn on that screen.
    _modes: dict[str, bool] = field(default_factory=dict, init=False)
    #: Incomplete private-mode prefix from the preceding PTY read.
    _mode_scan_tail: str = field(default="", init=False)

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self._note_modes(chunk)
        self._chunks.append(chunk)
        self._size += len(chunk)
        while self._size > self.limit and len(self._chunks) > 1:
            dropped = self._chunks.popleft()
            self._size -= len(dropped)
            self.truncated = True
            self._open_escape = _ends_mid_escape(dropped)

    def _note_modes(self, chunk: str) -> None:
        """Remember what the agent has told the terminal it wants.

        ``ESC[?1000;1006h`` sets several at once, so each parameter is recorded
        on its own. Modes are kept whichever way they were set — a mode the
        agent turned OFF is restored as off, because several of them (wraparound,
        the cursor) are ON in a terminal that was just built. The scan carries
        an incomplete prefix across PTY reads because the kernel may split the
        sequence after any byte.
        """
        scanned = self._mode_scan_tail + chunk
        for params, action in _PRIVATE_MODE_RE.findall(scanned):
            for param in params.split(";"):
                if param:
                    self._modes[param] = action == "h"
        partial = _PRIVATE_MODE_PARTIAL_RE.search(scanned)
        candidate = partial.group(0) if partial is not None else ""
        self._mode_scan_tail = (
            candidate if len(candidate) <= _PRIVATE_MODE_PARTIAL_MAX else ""
        )

    def _mode_prologue(self) -> str:
        """The negotiation a truncated replay lost, re-stated in order."""
        return "".join(f"\x1b[?{mode}{'h' if on else 'l'}" for mode, on in self._modes.items())

    def text(self) -> str:
        """Everything kept, oldest first — ready to be written to a terminal.

        A truncated tail is repaired at its front before it goes out. Dropping
        whole chunks can still leave the CONTINUATION of a sequence there — a
        terminal handed ``[38;5;214m`` with no ``ESC`` prints it as visible text
        in the middle of the agent's interface — so that remnant is skipped, and
        an attribute reset makes the starting colour deterministic rather than
        inherited from bytes that are gone.

        The repair also re-states the modes the agent negotiated (see the class
        docstring): they are set once at startup and are the first thing a long
        tail loses, and a viewer that never heard them is a terminal the agent
        cannot reach with the mouse.
        """
        text = "".join(self._chunks)
        if not self.truncated or not text:
            return text
        if self._open_escape:
            remnant = _CSI_TAIL_RE.match(text)
            if remnant is not None:
                text = text[remnant.end() :]
        return self._mode_prologue() + "\x1b[0m" + text

    def rebase_for_resize(self) -> str:
        """Forget drawing bytes tied to the old geometry, preserving modes.

        Cursor-addressed TUI output only has meaning at the terminal size that
        produced it.  Replaying that stream into a differently sized viewer
        leaves old status rows and fragments between the newly painted ones.
        A resize therefore starts a new replay epoch: the caller sends this
        returned prologue to a reset viewer, then asks the live TUI to repaint.

        Private modes survive because most coding CLIs negotiate alternate
        screen and mouse tracking only once, at process startup.  Dropping
        those together with the stale drawing would repair the text while
        silently breaking the pane's mouse and scrollbar.
        """
        prologue = self._mode_prologue() + "\x1b[0m"
        self._chunks.clear()
        self._size = 0
        self.truncated = False
        self._open_escape = False
        self._chunks.append(prologue)
        self._size = len(prologue)
        return prologue

    def clear(self) -> None:
        """Forget everything — this buffer is being handed to a fresh process.

        Including the remembered modes. They describe what the PREVIOUS agent
        negotiated, and it is gone; the new one states its own within its first
        few hundred bytes, straight into the empty buffer below. Keeping them
        would put a dead process's screen mode at the front of the next
        truncated replay, and a viewer that clears before drawing a replay has
        nothing else to derive its state from.
        """
        self._chunks.clear()
        self._size = 0
        self.truncated = False
        self._open_escape = False
        self._modes.clear()
        self._mode_scan_tail = ""


__all__ = [
    "REPLAY_LIMIT_CHARS",
    "ReplayBuffer",
    "Transcript",
    "is_noise",
    "strip_ansi",
]
