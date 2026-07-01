"""Unit tests for `jarvis.memory.frontmatter`.

Parse/write roundtrips, section replace/append, error resilience against broken
YAML.
"""
from __future__ import annotations

from jarvis.memory.frontmatter import (
    append_to_section,
    parse_frontmatter,
    replace_section,
    write_frontmatter,
)


# ======================================================================
# parse_frontmatter / write_frontmatter
# ======================================================================

class TestParseAndWrite:
    """Roundtrip und Edge Cases."""

    def test_roundtrip_preserves_data(self) -> None:
        meta = {
            "schema_version": 1,
            "identity": {"name": "Ruben", "languages": ["de", "en"]},
        }
        body = "# Body\n\nSome text."
        text = write_frontmatter(meta, body)

        parsed_meta, parsed_body = parse_frontmatter(text)
        assert parsed_meta == meta
        assert "# Body" in parsed_body
        assert "Some text." in parsed_body

    def test_body_without_frontmatter_returns_empty_meta(self) -> None:
        """A legacy file without frontmatter must not crash."""
        text = "# Just Markdown\n\nNo frontmatter here."
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_empty_frontmatter_parseable(self) -> None:
        """Leeres `---\\n---` ist valides YAML (= None → leeres Dict)."""
        text = "---\n---\n\n# Body"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert "# Body" in body

    def test_malformed_yaml_returns_empty_meta_and_preserves_body(self) -> None:
        """Broken YAML → empty dict, body is preserved (does not raise)."""
        text = "---\nkey: : invalid: yaml: [\n---\n\n# Body retained"
        meta, body = parse_frontmatter(text)
        # Empty dict on parse error
        assert meta == {}
        # The body must be preserved so we don't lose data
        assert "# Body retained" in body

    def test_missing_closing_delim_falls_back_to_no_frontmatter(self) -> None:
        """No second `---` → don't parse frontmatter, body == original."""
        text = "---\nkey: value\n\nNo close delim\n"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_non_dict_yaml_becomes_raw(self) -> None:
        """YAML that is only a list/number → we wrap it as `_raw`."""
        text = "---\n- one\n- two\n---\n\n# Body"
        meta, body = parse_frontmatter(text)
        assert meta.get("_raw") == ["one", "two"]
        assert "# Body" in body

    def test_crlf_line_endings_supported(self) -> None:
        text = "---\r\nkey: value\r\n---\r\n\r\n# Body"
        meta, body = parse_frontmatter(text)
        assert meta == {"key": "value"}
        # Body contains the rest (possibly with CR)
        assert "Body" in body


# ======================================================================
# replace_section
# ======================================================================

class TestReplaceSection:
    """`replace_section` swaps the content between the start and end markers."""

    def test_replace_content_between_markers(self) -> None:
        body = (
            "# Titel\n\n"
            "## Context\n\n"
            "<!-- curator:context:start -->\n"
            "OLD CONTENT\n"
            "<!-- curator:context:end -->\n"
        )
        result = replace_section(body, "context", "NEW CONTENT")
        assert "NEW CONTENT" in result
        assert "OLD CONTENT" not in result
        # Marker bleiben erhalten
        assert "<!-- curator:context:start -->" in result
        assert "<!-- curator:context:end -->" in result

    def test_no_markers_leaves_body_unchanged(self) -> None:
        """Without markers → body returned unchanged (we log, don't raise)."""
        body = "# Titel\n\nKein Marker hier."
        result = replace_section(body, "context", "NEW")
        assert result == body

    def test_empty_content_leaves_markers_in_place(self) -> None:
        body = (
            "<!-- curator:x:start -->\n"
            "stuff\n"
            "<!-- curator:x:end -->"
        )
        result = replace_section(body, "x", "")
        # Marker erhalten, Inhalt weg
        assert "<!-- curator:x:start -->" in result
        assert "<!-- curator:x:end -->" in result
        assert "stuff" not in result


# ======================================================================
# append_to_section
# ======================================================================

class TestAppendToSection:
    """`append_to_section` haengt eine Zeile dedupliziert an."""

    def test_appends_new_line(self) -> None:
        body = (
            "<!-- curator:observations:start -->\n"
            "- [2026-04-20] foo: bar\n"
            "<!-- curator:observations:end -->"
        )
        result = append_to_section(body, "observations", "- [2026-04-21] baz: qux")
        assert "- [2026-04-20] foo: bar" in result
        assert "- [2026-04-21] baz: qux" in result

    def test_dedupes_identical_line(self) -> None:
        """Identical line → not appended again."""
        body = (
            "<!-- curator:observations:start -->\n"
            "- [2026-04-20] foo: bar\n"
            "<!-- curator:observations:end -->"
        )
        result = append_to_section(body, "observations", "- [2026-04-20] foo: bar")
        # Only one instance of the line
        assert result.count("- [2026-04-20] foo: bar") == 1

    def test_append_into_empty_section(self) -> None:
        body = (
            "<!-- curator:observations:start -->\n"
            "<!-- curator:observations:end -->"
        )
        result = append_to_section(body, "observations", "- [2026-04-21] first")
        assert "- [2026-04-21] first" in result

    def test_no_markers_leaves_body_unchanged(self) -> None:
        body = "# Title, no markers"
        result = append_to_section(body, "observations", "- foo")
        assert result == body
