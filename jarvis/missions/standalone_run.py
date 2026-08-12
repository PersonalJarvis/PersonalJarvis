"""The marker that says "this run directory stands on its own".

The Outputs list deliberately hides worktree-slug directories
(``<ts>__<utterance>__<short>``). A worker mission creates two directories —
the temporary worktree it builds in, and the persistent ``mission_<short>``
sibling that carries its identity — and listing both produced the "two agents
for one task" cards reported on 2026-05-26. Hiding the slug directory is the
right call for that case.

It is the wrong call for a directory that has no sibling. An on-demand
visualisation (:mod:`jarvis.visuals.store`) is a complete run in one directory:
nothing is diffed, nothing is reviewed, and there is no mission row anywhere.
Under the old rule it would be written successfully and then never appear —
which is indistinguishable, from the user's side, from not being drawn at all.

So the exception is made explicit rather than inferred. A standalone run drops
this small JSON file at its root; the list treats a directory carrying it as a
first-class run and takes the label from it. A real worktree directory never
has one, so the 2026-05-26 behaviour is untouched.

The alternative considered and rejected: naming the directory
``mission_<hex>`` so the existing branch picks it up. That fakes a mission-id
prefix — on a collision the picture would inherit an unrelated mission's status
and error text, and the name would sort by hex instead of by time, which
silently pushes a fresh picture out of the gallery's newest-runs scan window.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Filename of the marker, at the root of the run directory. Dot-prefixed so it
#: never reads as a deliverable, and outside ``tasks/`` so the artifact
#: allowlist ignores it without needing to know about it.
MARKER_NAME = ".standalone-run.json"


def write_marker(run_dir: Path, *, kind: str, utterance: str = "", title: str = "") -> None:
    """Mark ``run_dir`` as a complete run in its own right.

    Inputs:
        run_dir: the run directory (the ``/api/outputs/{slug}`` one).
        kind: what produced it, e.g. ``"visualization"``. Shown to nobody;
            it exists so a future reader can tell the sources apart.
        utterance: what the user asked for — the label under the tile.
        title: what the run produced, used when there is no utterance.

    Raises:
        OSError: the marker could not be written. Not swallowed: a run that
            silently loses its marker is a run that silently vanishes from the
            list, which is the exact failure this module exists to prevent.
    """
    payload = {"kind": kind, "utterance": utterance, "title": title}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / MARKER_NAME).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def read_marker(run_dir: Path) -> dict[str, Any] | None:
    """Return the marker's contents, or None when this is an ordinary run.

    Never raises: this runs inside the Outputs listing, over directories any
    process may be writing at the same moment. An unreadable or malformed
    marker means "treat it as an ordinary directory" — the listing degrades to
    the old behaviour for that one entry instead of failing for all of them.
    """
    try:
        raw = (run_dir / MARKER_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Unreadable marker degrades to "ordinary directory", per the
        # function's own contract above — nothing else to report.
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.debug("standalone-run marker is not valid JSON: %s", run_dir)
        return None
    return data if isinstance(data, dict) else None


def marker_label(marker: dict[str, Any]) -> str | None:
    """The tile label for a standalone run: what was asked, else what came out."""
    for key in ("utterance", "title"):
        value = marker.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


__all__ = ["MARKER_NAME", "marker_label", "read_marker", "write_marker"]
