"""Fail-closed contract for figure recipes that may enter a shared catalog.

A local imported GLB is trusted only on the machine that validated it. Shared
figures have a narrower boundary: a JSON recipe may reference reviewed built-in
catalog assets, but it may never carry a model URL or other opaque payload. The
local M6 catalog publishes only this normalized recipe metadata; reports and
delisting stay local and no model is uploaded to a remote marketplace.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .companion import FigureAppearance

PUBLIC_FIGURE_LICENSES = frozenset({"first-party", "CC0-1.0"})
_RESERVED_FIGURES = frozenset({("spirit", "gigi")})
_ALLOWED_RECIPE_KEYS = frozenset(
    {
        "contract",
        "archetype",
        "base",
        "parts",
        "palette",
        "heightM",
        "style",
        "hairStyle",
        "outfit",
        "inner",
        "eyewear",
    }
)
_SHARE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")


class SharedFigureDraft(BaseModel):
    """Metadata + recipe that is safe to hand to a future public catalog."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=80)
    license: str = Field(min_length=1, max_length=40)
    source: str = Field(min_length=1, max_length=500)
    recipe: dict[str, Any]

    @field_validator("name")
    @classmethod
    def _safe_name(cls, value: str) -> str:
        clean = value.strip()
        if not _SHARE_NAME.fullmatch(clean):
            raise ValueError("invalid shared figure name")
        return clean

    @field_validator("license")
    @classmethod
    def _license_is_reviewed(cls, value: str) -> str:
        clean = value.strip()
        if clean not in PUBLIC_FIGURE_LICENSES:
            raise ValueError("shared figure license has not been reviewed")
        return clean

    @field_validator("source")
    @classmethod
    def _source_is_explicit(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("shared figures require provenance")
        return clean

    @model_validator(mode="after")
    def _recipe_is_shareable(self) -> "SharedFigureDraft":
        recipe = self.recipe
        if "model" in recipe:
            raise ValueError("shared figures cannot carry model URLs or GLB references")
        if "companion" in recipe:
            raise ValueError("companions are not part of a shared figure recipe")
        unknown = set(recipe) - _ALLOWED_RECIPE_KEYS
        if unknown:
            fields = ", ".join(sorted(unknown))
            raise ValueError(f"shared figure recipe has unsupported fields: {fields}")

        parsed = FigureAppearance.model_validate(recipe)
        if parsed.archetype is None:
            raise ValueError("shared figures require an explicit archetype")
        if parsed.contract != 1:
            raise ValueError("shared figures require recipe contract 1")
        key = (parsed.archetype or "", parsed.base)
        if key in _RESERVED_FIGURES:
            raise ValueError("this figure is reserved for the Jarvis lead")
        return self

    def normalized(self) -> dict[str, Any]:
        """Stable JSON-safe payload; no hidden Pydantic extras survive."""
        parsed = FigureAppearance.model_validate(self.recipe)
        recipe = {
            key: value
            for key, value in parsed.model_dump(exclude_none=True).items()
            if key in _ALLOWED_RECIPE_KEYS
        }
        return {
            "name": self.name,
            "license": self.license,
            "source": self.source,
            "recipe": recipe,
        }
