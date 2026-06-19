"""Unit tests for ``jarvis.memory.wiki.page``.

Round-trip stability is the central invariant: ``parse(render(parse(t)))``
must equal ``parse(t)`` for every input ``t``. Several tests verify this
directly; others target the tolerant-parser behaviour (malformed input
returns ``is_schema_valid=False`` and never raises).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.memory.wiki.page import (
    CANONICAL_SECTIONS,
    DIR_TO_TYPE,
    REQUIRED_KEYS,
    MarkdownPageRepository,
    parse_markdown,
    parse_sections,
    render_page,
)
from jarvis.memory.wiki.protocols import PageRepository, WikiPage


# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────


def _entity_page(tmp_path: Path, slug: str = "ruben") -> Path:
    return tmp_path / "entities" / f"{slug}.md"


def _make_entity_dirs(tmp_path: Path) -> None:
    for sub in ("entities", "concepts", "projects", "sessions"):
        (tmp_path / sub).mkdir(exist_ok=True)


def _valid_entity_markdown(slug: str = "ruben") -> str:
    return (
        "---\n"
        "type: entity\n"
        "entity_kind: person\n"
        f"slug: {slug}\n"
        "aliases: [Rubén, Ruben Lütke]\n"
        "created: 2026-05-11\n"
        "updated: 2026-05-11\n"
        "---\n"
        f"# {slug.title()}\n"
        "\n"
        "## Summary\n"
        "An example entity page.\n"
        "\n"
        "## Facts\n"
        "- Fact one.\n"
        "- Fact two.\n"
        "\n"
        "## Relationships\n"
        "- [[concepts/awareness-layer]] — the long-term tier.\n"
        "\n"
        "## Sources\n"
        "- [[log#2026-05-11]]\n"
    )


# ──────────────────────────────────────────────────────────────────────
# parsing happy paths
# ──────────────────────────────────────────────────────────────────────


def test_parse_valid_entity_is_schema_valid(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    path = _entity_page(tmp_path)
    page = parse_markdown(_valid_entity_markdown(), path)
    assert page.is_schema_valid is True
    assert page.page_type == "entity"
    assert page.slug == "ruben"
    assert page.frontmatter["aliases"] == "[Rubén, Ruben Lütke]"


def test_parse_extracts_wikilinks_in_order(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    page = parse_markdown(_valid_entity_markdown(), _entity_page(tmp_path))
    # Two wikilinks in the example body, in document order.
    assert page.wikilinks == ("concepts/awareness-layer", "log#2026-05-11")


def test_parse_concept_page_is_schema_valid(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = (
        "---\n"
        "type: concept\n"
        "slug: awareness-layer\n"
        "aliases: []\n"
        "created: 2026-05-11\n"
        "updated: 2026-05-11\n"
        "---\n"
        "# Awareness Layer\n"
        "\n"
        "## Summary\n"
        "Tiered memory.\n"
    )
    path = tmp_path / "concepts" / "awareness-layer.md"
    page = parse_markdown(src, path)
    assert page.is_schema_valid is True
    assert page.page_type == "concept"


def test_parse_project_requires_status(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    # Without status → invalid.
    src_no_status = (
        "---\n"
        "type: project\n"
        "slug: wiki-rebuild\n"
        "---\n"
        "# Wiki Rebuild\n"
    )
    bad = parse_markdown(src_no_status, tmp_path / "projects" / "wiki-rebuild.md")
    assert bad.is_schema_valid is False

    # With status → valid.
    src = (
        "---\n"
        "type: project\n"
        "slug: wiki-rebuild\n"
        "status: active\n"
        "started: 2026-05-11\n"
        "last_activity: 2026-05-11\n"
        "---\n"
        "# Wiki Rebuild\n"
    )
    good = parse_markdown(src, tmp_path / "projects" / "wiki-rebuild.md")
    assert good.is_schema_valid is True


def test_parse_session_page_uses_session_id_not_slug(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = (
        "---\n"
        "type: session\n"
        "date: 2026-05-11\n"
        "started_at: 14:00\n"
        "ended_at: 15:00\n"
        "episode_ids: [42, 43]\n"
        "session_id: abc123\n"
        "---\n"
        "Body prose.\n"
    )
    path = tmp_path / "sessions" / "2026-05-11-abc123.md"
    page = parse_markdown(src, path)
    assert page.is_schema_valid is True
    assert page.page_type == "session"
    # Session filename slug differs from the session_id — that is OK.
    assert page.slug == "2026-05-11-abc123"


# ──────────────────────────────────────────────────────────────────────
# parsing tolerant failures
# ──────────────────────────────────────────────────────────────────────


def test_parse_missing_frontmatter_is_invalid_no_raise(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    page = parse_markdown("# No frontmatter here\n", _entity_page(tmp_path))
    assert page.is_schema_valid is False
    assert page.frontmatter == {}
    # Page type falls back to directory; body retained.
    assert page.page_type == "entity"
    assert page.body == "# No frontmatter here"


def test_parse_unclosed_frontmatter_is_invalid(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = "---\ntype: entity\nslug: ruben\n# body without closing marker\n"
    page = parse_markdown(src, _entity_page(tmp_path))
    assert page.is_schema_valid is False
    # Everything went to body when the close marker was missing.
    assert "type: entity" in page.body


def test_parse_directory_mismatch_invalid(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    # An entity page placed under concepts/ — type mismatches directory.
    page = parse_markdown(
        _valid_entity_markdown(),
        tmp_path / "concepts" / "ruben.md",
    )
    assert page.is_schema_valid is False


def test_parse_slug_filename_mismatch_invalid(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = _valid_entity_markdown(slug="ruben")
    # The frontmatter says ``slug: ruben`` but the file lives at
    # entities/someone-else.md — must be flagged.
    page = parse_markdown(src, tmp_path / "entities" / "someone-else.md")
    assert page.is_schema_valid is False


def test_parse_unknown_page_type_invalid(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = (
        "---\n"
        "type: alien\n"
        "slug: x\n"
        "---\n"
        "Body\n"
    )
    page = parse_markdown(src, tmp_path / "entities" / "x.md")
    # Type mismatches directory (alien vs entity) → invalid; the alien
    # type does not appear in REQUIRED_KEYS either.
    assert page.is_schema_valid is False
    assert page.page_type == "alien"


def test_parse_empty_body_does_not_raise(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = (
        "---\n"
        "type: entity\n"
        "slug: ruben\n"
        "---\n"
    )
    page = parse_markdown(src, _entity_page(tmp_path))
    # ``entity_kind`` etc. are not required for tolerance — only ``type``
    # and ``slug`` are strictly required.
    assert page.is_schema_valid is True
    assert page.body == ""


# ──────────────────────────────────────────────────────────────────────
# round-trip
# ──────────────────────────────────────────────────────────────────────


def test_round_trip_valid_entity(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    path = _entity_page(tmp_path)
    src = _valid_entity_markdown()
    page = parse_markdown(src, path)
    rendered = render_page(page)
    re_parsed = parse_markdown(rendered, path)
    assert re_parsed == page


def test_round_trip_double_render_text_stable(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    page = parse_markdown(_valid_entity_markdown(), _entity_page(tmp_path))
    first = render_page(page)
    second = render_page(parse_markdown(first, _entity_page(tmp_path)))
    assert first == second


def test_round_trip_concept_page(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = (
        "---\n"
        "type: concept\n"
        "slug: voice-pipeline\n"
        "aliases: [Voice-Pipeline]\n"
        "created: 2026-05-11\n"
        "updated: 2026-05-11\n"
        "---\n"
        "# Voice Pipeline\n"
        "\n"
        "## Summary\n"
        "Wake → VAD → STT → Brain → TTS.\n"
    )
    path = tmp_path / "concepts" / "voice-pipeline.md"
    page = parse_markdown(src, path)
    re_parsed = parse_markdown(render_page(page), path)
    assert re_parsed == page


def test_round_trip_empty_body(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = (
        "---\n"
        "type: entity\n"
        "slug: ruben\n"
        "---\n"
    )
    path = _entity_page(tmp_path)
    page = parse_markdown(src, path)
    rendered = render_page(page)
    assert rendered.endswith("---\n")  # no body, no trailing extra newline
    assert parse_markdown(rendered, path) == page


def test_round_trip_preserves_internal_whitespace(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = (
        "---\n"
        "type: entity\n"
        "slug: ruben\n"
        "---\n"
        "\n"
        "\n"
        "Three blank lines above this one.\n"
        "Trailing spaces:    \n"
        "After.\n"
    )
    path = _entity_page(tmp_path)
    page = parse_markdown(src, path)
    re_parsed = parse_markdown(render_page(page), path)
    assert re_parsed == page
    # Internal trailing whitespace inside a line stays intact.
    assert "Trailing spaces:    " in page.body


def test_round_trip_unicode_body(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = (
        "---\n"
        "type: entity\n"
        "slug: ruben\n"
        "aliases: [Rubén Lütke]\n"
        "---\n"
        "Café — über sechs Zeichen mit Umlauten: äöüß.\n"
    )
    path = _entity_page(tmp_path)
    page = parse_markdown(src, path)
    re_parsed = parse_markdown(render_page(page), path)
    assert re_parsed == page
    assert "äöüß" in page.body


def test_render_includes_frontmatter_markers_even_when_empty() -> None:
    page = WikiPage(
        path=Path("entities/x.md"),
        page_type="",
        slug="x",
        frontmatter={},
        body="hello",
        wikilinks=(),
        is_schema_valid=False,
    )
    rendered = render_page(page)
    assert rendered.startswith("---\n---\n")


def test_frontmatter_value_with_colon_is_kept_verbatim(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    src = (
        "---\n"
        "type: entity\n"
        "slug: ruben\n"
        "url: https://example.com/path\n"
        "---\n"
        "body\n"
    )
    page = parse_markdown(src, _entity_page(tmp_path))
    assert page.frontmatter["url"] == "https://example.com/path"
    re_parsed = parse_markdown(render_page(page), _entity_page(tmp_path))
    assert re_parsed.frontmatter == page.frontmatter


# ──────────────────────────────────────────────────────────────────────
# parse_sections
# ──────────────────────────────────────────────────────────────────────


def test_parse_sections_empty_body() -> None:
    assert parse_sections("") == ()


def test_parse_sections_no_headings_returns_single_pair() -> None:
    out = parse_sections("Just one paragraph.\n")
    assert out == (("", "Just one paragraph.\n"),)


def test_parse_sections_preamble_plus_two_sections() -> None:
    body = (
        "# Title\n"
        "\n"
        "Preamble paragraph.\n"
        "\n"
        "## Summary\n"
        "Summary text.\n"
        "\n"
        "## Facts\n"
        "Fact text.\n"
    )
    sections = parse_sections(body)
    headings = [h for h, _ in sections]
    assert headings == ["", "Summary", "Facts"]
    # Preamble content begins with the H1 title.
    assert sections[0][1].startswith("# Title")
    assert sections[1][1].startswith("Summary text.")
    assert sections[2][1].startswith("Fact text.")


def test_canonical_sections_constant_matches_schema() -> None:
    # Entity sections must be exactly Summary/Facts/Relationships/Sources.
    assert CANONICAL_SECTIONS["entity"] == (
        "Summary", "Facts", "Relationships", "Sources",
    )
    # Concept sections per schema.
    assert "Definition" in CANONICAL_SECTIONS["concept"]
    # Project sections per schema.
    assert "Goal" in CANONICAL_SECTIONS["project"]


def test_dir_to_type_constant_complete() -> None:
    assert DIR_TO_TYPE == {
        "entities": "entity",
        "concepts": "concept",
        "projects": "project",
        "sessions": "session",
        "people": "person",
    }


def test_required_keys_constant_contains_type_for_every_page_type() -> None:
    for ptype, keys in REQUIRED_KEYS.items():
        assert "type" in keys, f"{ptype} missing required 'type' key"


# ──────────────────────────────────────────────────────────────────────
# MarkdownPageRepository (the PageRepository implementation)
# ──────────────────────────────────────────────────────────────────────


def test_repository_implements_page_repository_protocol() -> None:
    repo = MarkdownPageRepository()
    # runtime_checkable Protocol — isinstance is the contract check.
    assert isinstance(repo, PageRepository)


def test_repository_load_async_reads_file(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    path = _entity_page(tmp_path)
    path.write_text(_valid_entity_markdown(), encoding="utf-8")

    repo = MarkdownPageRepository()
    page = asyncio.run(repo.load(path))
    assert page.is_schema_valid is True
    assert page.slug == "ruben"


def test_repository_parse_does_not_touch_disk(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    # The file does not exist on disk — parse() must work from the string.
    path = _entity_page(tmp_path, slug="ghost")
    assert not path.exists()
    repo = MarkdownPageRepository()
    page = asyncio.run(repo.parse(_valid_entity_markdown(slug="ghost"), path))
    assert page.slug == "ghost"


def test_repository_resolve_delegates_to_wikilink(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    (tmp_path / "entities" / "ruben.md").write_text("x", encoding="utf-8")
    repo = MarkdownPageRepository()
    resolved = repo.resolve_wikilink("ruben", tmp_path)
    assert resolved == tmp_path / "entities" / "ruben.md"


def test_repository_render_round_trip(tmp_path: Path) -> None:
    _make_entity_dirs(tmp_path)
    repo = MarkdownPageRepository()
    page = asyncio.run(
        repo.parse(_valid_entity_markdown(), _entity_page(tmp_path))
    )
    text = repo.render(page)
    re_parsed = asyncio.run(repo.parse(text, _entity_page(tmp_path)))
    assert re_parsed == page
