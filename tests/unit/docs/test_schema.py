"""Unit tests for DocFrontmatter + doc schema."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.docs.schema import Doc, DocDiataxis, DocFrontmatter, DocStatus


# ----------------------------------------------------------------------
# DocFrontmatter — Happy-Path
# ----------------------------------------------------------------------

def test_frontmatter_minimal_valid() -> None:
    fm = DocFrontmatter(title="Hello World", slug="hello-world")
    assert fm.title == "Hello World"
    assert fm.slug == "hello-world"
    assert fm.diataxis == DocDiataxis.UNCLASSIFIED
    assert fm.status == DocStatus.DRAFT
    assert fm.owner == "harald"
    assert fm.phase == "-"
    assert fm.tags == []


def test_frontmatter_full() -> None:
    fm = DocFrontmatter(
        title="Concept: Router-Discipline",
        slug="router-discipline",
        diataxis="explanation",
        status="active",
        last_reviewed=date(2026, 4, 28),
        phase="5",
        audience="developer",
        tags=["brain", "routing"],
        related=["adr-0011-router-discipline"],
    )
    assert fm.diataxis == DocDiataxis.EXPLANATION
    assert fm.status == DocStatus.ACTIVE
    assert fm.last_reviewed == date(2026, 4, 28)
    assert fm.tags == ["brain", "routing"]


# ----------------------------------------------------------------------
# DocFrontmatter — Tolerance & Coercion
# ----------------------------------------------------------------------

def test_frontmatter_phase_int_coerced_to_str() -> None:
    fm = DocFrontmatter(title="t", slug="s", phase=5)
    assert fm.phase == "5"


def test_frontmatter_phase_none_becomes_dash() -> None:
    fm = DocFrontmatter(title="t", slug="s", phase=None)  # type: ignore[arg-type]
    assert fm.phase == "-"


def test_frontmatter_phase_alphanumeric_kept() -> None:
    fm = DocFrontmatter(title="t", slug="s", phase="1a")
    assert fm.phase == "1a"


def test_frontmatter_tags_csv_string_coerced() -> None:
    fm = DocFrontmatter(title="t", slug="s", tags="brain, routing, plugin")  # type: ignore[arg-type]
    assert fm.tags == ["brain", "routing", "plugin"]


def test_frontmatter_tags_none_becomes_empty_list() -> None:
    fm = DocFrontmatter(title="t", slug="s", tags=None)  # type: ignore[arg-type]
    assert fm.tags == []


def test_frontmatter_extra_fields_ignored() -> None:
    """``extra='ignore'`` must swallow unknown fields — no crash."""
    fm = DocFrontmatter.model_validate({
        "title": "t",
        "slug": "s",
        "unknown_future_field": "value",
        "another": [1, 2, 3],
    })
    assert fm.title == "t"


# ----------------------------------------------------------------------
# DocFrontmatter — Validation-Failures
# ----------------------------------------------------------------------

def test_frontmatter_empty_title_rejected() -> None:
    with pytest.raises(ValidationError):
        DocFrontmatter(title="   ", slug="s")


def test_frontmatter_empty_slug_rejected() -> None:
    with pytest.raises(ValidationError):
        DocFrontmatter(title="t", slug="")


def test_frontmatter_invalid_diataxis_rejected() -> None:
    with pytest.raises(ValidationError):
        DocFrontmatter.model_validate({
            "title": "t",
            "slug": "s",
            "diataxis": "fancy-new-quadrant",
        })


def test_frontmatter_invalid_status_rejected() -> None:
    with pytest.raises(ValidationError):
        DocFrontmatter.model_validate({
            "title": "t",
            "slug": "s",
            "status": "halffinished",
        })


# ----------------------------------------------------------------------
# Doc DataClass
# ----------------------------------------------------------------------

def test_doc_property_shortcuts() -> None:
    fm = DocFrontmatter(title="Hello", slug="hello", diataxis="howto")
    doc = Doc(path=Path("/tmp/hello.md"), frontmatter=fm, body="# Hello\nbody.")
    assert doc.slug == "hello"
    assert doc.title == "Hello"
    assert doc.diataxis == DocDiataxis.HOWTO


def test_doc_is_frozen() -> None:
    fm = DocFrontmatter(title="t", slug="s")
    doc = Doc(path=Path("/tmp/t.md"), frontmatter=fm, body="")
    with pytest.raises(Exception):
        doc.body = "mutated"  # type: ignore[misc]
