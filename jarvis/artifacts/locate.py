"""Find an existing artifact in the run archive, for a revision.

"Make the bars red" only makes sense against the page it talks about. The
brain cannot name that page by path — it never sees the archive — so the tool
accepts a TITLE (or "latest") and this module resolves it against the HTML
deliverables of the most recent runs. Bounded on purpose: the scan reads the
newest :data:`MAX_RUNS_SCANNED` run directories and the first few kilobytes of
each page for its ``<title>``; an archive with three hundred runs does not make
a revision request cost three hundred file reads.

Read-only, and honest about misses: ``None`` means "no such artifact among the
recent runs", and the tool turns that into an error the model can act on
(build it fresh, or ask which one).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.paths import repo_root
from jarvis.missions.isolation.worktree import resolve_readable_outputs_roots

log = logging.getLogger(__name__)

#: Newest run directories looked at per archive root.
MAX_RUNS_SCANNED = 40
#: Bytes read from a page to find its ``<title>``.
_TITLE_PROBE_BYTES = 8192
#: A revision brief rides the whole page; anything larger is not an artifact
#: this feature produced and is left alone.
_MAX_ARTIFACT_BYTES = 2_000_000

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_HTML_SUFFIXES = {".html", ".htm"}


@dataclass(frozen=True)
class LocatedArtifact:
    """One existing page, with what a revision brief needs from it."""

    slug: str
    """Run directory name — the ``/api/outputs/{slug}`` path segment."""
    artifact_path: str
    """Posix path inside the run, as ``/artifacts`` lists it."""
    file: Path
    """Absolute path on disk."""
    title: str
    """The page's ``<title>``, or its filename stem when it has none."""
    html: str
    """The page's full text."""


def _normalize(text: str) -> str:
    return _NON_WORD_RE.sub(" ", (text or "").lower()).strip()


def _page_title(file: Path) -> str:
    try:
        with file.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(_TITLE_PROBE_BYTES)
    except OSError as exc:
        # An unreadable page keeps its filename as the label — the listing
        # must not fail over one locked file; the read is retried on open.
        log.debug("artifact title probe failed %s: %s", file, exc)
        return file.stem
    match = _TITLE_RE.search(head)
    if match is None:
        return file.stem
    title = " ".join(match.group(1).split())
    return title or file.stem


def _candidate_pages(root: Path) -> list[tuple[float, Path, Path]]:
    """``(mtime, run_dir, page)`` for every HTML deliverable of the newest runs."""
    try:
        run_dirs = [entry for entry in root.iterdir() if entry.is_dir()]
    except OSError as exc:
        # A root that vanished or is unreadable contributes nothing; the other
        # roots are still scanned and the miss is reported as "no match".
        log.debug("artifact scan cannot list %s: %s", root, exc)
        return []
    # Newest first by directory mtime — the same order the Outputs rail uses.
    run_dirs.sort(key=lambda d: d.stat().st_mtime if d.exists() else 0.0, reverse=True)
    pages: list[tuple[float, Path, Path]] = []
    for run_dir in run_dirs[:MAX_RUNS_SCANNED]:
        try:
            for page in run_dir.glob("tasks/*/artifacts/files/**/*"):
                if page.suffix.lower() not in _HTML_SUFFIXES or not page.is_file():
                    continue
                stat = page.stat()
                if stat.st_size > _MAX_ARTIFACT_BYTES:
                    continue
                pages.append((stat.st_mtime, run_dir, page))
        except OSError as exc:
            log.debug("artifact scan skipped %s: %s", run_dir, exc)
    return pages


def locate_artifact(
    query: str, *, outputs_roots: tuple[Path, ...] | None = None
) -> LocatedArtifact | None:
    """The newest recent artifact matching ``query``, or None.

    ``query`` is ``"latest"`` for the most recent page of any title, otherwise
    a title (or part of one) compared case- and punctuation-insensitively
    against each page's ``<title>`` and filename stem.
    """
    roots = (
        outputs_roots if outputs_roots is not None else resolve_readable_outputs_roots(repo_root())
    )
    wanted = _normalize(query)
    if not wanted:
        return None

    pages: list[tuple[float, Path, Path]] = []
    for root in roots:
        if root.is_dir():
            pages.extend(_candidate_pages(root))
    pages.sort(key=lambda item: item[0], reverse=True)

    for _mtime, run_dir, page in pages:
        title = _page_title(page)
        if wanted != "latest":
            haystack = f"{_normalize(title)} {_normalize(page.stem)}"
            if wanted not in haystack:
                continue
        try:
            html = page.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.debug("artifact unreadable %s: %s", page, exc)
            continue
        return LocatedArtifact(
            slug=run_dir.name,
            artifact_path=page.relative_to(run_dir).as_posix(),
            file=page,
            title=title,
            html=html,
        )
    return None


__all__ = ["MAX_RUNS_SCANNED", "LocatedArtifact", "locate_artifact"]
