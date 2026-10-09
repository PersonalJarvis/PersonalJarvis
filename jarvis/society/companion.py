"""Validated visual companion settings inside the existing avatar JSON envelope."""

import secrets
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Mirrors ACCESSORY_SLOTS in the frontend catalog (parity test pins it). Item ids
# stay open-ended so a look saved by a newer build survives an older one.
AccessorySlot = Literal["back", "outfit", "neck", "mouth", "face", "head", "held"]
AccessoryId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,40}$")]
# A pet from My Pets (``jarvis/ui/pets/manifest.py``: built-in slug or ``u<hex>``
# for a drawn one). Open-ended like accessory ids: a pet deleted later or
# missing on another machine shows the shape instead, it never breaks the look.
PetId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
# Effect ids stay open like accessory ids: a design saved by a newer build keeps
# its colours in an older one, which simply draws no effect it does not know.
SkinEffect = Annotated[str, StringConstraints(pattern=r"^[a-z]{1,20}$")]

#: Mirrors ``COMPANION_SHAPES`` / ``COMPANION_COLORS`` in the frontend's
#: ``components/society/companion/appearance.ts`` (parity test pins both).
COMPANION_SHAPES: Final[tuple[str, ...]] = (
    "circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop",
)
COMPANION_COLORS: Final[tuple[str, ...]] = (
    "#ffffff", "#875b36", "#dd2233", "#ff6801", "#ff9700", "#029858",
    "#00a591", "#1174da", "#804ee1", "#df2a87", "#777777",
    "#e8c89a", "#ff7a6b", "#ffd23f", "#9ccc3a", "#3cc4e8", "#b59cf0",
    "#222222", "#8c1c3a", "#6b7a1f", "#0f5c63", "#1f3a8a", "#f49ac1",
)


class CompanionSkin(BaseModel):
    """A colour design worn instead of the flat colour (mirrors ``skinSchema`` in
    the frontend's ``components/society/companion/skins.ts``).

    ``color`` on the appearance stays the design's blended colour, so every
    surface that knows only one colour (chat bubbles, thinking dots) still fits.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    colors: list[HexColor] = Field(min_length=2, max_length=4)
    pattern: Literal["linear", "radial"] = "linear"
    #: CSS convention: 0 points up, 90 to the right.
    angle: int = Field(default=135, ge=0, le=359)
    effect: SkinEffect = "none"
    name: str = Field(default="", max_length=40, pattern=r"^[^\x00-\x1f\x7f]*$")


class CompanionAppearance(BaseModel):
    """One identity for the profile and its presentation-only world follower."""

    model_config = ConfigDict(extra="forbid", strict=True)

    shape: Literal["circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop"]
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    eyes: Literal["dots", "lines"] = "dots"
    enabled: bool = True
    accessories: dict[AccessorySlot, AccessoryId] = Field(default_factory=dict)
    #: Wear this pet instead of the shape, everywhere the agent appears.
    pet: PetId | None = None
    #: A colour design (gradient + effect) instead of the flat colour.
    skin: CompanionSkin | None = None
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
    return _bare(look)


def _bare(look: dict[str, Any]) -> dict[str, Any]:
    """Drop empty optional fields, so a plain look stays byte-identical to older builds."""
    if not look.get("accessories"):
        look.pop("accessories", None)
    if look.get("pet") is None:
        look.pop("pet", None)
    if look.get("skin") is None:
        look.pop("skin", None)
    return look


def validate_avatar_companion(avatar: dict[str, Any]) -> dict[str, Any]:
    """Preserve character/import fields verbatim; validate only our namespace."""
    if "companion" not in avatar:
        return avatar
    companion = CompanionAppearance.model_validate(avatar["companion"]).model_dump()
    return {**avatar, "companion": _bare(companion)}
