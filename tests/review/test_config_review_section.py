"""Tests for ReviewConfig Pydantic validation (Phase 8.4).

Plan reference: §6.4 — `[review]` section in jarvis.toml, Pydantic
validation green.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.core.config import JarvisConfig, ReviewConfig, ReviewRubricConfig

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
    # 4 rubrics from Plan §6.4
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
    """AD-4 hard ceiling 5; values > 5 or < 1 are rejected."""
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
# Round trip with jarvis.toml
# ----------------------------------------------------------------------


def test_example_config_uses_valid_review_defaults() -> None:
    """The public example remains valid when the review section is omitted."""
    jarvis_toml = REPO_ROOT / "jarvis.toml.example"
    assert jarvis_toml.exists()

    raw = tomllib.loads(jarvis_toml.read_text(encoding="utf-8"))
    cfg = JarvisConfig.model_validate(raw).review
    # Values from Plan §6.4
    assert cfg.enabled is True
    assert cfg.max_iterations == 3
    assert cfg.hard_ceiling == 5
    assert cfg.default_rubric == "default"

    # All 4 rubrics from Plan §6.4
    assert set(cfg.rubrics.keys()) == {
        "default",
        "code_generation",
        "skill_authoring",
        "research",
    }
