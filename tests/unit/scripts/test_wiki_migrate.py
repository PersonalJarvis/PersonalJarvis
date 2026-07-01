"""Tests for ``scripts/wiki_migrate_v0_to_v1.py``.

The migration script is standalone (no jarvis imports) so the tests
exercise it via direct function calls + a temp filesystem. No CI flake
from real workspace state.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def migrate_module():
    """Load scripts/wiki_migrate_v0_to_v1.py as a module."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    script_path = repo_root / "scripts" / "wiki_migrate_v0_to_v1.py"
    spec = importlib.util.spec_from_file_location("wiki_migrate", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["wiki_migrate"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault(target: Path) -> None:
    """Create a minimal target vault that satisfies the script's preflight."""
    target.mkdir(parents=True, exist_ok=True)
    (target / "schema.md").write_text("# schema stub\n", encoding="utf-8")
    (target / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (target / "index.md").write_text(
        "# Index\n\n"
        "## Entities\n\n"
        "*People, tools, repositories, services, devices.*\n\n"
        "(empty — populated by migration.)\n",
        encoding="utf-8",
    )


def _make_legacy(source: Path) -> None:
    """Plant a representative legacy workspace under ``source``."""
    source.mkdir(parents=True, exist_ok=True)
    (source / "USER.md").write_text(
        "---\nname: Ruben\naliases: ruby\n---\n\n# Ruben\n\nProfile body.\n",
        encoding="utf-8",
    )
    (source / "SOUL.md").write_text(
        "---\nname: Jarvis\n---\n\n# Jarvis\n\nPersona body.\n",
        encoding="utf-8",
    )
    people = source / "people"
    people.mkdir()
    (people / "mama.md").write_text(
        "---\nname: Mama\n---\n\n# Mama\n\nPerson body.\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

def test_slugify_lowercases_and_replaces_separators(migrate_module) -> None:
    assert migrate_module.slugify("Jürgen Müller") == "juergen-mueller"  # i18n-allow: umlaut-transliteration test fixture, not translatable prose


def test_slugify_strips_punctuation(migrate_module) -> None:
    assert migrate_module.slugify("Open Claw!") == "open-claw"


def test_slugify_falls_back_when_empty(migrate_module) -> None:
    assert migrate_module.slugify("   ") == "unnamed"


# ---------------------------------------------------------------------------
# parse_legacy_doc
# ---------------------------------------------------------------------------

def test_parse_legacy_doc_with_frontmatter(tmp_path: Path, migrate_module) -> None:
    p = tmp_path / "x.md"
    p.write_text("---\nname: Foo\n---\n\n# Body\n", encoding="utf-8")
    doc = migrate_module.parse_legacy_doc(p)
    assert doc.frontmatter == {"name": "Foo"}
    assert "# Body" in doc.body


def test_parse_legacy_doc_without_frontmatter(tmp_path: Path, migrate_module) -> None:
    p = tmp_path / "x.md"
    p.write_text("# Just body\n", encoding="utf-8")
    doc = migrate_module.parse_legacy_doc(p)
    assert doc.frontmatter == {}
    assert "Just body" in doc.body


# ---------------------------------------------------------------------------
# plan_migrations
# ---------------------------------------------------------------------------

def test_plan_migrations_emits_three_entries_for_full_legacy(
    tmp_path: Path, migrate_module
) -> None:
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    _make_legacy(source)
    _make_vault(target)

    plans = migrate_module.plan_migrations(source, target)
    targets = sorted(p.target.name for p in plans)
    assert targets == ["jarvis-persona.md", "mama.md", "ruben.md"]


def test_plan_migrations_empty_when_no_legacy(
    tmp_path: Path, migrate_module
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    target = tmp_path / "tgt"
    _make_vault(target)
    assert migrate_module.plan_migrations(source, target) == []


# ---------------------------------------------------------------------------
# End-to-end apply
# ---------------------------------------------------------------------------

def test_apply_creates_entity_pages_with_legacy_body_preserved(
    tmp_path: Path, migrate_module
) -> None:
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    backups = tmp_path / "backups"
    _make_legacy(source)
    _make_vault(target)

    rc = migrate_module.main(
        [
            "--source", str(source),
            "--target", str(target),
            "--backup-dir", str(backups),
            "--apply",
        ]
    )
    assert rc == 0

    ruben_page = target / "entities" / "ruben.md"
    assert ruben_page.exists()
    content = ruben_page.read_text(encoding="utf-8")
    assert "type: entity" in content
    assert "entity_kind: person" in content
    assert "Profile body." in content      # legacy verbatim block
    assert "## Summary" in content          # schema-shape sections present
    assert "## Sources" in content


def test_apply_creates_backup_tarball(
    tmp_path: Path, migrate_module
) -> None:
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    backups = tmp_path / "backups"
    _make_legacy(source)
    _make_vault(target)

    migrate_module.main(
        [
            "--source", str(source),
            "--target", str(target),
            "--backup-dir", str(backups),
            "--apply",
        ]
    )
    tarballs = list(backups.glob("wiki-migrate-*.tar.gz"))
    assert len(tarballs) == 1
    assert tarballs[0].stat().st_size > 0


def test_apply_is_idempotent(tmp_path: Path, migrate_module) -> None:
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    backups = tmp_path / "backups"
    _make_legacy(source)
    _make_vault(target)

    args = [
        "--source", str(source),
        "--target", str(target),
        "--backup-dir", str(backups),
        "--apply",
    ]
    migrate_module.main(args)
    mtimes_after_first = {
        p: p.stat().st_mtime_ns for p in (target / "entities").glob("*.md")
    }

    # Second run must skip everything and leave files alone.
    rc = migrate_module.main(args)
    assert rc == 0
    for p, mtime in mtimes_after_first.items():
        assert p.stat().st_mtime_ns == mtime, f"{p.name} was modified on second run"


def test_apply_appends_log_entry(tmp_path: Path, migrate_module) -> None:
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    backups = tmp_path / "backups"
    _make_legacy(source)
    _make_vault(target)

    migrate_module.main(
        [
            "--source", str(source),
            "--target", str(target),
            "--backup-dir", str(backups),
            "--apply",
        ]
    )
    log = (target / "log.md").read_text(encoding="utf-8")
    assert "migrate | legacy flat workspace" in log
    assert "[[entities/ruben]]" in log


def test_apply_updates_index_entities_section(
    tmp_path: Path, migrate_module
) -> None:
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    backups = tmp_path / "backups"
    _make_legacy(source)
    _make_vault(target)

    migrate_module.main(
        [
            "--source", str(source),
            "--target", str(target),
            "--backup-dir", str(backups),
            "--apply",
        ]
    )
    idx = (target / "index.md").read_text(encoding="utf-8")
    assert "[[entities/ruben]]" in idx
    assert "[[entities/jarvis-persona]]" in idx
    assert "[[entities/mama]]" in idx
    assert "(empty" not in idx     # placeholder gone


# ---------------------------------------------------------------------------
# Safety preflight
# ---------------------------------------------------------------------------

def test_main_refuses_if_target_has_no_schema_md(
    tmp_path: Path, migrate_module, capsys
) -> None:
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    _make_legacy(source)
    target.mkdir()                 # exists but no schema.md
    rc = migrate_module.main(
        [
            "--source", str(source),
            "--target", str(target),
            "--apply",
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "schema.md not found" in err


def test_dry_run_writes_nothing(tmp_path: Path, migrate_module) -> None:
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    backups = tmp_path / "backups"
    _make_legacy(source)
    _make_vault(target)

    migrate_module.main(
        [
            "--source", str(source),
            "--target", str(target),
            "--backup-dir", str(backups),
            "--dry-run",
        ]
    )
    assert not (target / "entities").exists() or not list((target / "entities").glob("*.md"))
    assert not backups.exists() or not list(backups.glob("*.tar.gz"))
