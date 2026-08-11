"""Write a rendered visualisation where the app already looks for pictures.

The Visualization section deliberately owns no storage: it reads the run
archive through ``/api/outputs`` and shows the files a window can draw (see
``useVisualArtifacts.ts``). Giving on-demand pictures their own directory and
their own REST route would mean a second archive, a second listing, a second
retention story — and the gallery would still not show them.

So a picture is archived as what it is: a small run that produced one
deliverable. That means matching two conventions exactly, both enforced by the
read side rather than by anything here:

* the run directory name is ``<ts>__<utterance>__<short-hex>``, the shape
  ``outputs_routes._SLUG_RE`` parses to recover the timestamp and the label
  under the tile;
* the file sits at ``tasks/<id>/artifacts/files/<name>``, the only subtree
  ``outputs_routes`` will list or serve — anything else is invisible by design,
  not by accident.

There is no git work here, unlike a worker mission: nothing is diffed and
nothing is reviewed, so a plain directory is the whole requirement.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.paths import repo_root
from jarvis.missions.isolation.worktree import resolve_outputs_root
from jarvis.missions.standalone_run import write_marker

log = logging.getLogger(__name__)

# The task directory every on-demand picture lands in. Worktree-style
# ``NN__label`` so the mission map renders it as "01 · Visualization" instead
# of the anonymous "Step 1" it falls back to for hex names.
TASK_DIR_NAME = "01__visualization"

# Windows still enforces MAX_PATH for many callers, and the archive path is
# already deep (run / tasks / id / artifacts / files / name). Both parts are
# clipped well short of it — the label is decoration, the hex keeps it unique.
_MAX_SLUG_LABEL = 48
_MAX_FILENAME_STEM = 40

_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class StoredVisual:
    """Where the picture ended up, in the terms the read surfaces use."""

    slug: str
    """Run directory name — the ``/api/outputs/{slug}`` path segment."""
    artifact_path: str
    """Posix path inside the run, as ``/artifacts`` lists it."""
    file: Path
    """Absolute path on disk, for "show in folder" and for tests."""


def _slugify(value: str, *, limit: int) -> str:
    out = _NON_SLUG_RE.sub("-", (value or "").lower()).strip("-")
    if len(out) > limit:
        out = out[:limit].rstrip("-")
    return out or "visualization"


def _outputs_root() -> Path:
    """The archive root, resolved the same way every other write path does.

    ``resolve_outputs_root`` already honours ``JARVIS_ISOLATION_ROOT`` and
    ``JARVIS_DATA_DIR``, so a headless or non-root install writes where the
    rest of the app writes — no separate environment contract for pictures.
    """
    return resolve_outputs_root(repo_root())


def store_visual(
    html: str,
    *,
    title: str,
    utterance: str = "",
    outputs_root: Path | None = None,
) -> StoredVisual:
    """Archive one rendered page and return where it landed.

    Inputs:
        html: the complete document from ``render_visual_html``.
        title: the picture's title — the filename stem and the fallback label.
        utterance: what the user asked, used for the run label under the tile.
        outputs_root: override for the archive root (tests).

    Raises:
        OSError: the archive is not writable. Left to the caller: a picture
            that could not be saved is a failed tool call, not a silent
            success with nothing behind the link (AP "no silent except").
    """
    root = Path(outputs_root) if outputs_root is not None else _outputs_root()

    label = _slugify(utterance or title, limit=_MAX_SLUG_LABEL)
    slug = f"{time.strftime('%Y%m%dT%H%M%S')}__{label}__{uuid.uuid4().hex[:8]}"
    filename = f"{_slugify(title, limit=_MAX_FILENAME_STEM)}.html"

    run_dir = root / slug
    directory = run_dir / "tasks" / TASK_DIR_NAME / "artifacts" / "files"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename

    # Without this the run is written correctly and never listed: the Outputs
    # list hides worktree-slug directories, which have a mission_<short>
    # sibling carrying their identity. This one has no sibling and no mission
    # row — the marker is how it says so. See jarvis/missions/standalone_run.py.
    write_marker(run_dir, kind="visualization", utterance=utterance, title=title)

    # Written whole, then moved into place: the gallery polls this tree, and a
    # partially-flushed HTML file would render as a blank frame at exactly the
    # moment the user is watching for the picture to appear.
    staging = target.with_name(f".{filename}.part")
    staging.write_text(html, encoding="utf-8")
    os.replace(staging, target)

    artifact_path = f"tasks/{TASK_DIR_NAME}/artifacts/files/{filename}"
    log.info("visualization_archived slug=%s file=%s", slug, filename)
    return StoredVisual(slug=slug, artifact_path=artifact_path, file=target)


__all__ = ["TASK_DIR_NAME", "StoredVisual", "store_visual"]
