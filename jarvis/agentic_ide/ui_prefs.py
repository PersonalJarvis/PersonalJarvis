"""Display preferences of the Agentic-IDE workspace, remembered per machine.

Only ONE thing lives here today — how large the terminal text is — but it earns
a backend store rather than a line of ``localStorage`` for a reason that applies
to every display preference the workspace will ever have: **the desktop window
does not keep browser storage between runs.** It is an embedded WebView started
in private mode, so everything the page writes to ``localStorage`` is thrown
away when the app closes. A preference kept only there is set again after every
single restart, which reads to the user as "the option is gone".

The store is therefore the source of truth and the page's ``localStorage`` is a
first-paint cache: the panes open at the remembered size instead of opening at
the default and visibly resizing a moment later.

Storage is one small JSON file under the per-user data directory (never in the
repo, never in ``jarvis.toml`` — this is a per-screen preference, not
configuration). Writes are atomic (temp file + ``os.replace``), reads are
defensive: a truncated or hand-edited file degrades to the defaults instead of
breaking the workspace, and unknown keys written by a newer version are carried
through untouched so an older one cannot silently drop them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

#: Terminal text size, in pixels. The bounds are not taste — below the floor
#: xterm's glyphs stop being legible on a normal display, above the ceiling a
#: pane holds too few columns for a coding agent's output to stay readable.
#: Mirrored in the grid's toolbar (``AgenticGrid.tsx``) and pinned by
#: ``tests/unit/agentic_ide/test_ui_prefs.py`` so the two cannot drift.
FONT_MIN = 10
FONT_MAX = 20
FONT_DEFAULT = 13

_FONT_KEY = "terminal_font_size"


def _store_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agentic_ide" / "ui_prefs.json"


def _load_raw() -> dict[str, Any]:
    path = _store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:  # A first run has no preferences file by design.
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Agentic IDE: unreadable UI preferences, using defaults: {}", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _save_raw(data: dict[str, Any]) -> bool:
    """Write the whole preference file atomically. False when it could not be."""
    target = _store_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return True
    except OSError as exc:
        logger.warning("Agentic IDE: could not persist UI preferences: {}", exc)
        return False


def clamp_font_size(value: Any) -> int:
    """The nearest usable text size to ``value``, or the default for nonsense.

    Clamped rather than rejected: this is a preference, and a caller that asks
    for 40 (a CLI typo, an older client with wider bounds) is better served with
    the largest size that works than with an error and no change at all. The
    effective value is always returned to the caller, so nothing has to guess
    what was stored.
    """
    try:
        size = int(value)
    except (TypeError, ValueError):  # Invalid stored input falls back to the safe default.
        return FONT_DEFAULT
    return max(FONT_MIN, min(FONT_MAX, size))


def has_terminal_font_size() -> bool:
    """True when a size was actually chosen, as opposed to never set.

    The difference matters exactly once: a workspace that has been used before
    this store existed carries the user's choice in the page's ``localStorage``
    and nowhere else. The UI reads this flag to know it may hand that older
    choice over instead of overwriting it with the default.
    """
    return isinstance(_load_raw().get(_FONT_KEY), (int, float))


def terminal_font_size() -> int:
    """The remembered terminal text size, or the default when none was set."""
    raw = _load_raw().get(_FONT_KEY)
    if not isinstance(raw, (int, float)):
        return FONT_DEFAULT
    return clamp_font_size(raw)


def set_terminal_font_size(value: Any) -> int:
    """Remember a terminal text size. Returns the value that was stored.

    Best-effort against the disk: a workspace must keep working on a read-only
    or full data directory, so a failed write is logged (never silent) and the
    clamped value is still returned — the panes resize now, they just will not
    remember it next time.
    """
    size = clamp_font_size(value)
    data = _load_raw()
    data[_FONT_KEY] = size
    _save_raw(data)
    return size


def reset() -> None:
    """Forget every stored preference. For tests and a clean-slate reinstall."""
    path = _store_path()
    try:
        path.unlink()
    except FileNotFoundError:  # Clearing an already-empty store is idempotent.
        return
    except OSError as exc:
        logger.warning("Agentic IDE: could not clear UI preferences: {}", exc)
