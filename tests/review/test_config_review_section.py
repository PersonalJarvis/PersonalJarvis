"""Tests fuer ReviewConfig-Pydantic-Validation (Phase 8.4).

Plan-Referenz: §6.4 — `[review]`-Section in jarvis.toml, Pydantic-
Validation grün.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.core.config import ReviewConfig, ReviewRubricConfig

REPO_ROOT = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------
# Default Construction
# ----------------------------------------------------------------------


def test_default_review_config() -> None:
    cfg = ReviewConfig()
    assert cfg.enabled is True
    assert cfg.max_iterations == 3
    assert cfg.hard_ceiling == 5
    assert cfg.worker_model == "sonnet"
    assert cfg.reviewer_model == "opus"
    assert cfg.default_rubric == "default"
    # 4 Rubrics aus Plan §6.4
    assert set(cfg.rubrics.keys()) == {
        "default",
        "code_generation",
        "skill_authoring",
        "research",
    }


def test_default_rubrics_have_items() -> None:
    cfg = ReviewConfig()
    for name, rubric in cfg.rubrics.items():
        assert rubric.items, f"rubric {name} has no items"
        for item in rubric.items:
            assert isinstance(item, str) and item.strip(), name


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


@pytest.mark.parametrize("bad_max", [0, 6, 10, -1])
def test_max_iterations_out_of_range_rejected(bad_max: int) -> None:
    """AD-4 hard ceiling 5; werte > 5 oder < 1 werden abgelehnt."""
    with pytest.raises(ValidationError):
        ReviewConfig(max_iterations=bad_max)


@pytest.mark.parametrize("bad_ceil", [0, 6, 100])
def test_hard_ceiling_out_of_range_rejected(bad_ceil: int) -> None:
    with pytest.raises(ValidationError):
        ReviewConfig(hard_ceiling=bad_ceil)


def test_gc_after_days_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ReviewConfig(gc_after_days=0)
    with pytest.raises(ValidationError):
        ReviewConfig(gc_after_days=-5)


def test_rubric_items_not_empty() -> None:
    with pytest.raises(ValidationError):
        ReviewRubricConfig(items=[])


# ----------------------------------------------------------------------
# Roundtrip mit jarvis.toml
# ----------------------------------------------------------------------


def test_jarvis_toml_review_section_exists_and_validates() -> None:
    """End-to-End: Die echte jarvis.toml muss eine [review]-Section haben,
    die ReviewConfig.model_validate akzeptiert."""
    jarvis_toml = REPO_ROOT / "jarvis.toml"
    assert jarvis_toml.exists()

    raw = tomllib.loads(jarvis_toml.read_text(encoding="utf-8"))
    assert "review" in raw, "jarvis.toml hat keine [review]-Section"

    cfg = ReviewConfig.model_validate(raw["review"])
    # Werte aus Plan §6.4
    assert cfg.enabled is True
    assert cfg.max_iterations == 3
    assert cfg.hard_ceiling == 5
    assert cfg.default_rubric == "default"

    # Alle 4 Rubrics aus Plan §6.4
    assert set(cfg.rubrics.keys()) == {
        "default",
        "code_generation",
        "skill_authoring",
        "research",
    }
