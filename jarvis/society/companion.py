"""Validated visual companion settings inside the existing avatar JSON envelope."""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompanionAppearance(BaseModel):
    """One identity for the profile and its presentation-only world follower."""

    model_config = ConfigDict(extra="forbid", strict=True)

    shape: Literal["circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop"]
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    eyes: Literal["dots", "lines"] = "dots"
    enabled: bool = True
    sizeM: float = Field(default=0.5, ge=0.25, le=0.8)
    followDistanceM: float = Field(default=1.0, ge=0.5, le=2.0)


def validate_avatar_companion(avatar: dict[str, Any]) -> dict[str, Any]:
    """Preserve character/import fields verbatim; validate only our namespace."""
    if "companion" not in avatar:
        return avatar
    companion = CompanionAppearance.model_validate(avatar["companion"])
    return {**avatar, "companion": companion.model_dump()}


_FIGURE_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,79}$"
_LOCAL_MODEL_PATTERN = r"^/api/society/figures/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.glb$"
_PALETTE_CELLS = {
    "skin",
    "skin_shade",
    "hair",
    "eyes",
    "primary",
    "primary_shade",
    "secondary",
    "secondary_shade",
    "accent",
    "metal",
    "leather",
    "fur",
    "fur_shade",
    "shoes",
    "eye_white",
    "emissive",
}


class FigureAppearance(BaseModel):
    """Security-sensitive subset of the persisted figure recipe.

    Extra presentation fields stay forward-compatible, but the fields that can
    select an asset or inflate the recipe are bounded here. Custom models must
    already have passed the local import route; arbitrary remote URLs are not
    valid persisted avatar state.
    """

    model_config = ConfigDict(extra="allow", strict=True)

    contract: Literal[1]
    archetype: Literal["biped", "quadruped", "spirit"] | None = None
    base: str = Field(min_length=1, max_length=80, pattern=_FIGURE_ID_PATTERN)
    parts: dict[str, str] = Field(default_factory=dict)
    palette: dict[str, str] | None = None
    heightM: float | None = Field(default=None, ge=0.1, le=5.0)
    model: str | None = Field(default=None, pattern=_LOCAL_MODEL_PATTERN)

    @field_validator("parts")
    @classmethod
    def _parts_are_bounded_ids(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 16:
            raise ValueError("too many figure parts")
        pattern = re.compile(_FIGURE_ID_PATTERN)
        for slot, part_id in value.items():
            if not pattern.fullmatch(slot) or not pattern.fullmatch(part_id):
                raise ValueError("invalid figure part id")
        return value

    @field_validator("palette")
    @classmethod
    def _palette_is_known_hex(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        if set(value) - _PALETTE_CELLS:
            raise ValueError("unknown palette cell")
        if any(re.fullmatch(r"#[0-9a-fA-F]{6}", color) is None for color in value.values()):
            raise ValueError("invalid palette colour")
        return value


def validate_avatar(avatar: dict[str, Any]) -> dict[str, Any]:
    """Validate the persisted avatar envelope without breaking legacy rows.

    Pre-contract avatars are preserved. Contract-v1 figure recipes are checked
    before persistence, including the same-origin custom-model boundary, while
    the companion namespace is validated by :func:`validate_avatar_companion`.
    """
    value = validate_avatar_companion(avatar)
    if "contract" in value:
        FigureAppearance.model_validate(value)
    return value
