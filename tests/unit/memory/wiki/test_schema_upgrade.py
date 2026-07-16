"""Compatibility checks for the expanded Wiki entity taxonomy."""

from __future__ import annotations

from pathlib import Path

from jarvis.memory.wiki.integration import _ensure_schema_present

OLD_KINDS = "entity_kind: person | tool | repository | service | device"
NEW_KINDS = (
    "entity_kind: person | tool | repository | service | device | asset | "
    "vehicle | place | organization"
)


def test_existing_canonical_schema_is_upgraded_atomically(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.md"
    schema_path.write_text(f"# Schema\n\n{OLD_KINDS}\n", encoding="utf-8")

    _ensure_schema_present(tmp_path, schema_path)

    body = schema_path.read_text(encoding="utf-8")
    assert OLD_KINDS not in body.splitlines()
    assert NEW_KINDS in body.splitlines()
    assert not (tmp_path / ".schema.md.upgrade.tmp").exists()


def test_custom_schema_without_canonical_line_is_not_rewritten(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.md"
    original = "# My custom schema\n\nentity_kind: person | vessel\n"
    schema_path.write_text(original, encoding="utf-8")

    _ensure_schema_present(tmp_path, schema_path)

    assert schema_path.read_text(encoding="utf-8") == original


def test_fresh_schema_contains_expanded_entity_taxonomy(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.md"

    _ensure_schema_present(tmp_path, schema_path)

    assert NEW_KINDS in schema_path.read_text(encoding="utf-8")
