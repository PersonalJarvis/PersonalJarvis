"""Fixtures for the wiki vault-index / log-writer / index-builder /
atomic-writer / curator-LLM tests.

The four B1 instances each shipped a ``conftest.py`` for their own
suite; Wave 2 has merged them into this single file. Fakes here cover
the duck-typed contract of Instance A's ``PageRepository`` so the
other instances' tests run before Wave 2 has wired anything real.

When the curator orchestrator (wave 2) uses real implementations, these
fakes stay around for the unit tests — only the integration tests in
``tests/integration/memory/wiki/`` construct the real stack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest


@dataclass(slots=True)
class FakeWikiPage:
    """Stand-in for Instance A's ``WikiPage`` dataclass.

    Exposes only the attributes the vault-index, index-builder, atomic-
    writer, and curator-LLM actually read. Mutability is on purpose —
    tests reassign fields when simulating renames.
    """

    path: Path
    page_type: str
    slug: str
    frontmatter: dict[str, str] = field(default_factory=dict)
    body: str = ""
    wikilinks: tuple[str, ...] = ()
    is_schema_valid: bool = True


# Pattern captures every ``[[target]]`` even when prefixed (``entities/x``)
# or aliased (``slug|alias``). Escaped form ``\[[...]]`` is excluded by
# the negative-lookbehind ``(?<!\\)``.
_WIKILINK_RE = re.compile(r"(?<!\\)\[\[([^\]\n]+)\]\]")


def _split_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Tiny YAML-front-matter splitter — enough for tests.

    Recognises a leading ``---`` block, parses ``key: value`` pairs as
    strings, and returns the body verbatim. Lists are kept as the raw
    literal (``[a, b]``) — the body of the wiki page is what we test
    against, not the frontmatter parser fidelity.
    """
    if not raw.startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    if len(lines) < 2:
        return {}, raw
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end < 0:
        return {}, raw
    fm: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return fm, body


def _infer_type_from_path(path: Path) -> str:
    parent = path.parent.name
    return {
        "entities": "entity",
        "concepts": "concept",
        "projects": "project",
        "sessions": "session",
    }.get(parent, "")


@dataclass(slots=True)
class FakePageRepository:
    """Stand-in for Instance A's ``PageRepository``.

    Reads markdown files from disk, splits a minimal YAML frontmatter
    block, scans for wikilinks. The output is good enough for VaultIndex
    + LogWriter + IndexBuilder + AtomicWriter + CuratorLLM tests. The
    real Instance A repository produces richer ``frontmatter`` values
    but the contract surface is the same.
    """

    async def load(self, path: Path) -> FakeWikiPage:
        raw = path.read_text(encoding="utf-8")
        return await self.parse(raw, path)

    async def parse(self, raw_markdown: str, path: Path) -> FakeWikiPage:
        fm, body = _split_frontmatter(raw_markdown)
        slug = fm.get("slug") or path.stem
        page_type = fm.get("type") or _infer_type_from_path(path)
        wikilinks = tuple(m.group(1).strip() for m in _WIKILINK_RE.finditer(body))
        is_valid = bool(slug) and page_type in {
            "entity", "concept", "project", "session", "meta", "index",
        }
        if page_type in {"entity", "concept", "project", "session"}:
            expected_dir = {
                "entity": "entities",
                "concept": "concepts",
                "project": "projects",
                "session": "sessions",
            }[page_type]
            is_valid = is_valid and path.parent.name == expected_dir
        return FakeWikiPage(
            path=path,
            page_type=page_type,
            slug=slug,
            frontmatter=fm,
            body=body,
            wikilinks=wikilinks,
            is_schema_valid=is_valid,
        )

    def render(self, page: FakeWikiPage) -> str:  # pragma: no cover
        # Not exercised by the vault-index tests, kept for protocol parity.
        fm_lines = ["---"] + [f"{k}: {v}" for k, v in page.frontmatter.items()] + ["---", ""]
        return "\n".join(fm_lines) + page.body

    def resolve_wikilink(
        self, link: str, vault_root: Path
    ) -> Path | None:  # pragma: no cover
        bare = link.split("|", 1)[0].strip()
        if "/" in bare:
            candidate = vault_root / (bare + ".md")
            return candidate if candidate.exists() else None
        for sub in ("entities", "concepts", "projects", "sessions"):
            candidate = vault_root / sub / (bare + ".md")
            if candidate.exists():
                return candidate
        return None


# ---------------------------------------------------------------------
# Reusable fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def fake_repo() -> FakePageRepository:
    """A fresh ``FakePageRepository`` per test."""
    return FakePageRepository()


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    """A clean vault skeleton on tmpfs.

    Creates the four page-type directories plus _archive and
    attachments so tests can drop ``*.md`` files directly without
    preparing the layout themselves.
    """
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (tmp_path / sub).mkdir()
    return tmp_path


def write_page(
    vault_root: Path,
    page_type: str,
    slug: str,
    *,
    body: str = "Body.",
    wikilinks: list[str] | None = None,
    frontmatter_extras: dict[str, str] | None = None,
) -> Path:
    """Materialise one wiki page on disk and return its path.

    Helper used across multiple test files. ``wikilinks`` are appended
    to the body as plain ``[[target]]`` tokens so the FakePageRepository
    picks them up.
    """
    dir_for = {
        "entity": "entities",
        "concept": "concepts",
        "project": "projects",
        "session": "sessions",
    }[page_type]
    path = vault_root / dir_for / f"{slug}.md"
    fm_lines = ["---", f"type: {page_type}", f"slug: {slug}"]
    for k, v in (frontmatter_extras or {}).items():
        fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    fm_lines.append("")
    rendered_body = body
    for link in wikilinks or []:
        rendered_body += f"\nSee [[{link}]]."
    path.write_text("\n".join(fm_lines) + "\n" + rendered_body + "\n", encoding="utf-8")
    return path
