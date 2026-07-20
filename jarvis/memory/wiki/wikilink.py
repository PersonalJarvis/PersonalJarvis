"""Wikilink parser and resolver.

Owned by Instance A. The vault uses Obsidian-style ``[[wikilinks]]``.
See ``wiki/obsidian-vault/schema.md`` § "Wikilinks" for the contract.

Supported forms:

* ``[[slug]]``                — short form, resolved across known
                                 directories with explicit-prefix
                                 preference.
* ``[[entities/slug]]``       — explicit directory prefix.
* ``[[slug|alias]]``          — display alias (canonical form is
                                 the part before the pipe).
* ``[[entities/slug|alias]]`` — directory prefix + alias.
* ``\\[[escaped]]``           — preceded by a single backslash;
                                 ignored as a link.

The parser is tolerant. Empty links (``[[]]``), multi-line links and
links containing ``]`` characters are not recognised. Wikilink output
preserves input order and allows duplicates so that round-trip rendering
is loss-free.
"""
from __future__ import annotations

import re
from pathlib import Path

# Directories searched when a short-form link ``[[slug]]`` has no explicit
# directory prefix. Order matches the schema's listing of long-term page
# types; ``sessions`` is included because session rollups also live in
# the vault and may be wikilinked.
SEARCHABLE_DIRS: tuple[str, ...] = ("entities", "concepts", "projects", "sessions")

# Matches ``[[...]]`` that is **not** preceded by a backslash. The inner
# group disallows ``]`` and newlines so we never absorb adjacent tokens.
# A leading ``+`` (one-or-more) prevents matching ``[[]]``.
_WIKILINK_RE = re.compile(r"(?<!\\)\[\[([^\]\n]+)\]\]")

# Code is documentation, not linkage: Obsidian renders neither inline code
# spans nor fenced blocks as live links, and the vault's own ``schema.md``
# quotes example links (`` `[[wikilinks]]` ``) in code throughout. Matching
# inside code once produced phantom graph nodes for every quoted example —
# including one whose "target" was half a sentence, because a lone ``[[``
# inside an inline span absorbed all prose up to the next real ``]]``.
_INLINE_CODE_RE = re.compile(r"(`+)([^`\n]+?)\1")
_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _blank_code_regions(body: str) -> str:
    """Replace fenced code blocks and inline code spans with spaces.

    Offsets and newlines are preserved so callers that search the original
    body for context (snippets, backlinks) stay aligned.
    """
    lines: list[str] = []
    fence_char = ""
    for line in body.splitlines(keepends=True):
        match = _FENCE_OPEN_RE.match(line)
        if fence_char:
            lines.append(_blank_keep_newline(line))
            if match and match.group(1)[0] == fence_char:
                fence_char = ""
            continue
        if match:
            fence_char = match.group(1)[0]
            lines.append(_blank_keep_newline(line))
            continue
        lines.append(_INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line))
    return "".join(lines)


def _blank_keep_newline(line: str) -> str:
    stripped = line.rstrip("\r\n")
    return " " * len(stripped) + line[len(stripped):]


def extract_wikilinks(body: str) -> tuple[str, ...]:
    """Return all wikilink targets in ``body`` in source order.

    The returned strings are in *canonical form*: the alias (the part
    after ``|``) is stripped, but any directory prefix is preserved.
    Duplicates are kept — a page that references ``[[ruben]]`` twice
    yields a 2-tuple. Escaped links (``\\[[ignored]]``), empty links
    (``[[]]``), and links inside inline code or fenced code blocks are
    not returned.
    """
    out: list[str] = []
    for match in _WIKILINK_RE.finditer(_blank_code_regions(body)):
        target = _canonicalise(match.group(1))
        if target:
            out.append(target)
    return tuple(out)


def resolve_wikilink(link: str, vault_root: Path) -> Path | None:
    """Resolve a wikilink target to an absolute vault path.

    Returns ``None`` if the link is broken — that is:

    * the explicit-prefix form points at a non-existent file, **or**
    * the short form is ambiguous (matches multiple directories), **or**
    * the short form matches no file at all.

    The caller decides what to do with a ``None`` result (e.g. instruct
    the curator to create the page or fall back to plain text).
    """
    target = _canonicalise(link)
    if not target:
        return None

    if "/" in target:
        # Explicit-prefix form. Trust the prefix; no fallback search.
        candidate = vault_root / f"{target}.md"
        return candidate if candidate.is_file() else None

    # Short form: search known directories.
    hits: list[Path] = []
    for directory in SEARCHABLE_DIRS:
        candidate = vault_root / directory / f"{target}.md"
        if candidate.is_file():
            hits.append(candidate)

    if len(hits) == 1:
        return hits[0]
    return None  # zero matches or ambiguous


def _canonicalise(raw: str) -> str:
    """Strip alias and whitespace from a wikilink body.

    ``"entities/ruben|the user"`` → ``"entities/ruben"``.
    Empty or whitespace-only results return ``""``.
    """
    if not raw:
        return ""
    target = raw.split("|", 1)[0].strip()
    return target


__all__ = ["extract_wikilinks", "resolve_wikilink", "SEARCHABLE_DIRS"]
