"""Markdown wiki page parser and renderer (``PageRepository``).

Owned by Instance A. Pure functions plus a thin async-friendly
``MarkdownPageRepository`` wrapper. Disk I/O happens via
``asyncio.to_thread`` so callers on an event loop never block.

Tolerant by design: malformed or missing frontmatter yields
``is_schema_valid=False`` and an otherwise-populated ``WikiPage`` —
never an exception. The schema contract enforced here is the *minimum*
needed to validate a page; the canonical maintenance rules live in
``wiki/obsidian-vault/schema.md`` and are interpreted by Instance D's LLM.

Round-trip stability is a hard invariant: parsing the output of
``render_page(p)`` must return a ``WikiPage`` equal to ``p``. Tests
in ``tests/unit/memory/wiki/test_page.py`` enforce this.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from .protocols import WikiPage
from .wikilink import extract_wikilinks
from .wikilink import resolve_wikilink as _resolve_wikilink

log = logging.getLogger(__name__)

# Directory name → schema page type.
DIR_TO_TYPE: dict[str, str] = {
    "entities": "entity",
    "concepts": "concept",
    "projects": "project",
    "sessions": "session",
    # Mirrored address-book contacts (jarvis/memory/wiki/contact_mirror.py).
    "people": "person",
}

# Required frontmatter keys per page type. Derived from schema.md
# § "Page Types". The minimum every page must declare.
REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "entity": frozenset({"type", "slug"}),
    "concept": frozenset({"type", "slug"}),
    "project": frozenset({"type", "slug", "status"}),
    "session": frozenset({"type", "session_id"}),
    "meta": frozenset({"type"}),
    "person": frozenset({"type", "slug"}),
}

# Canonical section headings per page type, in expected order. Used by
# ``parse_sections`` consumers (e.g. Instance D) to know which sections
# the LLM should produce. Order matches schema.md.
CANONICAL_SECTIONS: dict[str, tuple[str, ...]] = {
    "entity": ("Summary", "Facts", "Relationships", "Sources"),
    "concept": ("Summary", "Definition", "Examples", "Related", "Sources"),
    "project": (
        "Goal", "Current Status", "Recent Activity",
        "Open Threads", "Related", "Sources",
    ),
    "session": (),
    "meta": (),
    # Person pages carry a machine-managed block + free-form learned
    # content; no canonical section order is enforced.
    "person": (),
}

_FM_BOUNDARY = "---"
_SECTION_RE = re.compile(r"^## (.+?)\s*$", re.MULTILINE)


class MarkdownPageRepository:
    """Default ``PageRepository`` implementation.

    Wraps the pure parsing helpers below in an async-friendly interface.
    Reading the file happens in a worker thread; everything else is
    synchronous (markdown parsing is microseconds).
    """

    async def load(self, path: Path) -> WikiPage:
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return parse_markdown(raw, path)

    async def parse(self, raw_markdown: str, path: Path) -> WikiPage:
        return parse_markdown(raw_markdown, path)

    def render(self, page: WikiPage) -> str:
        return render_page(page)

    def resolve_wikilink(self, link: str, vault_root: Path) -> Path | None:
        return _resolve_wikilink(link, vault_root)


def parse_markdown(raw: str, path: Path) -> WikiPage:
    """Parse a markdown source into a ``WikiPage``.

    Always returns a ``WikiPage``. Pages that fail any schema check are
    returned with ``is_schema_valid=False``. The caller is expected to
    decide what to do with an invalid page.
    """
    # Trim only trailing whitespace at file end. Internal whitespace is
    # part of the body and must round-trip.
    cleaned = raw.rstrip()

    frontmatter, body, fm_present = _split_frontmatter(cleaned)
    slug = path.stem

    # Determine the effective page type. Frontmatter wins when present;
    # otherwise the directory is consulted so a typeless page still has a
    # reasonable type assigned (the page just is not schema-valid).
    fm_type = frontmatter.get("type", "")
    dir_type = _type_from_directory(path)
    page_type = fm_type or dir_type

    is_valid = _is_schema_valid(
        path=path,
        frontmatter=frontmatter,
        fm_present=fm_present,
        fm_type=fm_type,
        dir_type=dir_type,
    )

    wikilinks = extract_wikilinks(body)

    return WikiPage(
        path=path,
        page_type=page_type,
        slug=slug,
        frontmatter=frontmatter,
        body=body,
        wikilinks=wikilinks,
        is_schema_valid=is_valid,
    )


def render_page(page: WikiPage) -> str:
    """Render a ``WikiPage`` back to a markdown string.

    The output is always wrapped in ``---`` frontmatter markers, even
    when the frontmatter dictionary is empty — this keeps the renderer's
    output shape stable. Body whitespace is preserved verbatim; a single
    trailing newline is appended.
    """
    lines = [_FM_BOUNDARY]
    for key, value in page.frontmatter.items():
        lines.append(f"{key}: {value}")
    lines.append(_FM_BOUNDARY)
    head = "\n".join(lines)
    if page.body:
        return f"{head}\n{page.body}\n"
    return f"{head}\n"


def parse_sections(body: str) -> tuple[tuple[str, str], ...]:
    """Split a body into ``(heading, content)`` pairs by ``## `` markers.

    The text above the first ``## `` heading (typically the H1 title
    and an optional preamble) is returned under a synthetic empty
    heading ``""`` so nothing is lost. A body with no headings returns
    a single ``("", body)`` pair, or an empty tuple if the body is empty.

    Whitespace inside each section is preserved verbatim except for the
    single newline immediately following the heading line, which is
    consumed as part of the section break.
    """
    if not body:
        return ()

    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        return (("", body),)

    out: list[tuple[str, str]] = []
    first_start = matches[0].start()
    if first_start > 0:
        out.append(("", body[:first_start]))

    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        content_start = m.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[content_start:content_end]
        if content.startswith("\n"):
            content = content[1:]
        out.append((heading, content))

    return tuple(out)


# ──────────────────────────────────────────────────────────────────────
# internals
# ──────────────────────────────────────────────────────────────────────


def _split_frontmatter(text: str) -> tuple[dict[str, str], str, bool]:
    """Split YAML frontmatter from body.

    Returns ``(frontmatter, body, fm_present)``. ``fm_present`` is
    ``True`` only when both the opening and a closing ``---`` line were
    found. Frontmatter values are kept as strings exactly as written
    (after a single ``key: value`` split on the first colon and a strip
    of surrounding whitespace) — list-shaped values like ``[a, b]``
    round-trip as the literal string ``"[a, b]"``.
    """
    if not text.startswith(_FM_BOUNDARY):
        return {}, text, False

    pieces = text.split("\n")
    if pieces[0].strip() != _FM_BOUNDARY:
        return {}, text, False

    close_idx = -1
    for i in range(1, len(pieces)):
        if pieces[i].strip() == _FM_BOUNDARY:
            close_idx = i
            break

    if close_idx < 0:
        # Unclosed frontmatter — treat as broken; whole text becomes body.
        return {}, text, False

    fm_lines = pieces[1:close_idx]
    body_lines = pieces[close_idx + 1:]

    fm: dict[str, str] = {}
    for line in fm_lines:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        if not key:
            continue
        fm[key] = val.strip()

    body = "\n".join(body_lines)
    return fm, body, True


def _type_from_directory(path: Path) -> str:
    parent_name = path.parent.name
    return DIR_TO_TYPE.get(parent_name, "")


def _is_schema_valid(
    *,
    path: Path,
    frontmatter: dict[str, str],
    fm_present: bool,
    fm_type: str,
    dir_type: str,
) -> bool:
    if not fm_present:
        return False
    if not fm_type:
        return False

    # Directory cross-check (skipped for top-level files where dir is
    # neither entities/concepts/projects/sessions — that is the meta tier).
    if dir_type and fm_type != dir_type:
        return False

    required = REQUIRED_KEYS.get(fm_type)
    if required is None:
        return False
    if not required.issubset(frontmatter):
        return False

    # Filename slug must match the frontmatter slug for the three
    # long-term page types. Sessions use a date-prefixed filename whose
    # session_id is stored separately, so they are exempt.
    if fm_type in {"entity", "concept", "project", "person"}:
        if frontmatter.get("slug") != path.stem:
            return False

    return True


__all__ = [
    "MarkdownPageRepository",
    "parse_markdown",
    "render_page",
    "parse_sections",
    "DIR_TO_TYPE",
    "REQUIRED_KEYS",
    "CANONICAL_SECTIONS",
]
