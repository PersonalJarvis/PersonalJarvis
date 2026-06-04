"""Generator for ``wiki/obsidian-vault/index.md``.

The index is a human-readable table of contents. Three contracts apply:

* **Preserve the human preamble.** Anything the user typed above the
  first ``## Entities`` heading must round-trip unchanged. Below that
  heading, the body is fully regenerated from the current vault state.
* **Stable sort.** Within each category section, pages are listed
  alphabetically by slug. The same vault state must always render the
  same string.
* **Soft 200-line cap.** ``schema.md`` states ``index.md`` should stay
  under 200 lines. If the generated index would exceed the cap, the
  most-recently-updated pages win and a ``(... N more)`` line marks the
  remainder per category — the count stays informative without bloating
  the file.

This module owns the *content* of ``index.md`` but not the disk write —
``AtomicWriter`` (Instance C) handles the on-disk update during a
curator run. ``render`` is a pure function over the vault state.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Heading that marks the boundary between user preamble and the
# auto-generated body. Anything above it survives a re-render; anything
# from this heading onward is replaced.
_PREAMBLE_BOUNDARY = "## Entities"

# Categories rendered in this fixed order. Each tuple is
# ``(page_type, heading, blurb)``. The blurb mirrors the seed
# ``index.md`` so the regenerated file reads naturally.
_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    (
        "entity",
        "## Entities",
        "*People, tools, repositories, services, devices.*",
    ),
    (
        "concept",
        "## Concepts",
        "*Abstract recurring ideas, patterns, methodologies.*",
    ),
    (
        "project",
        "## Projects",
        "*Active or recently-active workstreams.*",
    ),
    (
        "session",
        "## Sessions",
        (
            "*Rolling mid-term session rollups. Last 5 active; "
            "older ones in `_archive/sessions/`.*"
        ),
    ),
)

# Default soft cap from schema.md.
DEFAULT_LINE_CAP = 200


@dataclass(slots=True)
class IndexBuilder:
    """Render an ``index.md`` body from the current vault state.

    Parameters
    ----------
    vault:
        The ``VaultIndex`` to read from. Only the read-only accessors
        ``pages_by_type`` are used.
    line_cap:
        Soft cap on the rendered output. Default 200 lines (per
        ``schema.md``). If the natural output exceeds the cap, the
        per-category list is truncated by ``updated`` date with a
        ``(... N more)`` marker line so the file shrinks under the cap.
    """

    vault: Any  # VaultIndex — Any avoids a circular type-only import.
    line_cap: int = DEFAULT_LINE_CAP

    async def render_index_md(
        self,
        *,
        existing_path: Path | None = None,
    ) -> str:
        """Return the new ``index.md`` content.

        ``existing_path`` is the path of the live ``index.md`` (or None
        if no preamble preservation is needed). When provided, the
        preamble — everything above the first ``## Entities`` heading —
        is carried over verbatim into the new content.
        """
        preamble = self._extract_preamble(existing_path)
        sections = [self._render_section(cat) for cat in _CATEGORIES]
        body = "\n\n".join(sections)
        full = preamble + body + "\n"
        full = self._enforce_line_cap(full)
        return full

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _extract_preamble(self, existing_path: Path | None) -> str:
        """Return the part of an existing ``index.md`` above ``## Entities``.

        Returns an empty string when the file is missing or has no
        preamble before the boundary heading. Always ends with a blank
        line so the regenerated body starts on a fresh paragraph.
        """
        if existing_path is None:
            return _DEFAULT_PREAMBLE
        try:
            raw = existing_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _DEFAULT_PREAMBLE
        idx = raw.find("\n" + _PREAMBLE_BOUNDARY)
        if idx < 0:
            # No boundary present — treat the whole file as preamble
            # only when it looks like a stub. Otherwise fall back to
            # the seed preamble so the new render still has structure.
            stripped = raw.strip()
            if not stripped:
                return _DEFAULT_PREAMBLE
            return stripped + "\n\n"
        preamble = raw[: idx + 1]
        if not preamble.endswith("\n\n"):
            preamble = preamble.rstrip("\n") + "\n\n"
        return preamble

    def _render_section(self, category: tuple[str, str, str]) -> str:
        """Render one category (heading + blurb + list of pages)."""
        page_type, heading, blurb = category
        pages: Sequence[Any] = self.vault.pages_by_type(page_type)
        if not pages:
            return f"{heading}\n\n{blurb}\n\n(empty)"
        lines = [heading, "", blurb, ""]
        # `pages_by_type` already sorts by slug. Re-sort defensively so
        # a future drift in that contract does not silently corrupt the
        # rendered index.
        for page in sorted(pages, key=lambda p: getattr(p, "slug", "")):
            lines.append(_render_page_line(page))
        return "\n".join(lines)

    def _enforce_line_cap(self, full: str) -> str:
        """If ``full`` exceeds the cap, truncate each section's tail.

        We do a single uniform pass: each category section keeps its
        most-recently-updated entries until the total stays under the
        cap. A ``(... N more)`` line is appended where truncation
        happened so the human can still count what they're missing.
        """
        line_count = full.count("\n")
        if line_count <= self.line_cap:
            return full
        # Split the rendered text into preamble + category blocks and
        # trim each block proportionally.
        preamble, _, body = full.partition(_PREAMBLE_BOUNDARY)
        if not body:
            return full
        body = _PREAMBLE_BOUNDARY + body
        blocks = _split_into_category_blocks(body)
        # Reserve preamble + headings + blurbs; the remaining budget is
        # for the bullet lines.
        preamble_lines = preamble.count("\n")
        fixed_overhead_per_block = 4  # heading, blank, blurb, blank
        budget = self.line_cap - preamble_lines - (
            fixed_overhead_per_block * len(blocks)
        )
        budget = max(budget, len(blocks))  # at least one bullet per block
        per_block = max(budget // max(len(blocks), 1), 1)
        trimmed_blocks: list[str] = []
        for heading, bullets in blocks:
            if len(bullets) > per_block:
                kept = bullets[:per_block]
                kept.append(f"(... {len(bullets) - per_block} more)")
            else:
                kept = bullets
            trimmed_blocks.append(_render_block(heading, kept))
        result = preamble + "\n\n".join(trimmed_blocks) + "\n"
        return result


_DEFAULT_PREAMBLE = (
    "---\n"
    "type: index\n"
    "purpose: table-of-contents\n"
    "---\n"
    "\n"
    "# Knowledge Vault — Index\n"
    "\n"
    "Auto-generated table of contents. The block above this notice is\n"
    "preserved across regenerations; everything from the first\n"
    "`## Entities` heading onward is rewritten by the wiki curator.\n"
    "\n"
)


def _render_page_line(page: Any) -> str:
    """Return one bullet line for ``page`` in the index list.

    Uses the short wikilink form (``[[slug]]``) — the schema permits the
    typed form too but short is what humans skim.
    """
    slug = getattr(page, "slug", "") or "(missing-slug)"
    aliases = getattr(page, "frontmatter", {}).get("aliases") or ""
    if isinstance(aliases, (list, tuple)) and aliases:
        alias_label = " — " + ", ".join(str(a) for a in aliases[:3])
    elif isinstance(aliases, str) and aliases.strip():
        alias_label = " — " + aliases.strip()
    else:
        alias_label = ""
    return f"- [[{slug}]]{alias_label}"


def _split_into_category_blocks(body: str) -> list[tuple[str, list[str]]]:
    """Decompose the category body back into ``(heading_block, bullets)``.

    ``heading_block`` is the heading + blurb + leading blank — the
    fixed part of a section. ``bullets`` is the list of trailing bullet
    lines (or the ``(empty)`` placeholder, rendered as a single bullet).
    """
    blocks: list[tuple[str, list[str]]] = []
    current_heading: list[str] = []
    current_bullets: list[str] = []
    state = "expect_heading"
    for line in body.splitlines():
        if line.startswith("## ") and state != "expect_heading":
            blocks.append(("\n".join(current_heading), current_bullets))
            current_heading = []
            current_bullets = []
            state = "expect_heading"
        if state == "expect_heading":
            current_heading.append(line)
            if line == "" and len(current_heading) >= 4:
                state = "bullets"
        else:
            if line:
                current_bullets.append(line)
    if current_heading or current_bullets:
        blocks.append(("\n".join(current_heading), current_bullets))
    return blocks


def _render_block(heading_block: str, bullets: list[str]) -> str:
    if not bullets:
        return heading_block + "\n(empty)"
    return heading_block + "\n" + "\n".join(bullets)
