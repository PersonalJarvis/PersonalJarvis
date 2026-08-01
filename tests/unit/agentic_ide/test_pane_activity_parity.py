"""Cross-layer parity guard for the pane activity vocabulary.

The words a pane can be in ("working", "waiting", …) cross four layers: the
Python literal that produces them, the Pydantic field that ships them, the
TypeScript union that types them and the label map that turns each one into
something a person reads. CLAUDE.md §5 exists because a value added to one layer
and forgotten in another is this repo's most-repeated bug — and the failure mode
here is quiet: an unknown word reaches a `Record` lookup, the badge renders
nothing, and a pane silently loses its status.

The panes have no database, so the SQL layer collapses; the four that exist are
all pinned here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from jarvis.agentic_ide.activity import Activity
from jarvis.ui.web.agentic_ide_routes import TerminalRecap

_REPO = Path(__file__).resolve().parents[3]
_FRONTEND = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src"
_API = _FRONTEND / "lib" / "agenticIdeApi.ts"
_PILL = _FRONTEND / "components" / "agentic" / "PaneActivityPill.tsx"


def _ts_activities() -> set[str]:
    text = _API.read_text(encoding="utf-8")
    match = re.search(r"export type PaneActivity\s*=([^;]+);", text)
    assert match is not None, "TypeScript PaneActivity union is missing"
    return set(re.findall(r'"([^"]*)"', match.group(1)))


def _ui_labelled() -> set[str]:
    """Every word the badge can draw, however it is drawn.

    The `LOOK` record covers all but ``waiting``, which is deliberately two
    looks — "done" for a pane that was given a job, "idle" for one that was not
    — and is therefore matched separately rather than being allowed to vanish.
    """
    text = _PILL.read_text(encoding="utf-8")
    match = re.search(r"> = \{(.*?)\n\};", text, flags=re.DOTALL)
    assert match is not None, "PaneActivityPill LOOK map is missing"
    labelled = set(re.findall(r"^\s{2}([a-z]+):", match.group(1), flags=re.MULTILINE))
    assert 'if (activity === "waiting")' in text, "the waiting split is gone"
    return labelled | {"waiting"}


def test_activity_python_typescript_and_ui_parity() -> None:
    python_words = set(get_args(Activity))
    # The empty word is a TypeScript-side addition: "this vocabulary does not
    # describe this pane" (a plain shell). It is not an activity.
    assert _ts_activities() - {""} == python_words
    assert _ui_labelled() == python_words


def test_the_route_ships_the_reading() -> None:
    """A word nothing carries is a word the UI never sees."""
    assert {"activity", "activity_since", "worked"} <= set(TerminalRecap.model_fields)


def test_activity_expected_vocabulary() -> None:
    """Pinned, so widening the vocabulary is a deliberate act with a diff."""
    assert set(get_args(Activity)) == {
        "starting",
        "working",
        "waiting",
        "asking",
        "failed",
        "exited",
    }
