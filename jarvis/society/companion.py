"""Validated visual companion settings inside the existing avatar JSON envelope."""

import secrets
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Mirrors ACCESSORY_SLOTS in the frontend catalog (parity test pins it). Item ids
# stay open-ended so a look saved by a newer build survives an older one.
AccessorySlot = Literal["back", "outfit", "neck", "mouth", "face", "head", "held"]
AccessoryId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,40}$")]

#: Mirrors ``COMPANION_SHAPES`` / ``COMPANION_COLORS`` in the frontend's
#: ``components/society/companion/appearance.ts`` (parity test pins both).
COMPANION_SHAPES: Final[tuple[str, ...]] = (
    "circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop",
)
COMPANION_COLORS: Final[tuple[str, ...]] = (
    "#ffffff", "#875b36", "#dd2233", "#ff6801", "#ff9700", "#029858",
    "#00a591", "#1174da", "#804ee1", "#df2a87", "#777777",
)


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


def random_companion() -> dict[str, Any]:
    """A random, valid companion look for a newly created agent."""
    rng = secrets.SystemRandom()
    look = CompanionAppearance(
        shape=rng.choice(COMPANION_SHAPES),  # type: ignore[arg-type]
        color=rng.choice(COMPANION_COLORS),
        eyes=rng.choice(("dots", "lines")),  # type: ignore[arg-type]
    ).model_dump()
    # A new agent wears nothing yet; store it the way a bare look is stored.
    if not look.get("accessories"):
        look.pop("accessories", None)
    return look


def validate_avatar_companion(avatar: dict[str, Any]) -> dict[str, Any]:
    """Preserve character/import fields verbatim; validate only our namespace."""
    if "companion" not in avatar:
        return avatar
    companion = CompanionAppearance.model_validate(avatar["companion"]).model_dump()
    # A bare look stays byte-identical to what builds without accessories stored.
    if not companion["accessories"]:
        del companion["accessories"]
    return {**avatar, "companion": companion}
