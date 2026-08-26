"""Which way the user is reading the open workspace right now.

The Agentic IDE renders one set of panes two different ways, and the backend
has to know which one is on screen — not for looks, but because deictic
reference depends on it: "this terminal" is only answerable when exactly one
pane fills the screen. Chat view puts one there; the grid puts a dozen, and
guessing among them is less honest than asking for a call-sign.

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
#: One pane on a calm stage, the rest on a rail, read like a conversation.
VIEW_CHAT = "chat"

#: Every value the contract allows, in the order the toolbar offers them.
WORKSPACE_VIEWS: tuple[str, ...] = (VIEW_GRID, VIEW_CHAT)

#: What an unreadable or absent value falls back to.
#:
#: The grid, deliberately: it is the view that promises the least. A surface
#: report that arrives garbled must not be able to switch a workspace into a
#: mode that answers "this terminal" with a pane the user is not looking at.
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
