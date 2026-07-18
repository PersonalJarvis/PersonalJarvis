"""Tests for ``session_links`` — deterministic graph-connectivity post-processing
of the session-rollup paragraph.

The session rollup used to drop the raw LLM paragraph verbatim into the page,
which produced a scattered Obsidian graph: every session linked Title-Case
display names of ephemeral apps (``[[Brave Browser]]``, ``[[PowerShell]]``)
that resolve to no page, plus the occasional token-truncated ``[[PickerHost.``
fragment, and never linked any durable hub. These pure helpers fix that:

* ``slugify`` mirrors the schema's kebab-case rule.
* ``strip_dangling_wikilinks`` removes a truncated ``[[…`` with no closing ``]]``.
* ``SlugIndex`` resolves a link target (display text or slug) to a canonical
  ``dir/slug`` only when the page actually exists.
* ``rewrite_body_links`` canonicalises resolvable links and demotes the rest
  to plain text (schema: "refuse the link and use plain text").
* ``build_related_footer`` emits the deterministic ``## Related`` backbone.
"""
from __future__ import annotations

import pytest

from jarvis.memory.wiki.session_links import (
    SlugIndex,
    build_related_footer,
    promote_plaintext_links,
    relink_session_body,
    rewrite_body_links,
    slugify,
    strip_dangling_wikilinks,
)

# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Brave Browser", "brave-browser"),
    ("Windows Terminal", "windows-terminal"),
    ("RazerAppEngine.exe", "razerappengine-exe"),
    ("Visual Studio Code", "visual-studio-code"),
    ("ruben", "ruben"),
    ("  Mixed   Spaces  ", "mixed-spaces"),
    ("Personal_Jarvis", "personal-jarvis"),
    ("Über Café", "uber-cafe"),  # i18n-allow: German/accented word testing slugify's unicode handling, content under test
])
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


# ---------------------------------------------------------------------------
# strip_dangling_wikilinks
# ---------------------------------------------------------------------------

def test_strips_trailing_unclosed_link() -> None:
    text = "User worked via [[PickerHost."
    assert strip_dangling_wikilinks(text) == "User worked via PickerHost."


def test_strips_unclosed_link_midtext_before_next_link() -> None:
    text = "Opened [[Foo and then [[bar]] later."
    # The first '[[' never closes before the next '[['; it must be removed.
    assert strip_dangling_wikilinks(text) == "Opened Foo and then [[bar]] later."


def test_keeps_well_formed_links() -> None:
    text = "See [[entities/ruben]] and [[projects/x|X]]."
    assert strip_dangling_wikilinks(text) == text


def test_strip_is_noop_without_brackets() -> None:
    text = "No links here at all."
    assert strip_dangling_wikilinks(text) == text


# ---------------------------------------------------------------------------
# SlugIndex.resolve
# ---------------------------------------------------------------------------

def _index() -> SlugIndex:
    return SlugIndex.from_pages([
        ("entities", "ruben", ["Ruben", "the user"]),
        ("entities", "obsidian", ["Obsidian"]),
        ("projects", "personal-jarvis", ["Personal Jarvis"]),
        ("concepts", "wiki-curator", []),
    ])


def test_resolve_bare_slug() -> None:
    assert _index().resolve("ruben") == "entities/ruben"


def test_resolve_dir_prefixed() -> None:
    assert _index().resolve("entities/ruben") == "entities/ruben"
    assert _index().resolve("projects/personal-jarvis") == "projects/personal-jarvis"


def test_resolve_title_case_via_slugify() -> None:
    assert _index().resolve("Personal Jarvis") == "projects/personal-jarvis"
    assert _index().resolve("Wiki Curator") == "concepts/wiki-curator"


def test_resolve_via_alias() -> None:
    assert _index().resolve("the user") == "entities/ruben"


def test_resolve_unknown_returns_none() -> None:
    assert _index().resolve("PowerShell") is None
    assert _index().resolve("Brave Browser") is None
    assert _index().resolve("entities/does-not-exist") is None


# ---------------------------------------------------------------------------
# rewrite_body_links
# ---------------------------------------------------------------------------

def test_rewrite_canonicalises_resolvable_link() -> None:
    text = "Worked on [[Personal Jarvis]] today."
    out, resolved = rewrite_body_links(text, _index())
    assert "[[projects/personal-jarvis|Personal Jarvis]]" in out
    assert "projects/personal-jarvis" in resolved


def test_rewrite_demotes_unresolvable_link_to_plain_text() -> None:
    text = "Ran [[PowerShell]] and [[BraveUpdate.exe]] in the background."
    out, resolved = rewrite_body_links(text, _index())
    assert "[[" not in out
    assert "PowerShell" in out and "BraveUpdate.exe" in out
    assert resolved == []


def test_rewrite_preserves_alias_display() -> None:
    text = "Talked to [[the user]] about it."
    out, _ = rewrite_body_links(text, _index())
    assert "[[entities/ruben|the user]]" in out


def test_rewrite_short_form_when_display_equals_slug() -> None:
    text = "See [[entities/ruben]]."
    out, resolved = rewrite_body_links(text, _index())
    # Already canonical and display is the typed slug -> keep typed form.
    assert "[[entities/ruben]]" in out
    assert resolved == ["entities/ruben"]


def test_rewrite_mixed_keeps_resolvable_drops_ghosts() -> None:
    text = "In [[Personal Jarvis]] I used [[Cursor]] and pinged [[Ruben]]."
    out, resolved = rewrite_body_links(text, _index())
    assert "[[projects/personal-jarvis|Personal Jarvis]]" in out
    assert "[[entities/ruben|Ruben]]" in out
    assert "Cursor" in out and "[[Cursor]]" not in out
    assert set(resolved) == {"projects/personal-jarvis", "entities/ruben"}


# ---------------------------------------------------------------------------
# build_related_footer
# ---------------------------------------------------------------------------

def test_footer_lists_hubs_and_resolved_targets() -> None:
    footer = build_related_footer(
        hub_links=["entities/ruben", "projects/personal-jarvis"],
        resolved_targets=["concepts/wiki-curator", "entities/ruben"],
    )
    assert footer.startswith("## Related")
    assert "[[entities/ruben]]" in footer
    assert "[[projects/personal-jarvis]]" in footer
    assert "[[concepts/wiki-curator]]" in footer
    # ruben appears once (dedup across hubs + resolved)
    assert footer.count("[[entities/ruben]]") == 1


def test_footer_empty_when_nothing_to_link() -> None:
    assert build_related_footer(hub_links=[], resolved_targets=[]) == ""


def test_footer_short_form_typed() -> None:
    """Hub/resolved links render as typed [[dir/slug]] (always resolvable)."""
    footer = build_related_footer(hub_links=["entities/ruben"], resolved_targets=[])
    assert "- [[entities/ruben]]" in footer


# ---------------------------------------------------------------------------
# relink_session_body (one-shot migration of existing pages)
# ---------------------------------------------------------------------------

def test_relink_demotes_ghosts_strips_dangling_and_adds_footer() -> None:
    body = (
        "# Session\n\nThe user worked in [[Personal Jarvis]] using [[PowerShell]] "
        "and opened [[Snipping Tool"
    )
    new_body, stats = relink_session_body(body, _index(), user_slug="ruben")
    # Ghost demoted, dangling stripped, resolvable canonicalised, footer added.
    assert "[[PowerShell]]" not in new_body
    assert "[[Snipping Tool" not in new_body and "Snipping Tool" in new_body
    assert "[[projects/personal-jarvis|Personal Jarvis]]" in new_body
    assert "## Related" in new_body
    assert "[[entities/ruben]]" in new_body
    assert stats["changed"] is True


def test_relink_idempotent_does_not_double_footer() -> None:
    body = "# Session\n\nWorked on [[Personal Jarvis]].\n\n## Related\n\n- [[entities/ruben]]\n"
    new_body, stats = relink_session_body(body, _index(), user_slug="ruben")
    assert new_body.count("## Related") == 1


def test_relink_clean_body_is_unchanged() -> None:
    body = "# Session\n\nWorked on [[projects/personal-jarvis]].\n\n## Related\n\n- [[entities/ruben]]\n"
    new_body, stats = relink_session_body(body, _index(), user_slug="ruben")
    assert stats["changed"] is False
    assert new_body == body


def _colliding_index() -> SlugIndex:
    """One slug living in TWO directories — the live bugatti-divo layout."""
    return SlugIndex.from_pages([
        ("entities", "ruben", ["Ruben"]),
        ("entities", "bugatti-divo", ["Bugatti Divo"]),
        ("projects", "bugatti-divo", []),
    ])


def test_dir_prefixed_resolve_survives_cross_directory_slug_collision() -> None:
    """Live scar 2026-07-18: entities/bugatti-divo AND projects/bugatti-divo
    both exist; the single-slot bare-slug map made ONE of the dir-prefixed
    forms unresolvable, so its valid [[link]] was demoted on every write."""
    index = _colliding_index()
    assert index.resolve("entities/bugatti-divo") == "entities/bugatti-divo"
    assert index.resolve("projects/bugatti-divo") == "projects/bugatti-divo"


def test_rewrite_keeps_both_colliding_dir_prefixed_links() -> None:
    body = (
        "Owner of [[entities/bugatti-divo]]; "
        "purchase tracked in [[projects/bugatti-divo]]."
    )
    new_body, resolved = rewrite_body_links(body, _colliding_index())
    assert "[[entities/bugatti-divo]]" in new_body
    assert "[[projects/bugatti-divo]]" in new_body
    assert set(resolved) == {"entities/bugatti-divo", "projects/bugatti-divo"}


# ---------------------------------------------------------------------------
# promote_plaintext_links — healing old demotion scars
# ---------------------------------------------------------------------------


def test_promote_rewraps_existing_page_path() -> None:
    body = "## Relationships\n\n- entities/bugatti-divo — Owner.\n"
    new_body, promoted = promote_plaintext_links(body, _colliding_index())
    assert promoted == 1
    assert "- [[entities/bugatti-divo]] — Owner.\n" in new_body


def test_promote_accepts_md_suffixed_spelling() -> None:
    body = "See projects/bugatti-divo.md for the purchase.\n"
    new_body, promoted = promote_plaintext_links(body, _colliding_index())
    assert promoted == 1
    assert "See [[projects/bugatti-divo]] for the purchase.\n" == new_body


def test_promote_leaves_unknown_paths_alone() -> None:
    body = "- entities/never-created — gone.\n"
    new_body, promoted = promote_plaintext_links(body, _colliding_index())
    assert promoted == 0
    assert new_body == body


def test_promote_never_touches_existing_links_or_frontmatter() -> None:
    body = (
        "---\n"
        "type: entity\n"
        "slug: ruben\n"
        "note: entities/bugatti-divo\n"
        "---\n"
        "\n"
        "- [[entities/bugatti-divo]] already linked\n"
        "- entities/bugatti-divo scar\n"
    )
    new_body, promoted = promote_plaintext_links(body, _colliding_index())
    assert promoted == 1
    assert "note: entities/bugatti-divo\n" in new_body  # frontmatter untouched
    assert new_body.count("[[entities/bugatti-divo]]") == 2  # link + healed scar


def test_promote_skips_backticked_and_url_spans() -> None:
    body = (
        "Run `entities/bugatti-divo` manually; "
        "see https://example.com/entities/bugatti-divo too.\n"
    )
    new_body, promoted = promote_plaintext_links(body, _colliding_index())
    assert promoted == 0
    assert new_body == body


def test_footer_order_is_deterministic() -> None:
    a = build_related_footer(
        hub_links=["entities/ruben", "projects/personal-jarvis"],
        resolved_targets=["concepts/wiki-curator"],
    )
    b = build_related_footer(
        hub_links=["projects/personal-jarvis", "entities/ruben"],
        resolved_targets=["concepts/wiki-curator"],
    )
    # Hubs keep caller order; the rendered block is stable for a given input.
    assert a == build_related_footer(
        hub_links=["entities/ruben", "projects/personal-jarvis"],
        resolved_targets=["concepts/wiki-curator"],
    )
    assert "[[projects/personal-jarvis]]" in b
