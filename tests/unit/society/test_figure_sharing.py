"""Shared avatar marketplace candidates stay recipe-only and license-gated."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.society.figure_sharing import SharedFigureDraft


def _draft(**overrides):
    value = {
        "name": "Olive scout",
        "license": "CC0-1.0",
        "source": "KayKit / recipe authored locally",
        "recipe": {
            "contract": 1,
            "archetype": "biped",
            "base": "rogue",
            "parts": {"head": "hood"},
            "palette": {"primary": "#315d45"},
            "style": "fantasy",
        },
    }
    value.update(overrides)
    return value


def test_shared_figure_normalizes_only_reviewed_recipe_fields():
    draft = SharedFigureDraft.model_validate(_draft())
    normalized = draft.normalized()
    assert normalized["license"] == "CC0-1.0"
    assert normalized["recipe"]["base"] == "rogue"
    assert "model" not in normalized["recipe"]


@pytest.mark.parametrize(
    "recipe",
    [
        {
            "contract": 1,
            "archetype": "biped",
            "base": "rogue",
            "parts": {},
            "model": "/api/society/figures/local.glb",
        },
        {
            "contract": 1,
            "archetype": "biped",
            "base": "rogue",
            "parts": {},
            "companion": {},
        },
        {
            "contract": 1,
            "archetype": "biped",
            "base": "rogue",
            "parts": {},
            "payload": "hidden",
        },
        {"contract": 1, "archetype": "spirit", "base": "gigi", "parts": {}},
    ],
)
def test_shared_figure_rejects_opaque_or_reserved_content(recipe):
    with pytest.raises(ValidationError):
        SharedFigureDraft.model_validate(_draft(recipe=recipe))


def test_shared_figure_rejects_unreviewed_license_and_missing_provenance():
    with pytest.raises(ValidationError):
        SharedFigureDraft.model_validate(_draft(license="CC-BY-4.0"))
    with pytest.raises(ValidationError):
        SharedFigureDraft.model_validate(_draft(source="   "))
