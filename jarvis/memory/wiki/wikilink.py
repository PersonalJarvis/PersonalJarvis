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


def extract_wikilinks(body: str) -> tuple[str, ...]:
    """Return all wikilink targets in ``body`` in source order.

    The returned strings are in *canonical form*: the alias (the part
    after ``|``) is stripped, but any directory prefix is preserved.
    Duplicates are kept — a page that references ``[[ruben]]`` twice
    yields a 2-tuple. Escaped links (``\\[[ignored]]``) and empty links
    (``[[]]``) are not returned.
    """
    out: list[str] = []
    for match in _WIKILINK_RE.finditer(body):
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
