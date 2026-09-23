"""Validated visual companion settings inside the existing avatar JSON envelope."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
