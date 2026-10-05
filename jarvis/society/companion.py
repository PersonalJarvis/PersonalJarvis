"""Validated visual companion settings inside the existing avatar JSON envelope."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Mirrors ACCESSORY_SLOTS in the frontend catalog (parity test pins it). Item ids
# stay open-ended so a look saved by a newer build survives an older one.
AccessorySlot = Literal["back", "outfit", "neck", "mouth", "face", "head", "held"]
AccessoryId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,40}$")]


class CompanionAppearance(BaseModel):
    """One identity for the profile and its presentation-only world follower."""

    model_config = ConfigDict(extra="forbid", strict=True)

    shape: Literal["circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop"]
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    eyes: Literal["dots", "lines"] = "dots"
    enabled: bool = True
    accessories: dict[AccessorySlot, AccessoryId] = Field(default_factory=dict)
    sizeM: float = Field(default=0.5, ge=0.25, le=0.8)
    followDistanceM: float = Field(default=1.0, ge=0.5, le=2.0)


def validate_avatar_companion(avatar: dict[str, Any]) -> dict[str, Any]:
    """Preserve character/import fields verbatim; validate only our namespace."""
    if "companion" not in avatar:
        return avatar
    companion = CompanionAppearance.model_validate(avatar["companion"]).model_dump()
    # A bare look stays byte-identical to what builds without accessories stored.
    if not companion["accessories"]:
        del companion["accessories"]
    return {**avatar, "companion": companion}
