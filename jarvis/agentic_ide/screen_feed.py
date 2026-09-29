"""Read-only snapshots of pane screens — what the office's monitors show.

The coding floor of the agent office draws every seated coding agent's terminal
on its desk monitor. That picture comes from here: the replayed
:class:`~jarvis.agentic_ide.screen.ScreenBuffer` every pane keeps in its
transcript, copied out as plain rows.

Strictly a READER. Nothing here resizes, attaches to or writes to a PTY — a
monitor is a bystander, and a bystander that nudged the geometry would repaint
the agent's real pane under the person working in it.

Thread safety: the buffer has no lock of its own. Every write to it (the PTY
output callbacks, adoption, resizes) runs on the event loop, so the route that
calls :func:`collect_screens` is ``async`` and copies the rows on that same loop
without awaiting in between — the copy can never interleave with a half-applied
chunk.

Remote panes (an agent running on a connected computer) stream their output
through the same callbacks, so they carry the same local mirror. A pane that
has never produced output simply comes back with no rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

#: Panes per request — the office polls only the handful of monitors near its
#: camera, so anything more is a caller bug, not a bigger office.
MAX_PANES = 8
#: Rows per screen. The BOTTOM rows are kept: that is where an agent's prompt,
#: spinner and newest output live.
MAX_ROWS = 60
#: Characters per row; a monitor texture has no use for more.
MAX_COLS = 240


class _Screen(Protocol):
    cols: int
    rows: int

    def display(self) -> list[str]: ...

    @property
    def visible_cursor(self) -> tuple[int, int] | None: ...


class _Transcript(Protocol):
    @property
    def screen(self) -> _Screen: ...


class _Terminal(Protocol):
    key: str
    name: str
    last_output_at: float | None
    started_at: float | None

    @property
    def transcript(self) -> _Transcript: ...


class _Session(Protocol):
    id: str

    @property
    def terminals(self) -> list[Any]: ...


class _Registry(Protocol):
    def get(self, workspace_id: str | None) -> Any: ...


def parse_pane_refs(values: Iterable[str]) -> list[tuple[str, str]]:
    """``"<workspace_id>:<key>"`` strings as pairs — deduplicated, capped.

    Malformed entries (no colon, an empty half) are dropped rather than
    rejected: an unknown pane is simply omitted from the answer, and a garbled
    one is just a pane nobody knows.
    """
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in values:
        workspace_id, sep, key = (raw or "").strip().partition(":")
        if not sep or not workspace_id or not key:
            continue
        pair = (workspace_id, key)
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
        if len(out) >= MAX_PANES:
            break
    return out


def _find(session: _Session, key: str) -> Any | None:
    """The pane filed under ``key`` — the key first, then its call-sign.

    Exact matches only. ``Session.find`` also resolves spoken phrases, which is
    right for a voice command and wrong for a list row: a near miss would put
    another agent's screen on this agent's monitor.
    """
    for term in session.terminals:
        if term.key == key:
            return term
    folded = key.casefold()
    for term in session.terminals:
        if term.name.casefold() == folded:
            return term
    return None


def pane_screen(workspace_id: str, term: _Terminal) -> dict[str, Any]:
    """One pane's visible screen, trimmed and capped."""
    screen = term.transcript.screen
    rows = screen.display()
    cursor = screen.visible_cursor
    offset = max(0, len(rows) - MAX_ROWS)
    lines = [row[:MAX_COLS] for row in rows[offset:]]
    if cursor is not None:
        row, col = cursor[0] - offset, cursor[1]
        # A cursor outside the rows handed out has no cell to be drawn in.
        cursor = (row, col) if 0 <= row < MAX_ROWS and 0 <= col < MAX_COLS else None
    return {
        "workspace_id": workspace_id,
        "key": term.key,
        "name": term.name,
        "cols": min(screen.cols, MAX_COLS),
        "rows": min(screen.rows, MAX_ROWS),
        "lines": lines,
        "cursor": list(cursor) if cursor is not None else None,
        # Changes exactly when the screen can have: a monitor redraws on it.
        "at": float(term.last_output_at or term.started_at or 0.0),
    }


def collect_screens(registry: _Registry, refs: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
    """Screens for the requested panes, in request order; unknown ones omitted."""
    out: list[dict[str, Any]] = []
    sessions: dict[str, Any | None] = {}
    for workspace_id, key in list(refs)[:MAX_PANES]:
        if workspace_id not in sessions:
            sessions[workspace_id] = registry.get(workspace_id)
        session = sessions[workspace_id]
        if session is None:
            continue
        term = _find(session, key)
        if term is None:
            continue
        out.append(pane_screen(workspace_id, term))
    return out


__all__ = [
    "MAX_COLS",
    "MAX_PANES",
    "MAX_ROWS",
    "collect_screens",
    "pane_screen",
    "parse_pane_refs",
]
