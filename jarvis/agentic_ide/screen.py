"""A minimal terminal screen emulator — enough of VT100/ANSI to read a TUI.

Why this exists: a PTY does not carry lines of text, it carries drawing
commands. Claude Code and Codex paint full-screen interfaces by moving the
cursor to absolute positions, erasing regions, and repainting rows in place.
Stripping the escape sequences out of that stream and keeping the leftovers
produces text in an order no human ever read — measured on a real Claude Code
startup: 872 characters of output collapsed into a single line.

So instead of filtering the stream, this replays it onto a grid, the way the
terminal itself would, and then the visible rows are simply read off. That is
what makes "what is Mika doing?" answerable: the model sees what the user sees.

Scope is deliberately narrow — the sequences TUIs actually use for layout:

* printable text with line wrapping,
* CR / LF / BS / TAB,
* cursor positioning (CUP, HVP) and relative moves (CUU, CUD, CUF, CUB, CHA),
* erase in line (EL) and erase in display (ED),
* index / reverse index (IND, RI, NEL) and scroll up / down (SU, SD),
* a scroll region (DECSTBM), because status bars depend on it.

Everything else — colours (SGR), bracketed paste, mouse reporting, alternate
screen switches — is parsed and ignored, which is correct: it changes
appearance, not content. Cursor visibility is the one appearance bit retained:
coding TUIs use it to distinguish a writable composer from a disabled one.
Unknown sequences are skipped rather than printed, so a future escape code
cannot leak garbage into the transcript.

Not a goal: a conformant emulator. There is no support for double-width glyphs,
character sets, or wide-character alignment. A row is a list of single-cell
strings; a CJK glyph occupies one cell instead of two, which can shift a box
border by a column. For reading what an agent is doing that is irrelevant, and
it keeps the module dependency-free — the base install stays universal.
"""
from __future__ import annotations

from collections import deque

# Bounds that keep a runaway resize or a hostile escape sequence from
# allocating an unreasonable grid.
MIN_COLS, MAX_COLS = 20, 500
MIN_ROWS, MAX_ROWS = 5, 200

_SPACE = " "


def _clamp(value: int, low: int, high: int) -> int:
    return low if value < low else high if value > high else value


class ScreenBuffer:
    """A character grid plus a cursor, driven by a PTY byte stream."""

    __slots__ = (
        "cols",
        "rows",
        "_grid",
        "_cursor_row",
        "_cursor_col",
        "_cursor_visible",
        "_scrollback",
        "_pending",
        "_scroll_top",
        "_scroll_bottom",
        "_wrap_pending",
    )

    def __init__(self, cols: int = 100, rows: int = 30, scrollback: int = 800) -> None:
        self.cols = _clamp(cols, MIN_COLS, MAX_COLS)
        self.rows = _clamp(rows, MIN_ROWS, MAX_ROWS)
        self._grid: list[list[str]] = [self._blank_row() for _ in range(self.rows)]
        self._cursor_row = 0
        self._cursor_col = 0
        self._cursor_visible = True
        self._scrollback: deque[str] = deque(maxlen=scrollback)
        # Bytes of an escape sequence split across two PTY reads.
        self._pending = ""
        self._scroll_top = 0
        self._scroll_bottom = self.rows - 1
        self._wrap_pending = False

    # ------------------------------------------------------------------ grid
    def _blank_row(self) -> list[str]:
        return [_SPACE] * self.cols

    def resize(self, cols: int, rows: int) -> None:
        """Resize the grid, keeping the top-left content (the usual TUI reflow)."""
        cols = _clamp(cols, MIN_COLS, MAX_COLS)
        rows = _clamp(rows, MIN_ROWS, MAX_ROWS)
        if cols == self.cols and rows == self.rows:
            return
        old = self._grid
        self.cols, self.rows = cols, rows
        self._grid = [self._blank_row() for _ in range(rows)]
        for r in range(min(rows, len(old))):
            row = old[r]
            for c in range(min(cols, len(row))):
                self._grid[r][c] = row[c]
        self._cursor_row = min(self._cursor_row, rows - 1)
        self._cursor_col = min(self._cursor_col, cols - 1)
        self._scroll_top = 0
        self._scroll_bottom = rows - 1

    def _scroll_up(self, count: int = 1) -> None:
        """Push rows off the top of the scroll region into the scrollback."""
        for _ in range(max(1, count)):
            leaving = self._grid.pop(self._scroll_top)
            # Only the true top of the screen is history; a scroll region below
            # it (a status bar) is transient decoration.
            if self._scroll_top == 0:
                self._scrollback.append("".join(leaving).rstrip())
            self._grid.insert(self._scroll_bottom, self._blank_row())

    def _scroll_down(self, count: int = 1) -> None:
        for _ in range(max(1, count)):
            self._grid.pop(self._scroll_bottom)
            self._grid.insert(self._scroll_top, self._blank_row())

    def _newline(self) -> None:
        if self._cursor_row >= self._scroll_bottom:
            self._scroll_up()
            self._cursor_row = self._scroll_bottom
        else:
            self._cursor_row += 1

    def _put(self, ch: str) -> None:
        if self._wrap_pending:
            self._cursor_col = 0
            self._newline()
            self._wrap_pending = False
        if self._cursor_col >= self.cols:
            self._cursor_col = 0
            self._newline()
        self._grid[self._cursor_row][self._cursor_col] = ch
        if self._cursor_col + 1 >= self.cols:
            # Defer the wrap: a character in the last column does not move to
            # the next row until one more character arrives (VT behaviour, and
            # it stops full-width box borders from inserting blank rows).
            self._wrap_pending = True
        else:
            self._cursor_col += 1

    # ------------------------------------------------------------------ feed
    def feed(self, chunk: str) -> None:
        """Replay a raw PTY chunk onto the grid."""
        if not chunk:
            return
        data = self._pending + chunk
        self._pending = ""
        i, n = 0, len(data)
        while i < n:
            ch = data[i]
            if ch == "\x1b":
                consumed = self._handle_escape(data, i)
                if consumed == 0:
                    # Sequence is cut off at the chunk boundary — keep the tail
                    # and finish it when the next read arrives.
                    self._pending = data[i:]
                    return
                i += consumed
                continue
            i += 1
            if ch == "\n":
                self._wrap_pending = False
                self._newline()
            elif ch == "\r":
                self._wrap_pending = False
                self._cursor_col = 0
            elif ch == "\b":
                self._wrap_pending = False
                self._cursor_col = max(0, self._cursor_col - 1)
            elif ch == "\t":
                self._wrap_pending = False
                self._cursor_col = min(self.cols - 1, (self._cursor_col // 8 + 1) * 8)
            elif ch == "\x07":
                continue  # bell
            elif ch < " " or ch == "\x7f":
                continue  # other C0 controls carry no content
            else:
                self._put(ch)

    def _handle_escape(self, data: str, start: int) -> int:
        """Consume one escape sequence. Returns its length, or 0 if incomplete."""
        n = len(data)
        if start + 1 >= n:
            return 0
        kind = data[start + 1]

        if kind == "[":
            return self._handle_csi(data, start)
        if kind == "]":
            # OSC: ends at BEL or ST (ESC \). Title changes, hyperlinks.
            j = start + 2
            while j < n:
                if data[j] == "\x07":
                    return j - start + 1
                if data[j] == "\x1b" and j + 1 < n and data[j + 1] == "\\":
                    return j - start + 2
                if data[j] == "\x1b":
                    return 0 if j + 1 >= n else j - start + 1
                j += 1
            return 0
        if kind in "()#%":
            # Character-set selection: two bytes of payload, ignored.
            return 3 if start + 2 < n else 0
        if kind == "D":  # IND — index (down one row)
            self._newline()
            return 2
        if kind == "M":  # RI — reverse index (up one row)
            if self._cursor_row <= self._scroll_top:
                self._scroll_down()
            else:
                self._cursor_row -= 1
            return 2
        if kind == "E":  # NEL — next line
            self._cursor_col = 0
            self._newline()
            return 2
        if kind == "c":  # RIS — full reset
            self.reset()
            return 2
        # Anything else (7/8 save-restore cursor, =/>, ...) — skip the two bytes.
        return 2

    def _handle_csi(self, data: str, start: int) -> int:
        n = len(data)
        j = start + 2
        params_start = j
        # Parameter bytes 0x30-0x3F, then intermediates 0x20-0x2F, then final.
        while j < n and "\x30" <= data[j] <= "\x3f":
            j += 1
        while j < n and "\x20" <= data[j] <= "\x2f":
            j += 1
        if j >= n:
            return 0  # incomplete — wait for more input
        final = data[j]
        raw = data[params_start:j]
        length = j - start + 1

        private = raw.startswith("?")
        if private:
            # Cursor visibility is semantic for prompt delivery: Ratatui hides
            # it when a composer's input is disabled and puts it back on the
            # input row only when key events can be consumed. Other private
            # modes affect presentation or input routing, not screen content.
            if final in "hl" and "25" in raw[1:].split(";"):
                self._cursor_visible = final == "h"
            return length

        params = [int(p) if p.isdigit() else 0 for p in raw.split(";")] if raw else []

        def p(index: int, default: int = 1) -> int:
            if index < len(params) and params[index] > 0:
                return params[index]
            return default

        self._wrap_pending = False

        if final in "Hf":  # CUP / HVP — absolute position
            self._cursor_row = _clamp(p(0) - 1, 0, self.rows - 1)
            self._cursor_col = _clamp(p(1) - 1, 0, self.cols - 1)
        elif final == "A":  # CUU
            self._cursor_row = max(0, self._cursor_row - p(0))
        elif final == "B":  # CUD
            self._cursor_row = min(self.rows - 1, self._cursor_row + p(0))
        elif final == "C":  # CUF
            self._cursor_col = min(self.cols - 1, self._cursor_col + p(0))
        elif final == "D":  # CUB
            self._cursor_col = max(0, self._cursor_col - p(0))
        elif final == "G":  # CHA — absolute column
            self._cursor_col = _clamp(p(0) - 1, 0, self.cols - 1)
        elif final == "d":  # VPA — absolute row
            self._cursor_row = _clamp(p(0) - 1, 0, self.rows - 1)
        elif final == "E":  # CNL
            self._cursor_col = 0
            self._cursor_row = min(self.rows - 1, self._cursor_row + p(0))
        elif final == "F":  # CPL
            self._cursor_col = 0
            self._cursor_row = max(0, self._cursor_row - p(0))
        elif final == "K":  # EL — erase in line
            mode = params[0] if params else 0
            row = self._grid[self._cursor_row]
            if mode == 0:
                for c in range(self._cursor_col, self.cols):
                    row[c] = _SPACE
            elif mode == 1:
                for c in range(0, min(self._cursor_col + 1, self.cols)):
                    row[c] = _SPACE
            else:
                self._grid[self._cursor_row] = self._blank_row()
        elif final == "J":  # ED — erase in display
            mode = params[0] if params else 0
            if mode == 0:
                for c in range(self._cursor_col, self.cols):
                    self._grid[self._cursor_row][c] = _SPACE
                for r in range(self._cursor_row + 1, self.rows):
                    self._grid[r] = self._blank_row()
            elif mode == 1:
                for c in range(0, min(self._cursor_col + 1, self.cols)):
                    self._grid[self._cursor_row][c] = _SPACE
                for r in range(0, self._cursor_row):
                    self._grid[r] = self._blank_row()
            else:
                # Clearing the whole screen: the rows that held content are
                # history, otherwise a repainting TUI would erase its own past.
                for row in self._grid:
                    text = "".join(row).rstrip()
                    if text:
                        self._scrollback.append(text)
                self._grid = [self._blank_row() for _ in range(self.rows)]
                if mode == 2:
                    self._cursor_row = self._cursor_col = 0
        elif final == "L":  # IL — insert lines
            for _ in range(p(0)):
                self._grid.pop(self._scroll_bottom)
                self._grid.insert(self._cursor_row, self._blank_row())
        elif final == "M":  # DL — delete lines
            for _ in range(p(0)):
                self._grid.pop(self._cursor_row)
                self._grid.insert(self._scroll_bottom, self._blank_row())
        elif final == "P":  # DCH — delete characters
            row = self._grid[self._cursor_row]
            for _ in range(p(0)):
                if self._cursor_col < self.cols:
                    del row[self._cursor_col]
                    row.append(_SPACE)
        elif final == "@":  # ICH — insert characters
            row = self._grid[self._cursor_row]
            for _ in range(p(0)):
                row.insert(self._cursor_col, _SPACE)
                row.pop()
        elif final == "X":  # ECH — erase characters
            row = self._grid[self._cursor_row]
            for c in range(self._cursor_col, min(self._cursor_col + p(0), self.cols)):
                row[c] = _SPACE
        elif final == "S":  # SU — scroll up
            self._scroll_up(p(0))
        elif final == "T":  # SD — scroll down
            self._scroll_down(p(0))
        elif final == "r":  # DECSTBM — scroll region
            top = _clamp(p(0) - 1, 0, self.rows - 1)
            bottom = _clamp(p(1, self.rows) - 1, 0, self.rows - 1)
            if top < bottom:
                self._scroll_top, self._scroll_bottom = top, bottom
            else:
                self._scroll_top, self._scroll_bottom = 0, self.rows - 1
            self._cursor_row, self._cursor_col = self._scroll_top, 0
        # SGR ("m") and everything else: appearance only.
        return length

    # ---------------------------------------------------------------- output
    def display(self) -> list[str]:
        """Visible rows, trailing blanks removed."""
        rows = ["".join(row).rstrip() for row in self._grid]
        while rows and not rows[-1]:
            rows.pop()
        return rows

    @property
    def visible_cursor(self) -> tuple[int, int] | None:
        """Current ``(row, column)`` when the application exposes its cursor."""
        if not self._cursor_visible:
            return None
        return self._cursor_row, self._cursor_col

    def row_text(self, row: int) -> str:
        """One visible grid row, or an empty string outside the screen."""
        if row < 0 or row >= self.rows:
            return ""
        return "".join(self._grid[row]).rstrip()

    def history(self) -> list[str]:
        """Rows that have scrolled off the top, oldest first."""
        return list(self._scrollback)

    def reset(self) -> None:
        self._grid = [self._blank_row() for _ in range(self.rows)]
        self._cursor_row = self._cursor_col = 0
        self._cursor_visible = True
        self._scrollback.clear()
        self._pending = ""
        self._scroll_top = 0
        self._scroll_bottom = self.rows - 1
        self._wrap_pending = False


__all__ = ["ScreenBuffer"]
