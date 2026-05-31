"""Unit tests for ``jarvis.memory.wiki.wikilink``.

Covers extraction (all four documented forms + escape + edge cases)
and resolution (short form, explicit-prefix form, ambiguous, missing).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.memory.wiki.wikilink import (
    SEARCHABLE_DIRS,
    extract_wikilinks,
    resolve_wikilink,
)


# ──────────────────────────────────────────────────────────────────────
# extract_wikilinks
# ──────────────────────────────────────────────────────────────────────


def test_extract_empty_body_returns_empty_tuple() -> None:
    assert extract_wikilinks("") == ()


def test_extract_single_short_link() -> None:
    assert extract_wikilinks("See [[the maintainer]] here.") == ("the maintainer",)


def test_extract_explicit_prefix_form() -> None:
    assert extract_wikilinks("Related: [[entities/the maintainer]].") == ("entities/the maintainer",)


def test_extract_aliased_form_canonical_strips_alias() -> None:
    assert extract_wikilinks("Talk to [[the maintainer|the user]].") == ("the maintainer",)


def test_extract_prefix_plus_alias() -> None:
    body = "See [[concepts/awareness-layer|the layer]] for details."
    assert extract_wikilinks(body) == ("concepts/awareness-layer",)


def test_extract_escaped_link_is_ignored() -> None:
    # A single backslash before [[ marks the link as escaped.
    assert extract_wikilinks(r"Literal: \[[not a link]] here.") == ()


def test_extract_multiple_links_order_preserved() -> None:
    body = "First [[a]], then [[b]], finally [[c]]."
    assert extract_wikilinks(body) == ("a", "b", "c")


def test_extract_duplicates_kept_in_order() -> None:
    body = "[[the maintainer]] talked to [[the maintainer]] about [[claude]]."
    assert extract_wikilinks(body) == ("the maintainer", "the maintainer", "claude")


def test_extract_empty_link_ignored() -> None:
    assert extract_wikilinks("Dangling [[]] should not appear.") == ()


def test_extract_whitespace_only_link_ignored() -> None:
    # `[[ ]]` matches the regex but canonicalises to "" — skipped.
    assert extract_wikilinks("Empty [[   ]] gap.") == ()


def test_extract_does_not_span_newline() -> None:
    # A link must be on a single line — newlines abort the match.
    body = "Broken [[foo\nbar]] reference."
    assert extract_wikilinks(body) == ()


def test_extract_adjacent_links() -> None:
    assert extract_wikilinks("[[a]][[b]]") == ("a", "b")


def test_extract_link_with_internal_whitespace_preserved() -> None:
    # Internal whitespace inside the slug is preserved (no normalisation).
    # The schema discourages it but the parser tolerates anything that is
    # not `]` or `\n`.
    assert extract_wikilinks("[[foo bar]]") == ("foo bar",)


# ──────────────────────────────────────────────────────────────────────
# resolve_wikilink
# ──────────────────────────────────────────────────────────────────────


def _make_vault(tmp_path: Path) -> Path:
    for sub in SEARCHABLE_DIRS:
        (tmp_path / sub).mkdir()
    return tmp_path


def test_resolve_short_form_unique_match(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    (vault / "entities" / "the maintainer.md").write_text("x", encoding="utf-8")
    assert resolve_wikilink("the maintainer", vault) == vault / "entities" / "the maintainer.md"


def test_resolve_explicit_prefix(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    (vault / "concepts" / "awareness.md").write_text("x", encoding="utf-8")
    assert (
        resolve_wikilink("concepts/awareness", vault)
        == vault / "concepts" / "awareness.md"
    )


def test_resolve_short_form_ambiguous_returns_none(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    (vault / "entities" / "voice.md").write_text("x", encoding="utf-8")
    (vault / "concepts" / "voice.md").write_text("x", encoding="utf-8")
    assert resolve_wikilink("voice", vault) is None


def test_resolve_explicit_prefix_picks_the_right_one_when_ambiguous(
    tmp_path: Path,
) -> None:
    vault = _make_vault(tmp_path)
    (vault / "entities" / "voice.md").write_text("x", encoding="utf-8")
    (vault / "concepts" / "voice.md").write_text("x", encoding="utf-8")
    assert (
        resolve_wikilink("concepts/voice", vault)
        == vault / "concepts" / "voice.md"
    )


def test_resolve_missing_returns_none(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    assert resolve_wikilink("nobody-here", vault) is None


def test_resolve_alias_is_stripped_before_resolution(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    (vault / "entities" / "the maintainer.md").write_text("x", encoding="utf-8")
    assert (
        resolve_wikilink("the maintainer|the user", vault)
        == vault / "entities" / "the maintainer.md"
    )


def test_resolve_empty_link_returns_none(tmp_path: Path) -> None:
    assert resolve_wikilink("", tmp_path) is None


def test_resolve_explicit_prefix_with_missing_file_returns_none(
    tmp_path: Path,
) -> None:
    vault = _make_vault(tmp_path)
    assert resolve_wikilink("entities/ghost", vault) is None
