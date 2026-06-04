"""Unit-Tests fuer den Doc-Loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.docs.loader import (
    _slugify,
    _split_frontmatter,
    discover_docs,
    parse_doc,
)
from jarvis.docs.schema import DocDiataxis, DocStatus


VALID_DOC_MD = """---
title: "Concept: Router-Discipline"
slug: router-discipline
diataxis: explanation
status: active
owner: sam
last_reviewed: 2026-04-28
phase: 5
tags: [brain, routing]
---

# Concept: Router-Discipline

Hauptjarvis ist Pure Dispatcher.

## Wann triggern

- Direkt-Aktion via Sub-Jarvis-Spawn

## Hard Rules

`spawn_worker` darf NIE in `SUB_TOOLS` landen.
"""


LEGACY_NO_FRONTMATTER_MD = """# Phase-6 Test-Report

Branch: phase6-self-healing
Tests: 426/426 gruen

## Befunde

- Alle Subphasen 1-5 abgeschlossen
"""


BROKEN_YAML_MD = """---
title: "missing close-quote
slug: broken
---

# Body
"""


PARTIAL_FRONTMATTER_MD = """---
diataxis: howto
phase: 6
---

# How-To: Worker spawnen

Die Datei hat keinen ``title`` und keinen ``slug`` — Loader muss
synthetisieren.
"""


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def test_slugify_basic() -> None:
    assert _slugify("Hello World") == "hello-world"
    assert _slugify("Concept: Router-Discipline") == "concept-router-discipline"
    assert _slugify("Über uns") == "ueber-uns"
    assert _slugify("   ") == "untitled"


def test_split_frontmatter_present() -> None:
    meta, body = _split_frontmatter(VALID_DOC_MD)
    assert meta["title"] == "Concept: Router-Discipline"
    assert meta["slug"] == "router-discipline"
    assert "Hauptjarvis ist Pure Dispatcher" in body


def test_split_frontmatter_absent() -> None:
    meta, body = _split_frontmatter(LEGACY_NO_FRONTMATTER_MD)
    assert meta == {}
    assert body == LEGACY_NO_FRONTMATTER_MD


# ----------------------------------------------------------------------
# parse_doc — Happy-Path
# ----------------------------------------------------------------------

def test_parse_doc_full_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "good.md"
    p.write_text(VALID_DOC_MD, encoding="utf-8")
    doc = parse_doc(p, root=tmp_path)
    assert doc.error is None
    assert doc.frontmatter.title == "Concept: Router-Discipline"
    assert doc.frontmatter.slug == "router-discipline"
    assert doc.frontmatter.diataxis == DocDiataxis.EXPLANATION
    assert doc.frontmatter.status == DocStatus.ACTIVE
    assert doc.frontmatter.phase == "5"
    assert "brain" in doc.frontmatter.tags
    # Headings extrahiert mit Slug + Level
    assert any(h[2] == "wann-triggern" and h[0] == 2 for h in doc.headings)
    assert any(h[2] == "hard-rules" and h[0] == 2 for h in doc.headings)


# ----------------------------------------------------------------------
# parse_doc — Legacy / fehlendes Frontmatter
# ----------------------------------------------------------------------

def test_parse_doc_no_frontmatter_synthesized(tmp_path: Path) -> None:
    p = tmp_path / "phase6-test-report.md"
    p.write_text(LEGACY_NO_FRONTMATTER_MD, encoding="utf-8")
    doc = parse_doc(p, root=tmp_path)
    assert doc.error is None
    # Synth-Frontmatter
    assert doc.frontmatter.diataxis == DocDiataxis.UNCLASSIFIED
    assert doc.frontmatter.status == DocStatus.ACTIVE  # Legacy gilt als active
    assert doc.frontmatter.slug == "phase6-test-report"
    assert "phase6 test report" in doc.frontmatter.title.lower()


def test_parse_doc_partial_frontmatter_completes(tmp_path: Path) -> None:
    p = tmp_path / "worker-spawnen.md"
    p.write_text(PARTIAL_FRONTMATTER_MD, encoding="utf-8")
    doc = parse_doc(p, root=tmp_path)
    assert doc.error is None
    assert doc.frontmatter.diataxis == DocDiataxis.HOWTO
    assert doc.frontmatter.phase == "6"
    # title + slug aus Synth ergaenzt
    assert "worker spawnen" in doc.frontmatter.title.lower()
    assert doc.frontmatter.slug == "worker-spawnen"


# ----------------------------------------------------------------------
# parse_doc — Error-Pfade (niemals raise)
# ----------------------------------------------------------------------

def test_parse_doc_broken_yaml_returns_error(tmp_path: Path) -> None:
    p = tmp_path / "broken.md"
    p.write_text(BROKEN_YAML_MD, encoding="utf-8")
    doc = parse_doc(p, root=tmp_path)
    assert doc.error is not None
    # Synth-Frontmatter trotzdem gesetzt
    assert doc.frontmatter.diataxis == DocDiataxis.UNCLASSIFIED
    assert doc.frontmatter.slug == "broken"


def test_parse_doc_unreadable_returns_error(tmp_path: Path) -> None:
    p = tmp_path / "ghost.md"
    # Datei existiert nicht — read() raised OSError
    doc = parse_doc(p, root=tmp_path)
    assert doc.error is not None
    assert "read failed" in doc.error
    assert doc.body == ""


# ----------------------------------------------------------------------
# discover_docs
# ----------------------------------------------------------------------

@pytest.fixture
def doc_tree(tmp_path: Path) -> Path:
    """Baut einen typischen Jarvis-Doc-Tree mit Mix aus modern/legacy."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "adr").mkdir()
    (tmp_path / "Latenz").mkdir()
    # Versteckter Ordner — soll ausgeschlossen werden
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "docs" / "concept-routing.md").write_text(
        VALID_DOC_MD, encoding="utf-8"
    )
    (tmp_path / "docs" / "phase6-test-report.md").write_text(
        LEGACY_NO_FRONTMATTER_MD, encoding="utf-8"
    )
    (tmp_path / "docs" / "adr" / "0009-self-healing.md").write_text(
        "# ADR-0009\n\nContent.\n", encoding="utf-8"
    )
    (tmp_path / "Latenz" / "PHASE_L_P.md").write_text(
        "# Phase L+P\n\nLatenz-Optimierung.\n", encoding="utf-8"
    )
    # SKILL.md soll ausgeschlossen werden
    (tmp_path / "docs" / "SKILL.md").write_text("---\nname: foo\n---\n", encoding="utf-8")
    # .git-File soll ausgeschlossen werden
    (tmp_path / ".git" / "config.md").write_text("# git config\n", encoding="utf-8")
    # node_modules-File soll ausgeschlossen werden
    (tmp_path / "node_modules" / "readme.md").write_text("# node\n", encoding="utf-8")
    # Andere Extensions ignorieren
    (tmp_path / "docs" / "data.txt").write_text("foo\n", encoding="utf-8")
    return tmp_path


def test_discover_docs_finds_md_files(doc_tree: Path) -> None:
    docs = discover_docs([doc_tree / "docs", doc_tree / "Latenz"])
    slugs = {d.frontmatter.slug for d in docs}
    # Modern + 2 Legacy + ADR + Phase-LP
    assert "router-discipline" in slugs
    assert any("phase6" in s for s in slugs)
    assert any("0009" in s or "self-healing" in s for s in slugs)
    assert any("phase-l-p" in s for s in slugs)


def test_discover_docs_excludes_skill_md(doc_tree: Path) -> None:
    docs = discover_docs([doc_tree / "docs"])
    paths = [d.path.name for d in docs]
    assert "SKILL.md" not in paths


def test_discover_docs_excludes_hidden_dirs(doc_tree: Path) -> None:
    docs = discover_docs([doc_tree])
    paths = [str(d.path) for d in docs]
    assert not any(".git" in p for p in paths)
    assert not any("node_modules" in p for p in paths)


def test_discover_docs_excludes_non_md_files(doc_tree: Path) -> None:
    docs = discover_docs([doc_tree / "docs"])
    paths = [d.path.name for d in docs]
    assert "data.txt" not in paths


def test_discover_docs_handles_missing_root(tmp_path: Path) -> None:
    nonexistent = tmp_path / "does-not-exist"
    docs = discover_docs([nonexistent])
    assert docs == []


def test_discover_docs_dedupes_overlapping_roots(doc_tree: Path) -> None:
    """Wenn zwei Roots ueberlappen, jeder File nur einmal."""
    docs = discover_docs([doc_tree / "docs", doc_tree / "docs" / "adr"])
    slugs = [d.frontmatter.slug for d in docs]
    assert len(slugs) == len(set(slugs))
