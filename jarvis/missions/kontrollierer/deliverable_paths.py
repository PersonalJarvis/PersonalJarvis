"""Single source of truth for "is this archive path a genuine deliverable?".

A worker runs in a fresh ``git worktree``; the Kontrollierer archives its
outputs into ``<mission_dir>/tasks/<id>/artifacts/files/<rel>`` by enumerating
untracked files (``git ls-files --others``) AND — since the 2026-05-27 hardening
audit — gitignored files (``git ls-files --others --ignored``, the "union" that
captures deliverables a worker named with an ignored pattern such as
``output.log`` or anything under ``dist/``). That union is deliberately broad,
so a denylist must keep tool-scratch out of ``artifacts/files/``.

Three layers consume this predicate, so it lives in ONE module to stop the
multi-layer drift bug class (CLAUDE.md / BUG-008):

  * the archive filter — :func:`Kontrollierer._archive_task_artifacts`
    (``orchestrator.py``) — keeps scratch out of the archive at the source;
  * the Outputs-view listing + download/raw/view guards
    (``ui/web/outputs_routes.py``) — hides scratch already on disk from
    pre-fix missions and any other archive path;
  * the user-folder mirror + voice readback
    (``deliverable.py``) — keeps the user's Downloads and the spoken
    "N files saved" count free of scratch.

Live forensic 2026-06-21 (mission_019eeb34-bb67): a browser/QA worker launched
four headless Chrome instances with profiles under
``qa-artifacts/chrome-profile-<hex>/`` and gitignored them (``chrome-profile-*/``).
The ``--ignored`` union re-imported all 199 cache / shader / journal blobs
(68 of them 0-byte) into ``artifacts/files/`` next to the 2 real deliverables;
the Outputs view (cap 200, sort-by-mtime) then buried the real files under the
fresher junk — the user saw "the files aren't shown" and "empty files". The
denylist below recognises browser user-data directories so the union can keep
capturing real ignored deliverables WITHOUT re-importing browser scratch.

Pure stdlib, pure-string, cross-platform (normalises ``\\`` to ``/``). It can
only ever REMOVE recognised scratch from a listing — never a real deliverable,
as long as the patterns stay tight (guarded by ``test_deliverable_paths.py``).
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Sequence
from typing import Final

# Directory-segment names whose contents are never worker deliverables: git
# internals, the materialized OpenClaw state, language/build dep caches, and
# browser-engine cache/state subdirectories. NOTE: build *output* dirs
# (``dist/``, ``build/``, ``.next/``) are intentionally NOT here — the
# ``--ignored`` union exists precisely to capture deliverables a worker emits
# there. Only add a name when it is unmistakably tool-scratch, never a result.
_JUNK_DIR_NAMES: Final[frozenset[str]] = frozenset({
    # git / project scaffolding + dependency / language caches (pre-2026-06-21)
    ".git",
    ".openclaw",
    "openclaw_state",
    "node_modules",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    # Chromium / Chrome / Edge user-data-dir cache + state subdirectories.
    # A browser/QA worker (the browser-ui-checkup skill, Playwright, Puppeteer,
    # Selenium) launches a real browser with a ``--user-data-dir`` inside the
    # worktree; the engine writes hundreds of these. The names carry spaces /
    # camel-case that essentially never name a real deliverable, so excluding
    # them catches a browser profile even when its root dir is named
    # unconventionally (defence-in-depth behind _BROWSER_PROFILE_SEG_RE).
    "GrShaderCache",
    "GPUCache",
    "ShaderCache",
    "Code Cache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GraphiteDawnCache",
    "Crashpad",
    "Crash Reports",
    "Service Worker",
    "Shared Dictionary",
    "Subresource Filter",
    "Safe Browsing",
    "component_crx_cache",
    "extensions_crx_cache",
    "segmentation_platform",
    "BrowserMetrics",
})


# A path SEGMENT that is a browser user-data / automation profile ROOT. Matching
# the root catches the WHOLE profile subtree — including the top-level files
# (``Local State``, ``Last Browser``, ``Variations``, ``Preferences``,
# ``First Run``, ``*.db-journal``) that carry no cache-dir segment and would
# otherwise leak past the _JUNK_DIR_NAMES check. The worker's own ``.gitignore``
# in the live forensic used exactly ``chrome-profile-*/``.
_BROWSER_PROFILE_SEG_RE: Final[re.Pattern[str]] = re.compile(
    r"""^(
        # <engine>[-_.]?(profile|user-data|userdata)[-_.suffix]  →  chrome-profile-<hex>
        (?:chrome|chromium|edge|msedge|brave|opera|vivaldi|webkit|firefox)
            [._-]?(?:profile|user-?data)(?:[._-].*)?
        # explicit "...user-data-dir..." anywhere in the segment
      | .*[._-]user-data-dir.*
        # automation-harness temp profiles
      | puppeteer[._-]dev[._-]chrome[._-]profile.*
      | playwright[._-].*profile.*
      | selenium[._-].*profile.*
        # Chrome's own scratch profile dir (scoped_dir<pid>_<n>) — requires a
        # trailing digit so it can't swallow a real "scoped_directory/".
      | scoped_dir[0-9].*
        # macOS temp profile bundles
      | \.(?:org\.chromium\.Chromium|com\.google\.Chrome|com\.microsoft\.Edge)\..*
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


def is_browser_scratch_segment(segment: str) -> bool:
    """True iff *segment* is a browser user-data / automation profile root dir."""
    return bool(_BROWSER_PROFILE_SEG_RE.match(segment))


def is_nondeliverable_scratch(rel: str) -> bool:
    """True iff a path is internal tool-scratch, not a genuine deliverable.

    Cross-platform: backslashes are normalised to ``/`` and leading/trailing
    slashes are stripped before the per-segment checks. An empty / root path is
    NOT scratch (returns False) — the caller decides what to do with it.

    A path is scratch iff ANY of its segments is a known junk directory
    (:data:`_JUNK_DIR_NAMES`) or a browser-profile root
    (:func:`is_browser_scratch_segment`).
    """
    norm = rel.replace("\\", "/").strip("/")
    if not norm:
        return False
    for seg in norm.split("/"):
        if not seg or seg == ".":
            continue
        if seg in _JUNK_DIR_NAMES:
            return True
        if is_browser_scratch_segment(seg):
            return True
    return False


def is_deliverable_path(rel: str, *, managed_files: frozenset[str] = frozenset()) -> bool:
    """True iff a worktree-relative path is a genuine worker deliverable.

    False for an empty path, for a managed worker-contract file (``AGENTS.md``
    etc. — pass the caller's set via *managed_files*), and for any tool-scratch
    path (:func:`is_nondeliverable_scratch`). This is the full archive-side
    predicate; the read-side layers use :func:`is_nondeliverable_scratch`
    directly (managed contract files never reach ``artifacts/files/``).
    """
    norm = rel.replace("\\", "/").strip("/")
    if not norm:
        return False
    if norm in managed_files:
        return False
    return not is_nondeliverable_scratch(norm)


# --- Generator / build-script detection (content-aware, cross-file) -----------
# The predicates above are pure path-string filters. The check below is a
# different shape: it reads file CONTENT and looks ACROSS the deliverable set to
# spot a script whose only purpose is to emit ANOTHER deliverable.
#
# Live forensic 2026-06-22 (mission_019ef099): the user asked by voice for "one
# HTML file" and the worker shipped city_guide.html PLUS generate_guide.py
# — a Python script that embeds the whole page as a string literal and writes
# the sibling .html — PLUS a hero image. The user opened the .py in a browser,
# saw "only code", and wanted that process scratch gone ("just the main thing").
# A path-only denylist cannot catch this: generate_guide.py is a perfectly
# normal-looking file in artifacts/files/. So this filter inspects content.
#
# Safe by construction: a script is only flagged when the document it emits is
# ALSO in the set, and documents are never script-typed, so the emitted artifact
# always survives. A standalone script the user actually requested has no
# emitted sibling in the set and is never flagged.

_GENERATOR_SCRIPT_EXTS: Final[frozenset[str]] = frozenset({
    ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".php", ".sh", ".bash", ".ps1", ".pl",
})

# Document/markup outputs a build script typically EMITS — things the user looks
# at, not data formats they post-process (no .csv/.json: a script writing those
# is more likely a tool the user wanted, so we conservatively keep it).
_GENERATED_DOC_EXTS: Final[frozenset[str]] = frozenset({
    ".html", ".htm", ".xhtml", ".md", ".markdown", ".svg", ".xml", ".rss", ".atom", ".pdf",
})

# A write/emit signal anywhere in the script body. Combined with a reference to
# the sibling document's basename, this marks "this script writes that file".
_WRITE_SIGNATURE_RE: Final[re.Pattern[str]] = re.compile(
    r"open\s*\([^)]*['\"][wa]"          # open(..., 'w'|'a' ...)
    r"|\.write(?:lines)?\s*\("           # .write( / .writelines(
    r"|\.write_text\s*\("                # pathlib Path.write_text(
    r"|write_?file(?:sync)?\s*\("        # node writeFile / writeFileSync(
    r"|\bfs\.write"                       # node fs.write*
    r"|Out-File\b|Set-Content\b"         # PowerShell
    r"|>>?\s*['\"]?\S+\.[A-Za-z0-9]+",   # shell redirect to a file
    re.IGNORECASE,
)

# Markup-literal fingerprints per document extension: a script embedding these
# is emitting that document even when its write call is obscured (a template
# engine, an f-string sink, etc.).
_DOC_LITERAL_SIGNATURES: Final[dict[str, tuple[str, ...]]] = {
    ".html": ("<!doctype html", "<html"),
    ".htm": ("<!doctype html", "<html"),
    ".xhtml": ("<!doctype html", "<html"),
    ".svg": ("<svg",),
    ".xml": ("<?xml",),
    ".rss": ("<?xml", "<rss"),
    ".atom": ("<?xml", "<feed"),
}


def _ext(rel: str) -> str:
    return posixpath.splitext(rel.replace("\\", "/"))[1].lower()


def _basename(rel: str) -> str:
    return posixpath.basename(rel.replace("\\", "/").strip("/"))


def _script_emits_doc(text: str, doc_rel: str) -> bool:
    """True iff *text* (a script body) generates the document *doc_rel*.

    Requires the document's basename to appear in the script AND either a write
    signature or an embedded markup literal for the document's type.
    """
    base = _basename(doc_rel)
    if not base or base not in text:
        return False
    if _WRITE_SIGNATURE_RE.search(text):
        return True
    low = text.lower()
    return any(sig in low for sig in _DOC_LITERAL_SIGNATURES.get(_ext(doc_rel), ()))


def find_generator_scripts(
    rels: Sequence[str],
    read_text: Callable[[str], str],
) -> frozenset[str]:
    """Return the subset of *rels* that are generator/build scripts.

    A generator script is one whose sole purpose is to emit ANOTHER document
    deliverable that is itself in *rels* (e.g. a ``generate_guide.py`` that
    writes ``city_guide.html``). Such a script is process scratch, not the
    artifact the user asked for.

    *read_text* maps a worktree-relative path to its text content (injected so
    this stays pure and testable). Only script-typed files are ever read.

    Safe by construction:
      * documents are never script-typed, so an emitted document always survives;
      * a script with no emitted sibling document in the set is never flagged;
      * if flagging would leave the set empty, nothing is flagged.
    """
    items = [r for r in rels if r]
    docs = [r for r in items if _ext(r) in _GENERATED_DOC_EXTS]
    if not docs:
        return frozenset()
    generators: set[str] = set()
    for rel in items:
        if _ext(rel) not in _GENERATOR_SCRIPT_EXTS:
            continue
        try:
            text = read_text(rel) or ""
        except OSError:
            continue
        if not text:
            continue
        for doc in docs:
            if doc != rel and _script_emits_doc(text, doc):
                generators.add(rel)
                break
    # Never leave the user with nothing (defensive — documents always survive,
    # so this can only trip on a pathological all-script match).
    if not [r for r in items if r not in generators]:
        return frozenset()
    return frozenset(generators)


__all__ = [
    "find_generator_scripts",
    "is_browser_scratch_segment",
    "is_deliverable_path",
    "is_nondeliverable_scratch",
]
