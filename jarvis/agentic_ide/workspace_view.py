"""Which surface the Agentic IDE is showing right now.

The backend has to know, and not for looks: deictic reference depends on it.
"This terminal" is only answerable when exactly one pane fills the screen, and
neither surface does that any more — the grid puts a dozen panes up, and since
2026-08-24 ``chat`` is not a way of reading them at all but the agent chat in
the workspace's folder, with no terminal on screen. So the report is what tells
the voice path to ask for a call-sign instead of guessing.

This module is layer 0 of the five-layer enum pattern
(``docs/anti-drift-three-layer.md``). The value crosses from this package into
a Pydantic model, a REST body, a TypeScript union and a stored browser
preference, and BUG-008 is what happens when one of those learns a value the
others were never told about. Producers import the symbols; nobody spells the
strings.

Why a plain tuple of strings rather than ``enum.StrEnum``: this value is read
on the voice hot path and serialised on every surface report, and the rest of
this package (see :mod:`.task_kind`) already settled on module constants. One
convention beats two.
"""

from __future__ import annotations

#: The wall of terminals — every pane visible at once, sized by dragged seams.
VIEW_GRID = "grid"
#: The agent chat, running in this workspace's folder. No pane is on screen;
#: the terminals stay alive behind it (the browser covers them, never unmounts
#: them) and come back untouched when the grid does.
VIEW_CHAT = "chat"

#: Every value the contract allows, in the order the switch offers them.
WORKSPACE_VIEWS: tuple[str, ...] = (VIEW_GRID, VIEW_CHAT)

#: What an unreadable or absent value falls back to.
#:
#: The grid, deliberately: it is the surface that promises the least. A report
#: that arrives garbled must not be able to put a workspace into a state that
#: answers "this terminal" with a pane the user is not looking at.
VIEW_DEFAULT = VIEW_GRID


def coerce_view(raw: object) -> str:
    """The named view, or :data:`VIEW_DEFAULT` for anything unrecognised.

    Used wherever a view arrives from outside this process — a REST body, a
    resume snapshot, an older frontend bundle. Never raises: a workspace whose
    reading mode could not be understood is still a working workspace, and
    taking a request down over a display preference would be the larger bug.
    """
    return raw if isinstance(raw, str) and raw in WORKSPACE_VIEWS else VIEW_DEFAULT


def view_from_legacy_chat_flag(chat_view: object) -> str:
    """Read the boolean that came before this enum.

    The desktop shell is an embedded WebView holding a bundle that reloads
    itself, so for a few seconds after an update a window can still be posting
    the old ``chat_view: true|false`` body. Those two values map cleanly onto
    the two views.
    """
    return VIEW_CHAT if bool(chat_view) else VIEW_GRID
