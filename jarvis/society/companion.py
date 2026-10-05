"""Validated visual companion settings inside the existing avatar JSON envelope."""

import secrets
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field


#: Mirrors ``COMPANION_SHAPES`` / ``COMPANION_COLORS`` in the frontend's
#: ``components/society/companion/appearance.ts`` (parity test pins both).
COMPANION_SHAPES: Final[tuple[str, ...]] = (
    "circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop",
)
COMPANION_COLORS: Final[tuple[str, ...]] = (
    "#8b5cf6", "#c5dfd4", "#f2a65a", "#7ab6ef", "#ed91aa", "#b7cb78", "#bba7ed", "#79c7c4",
)


class CompanionAppearance(BaseModel):
    """One identity for the profile and its presentation-only world follower."""

    model_config = ConfigDict(extra="forbid", strict=True)

    shape: Literal["circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop"]
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    eyes: Literal["dots", "lines"] = "dots"
    enabled: bool = True
    sizeM: float = Field(default=0.5, ge=0.25, le=0.8)
    followDistanceM: float = Field(default=1.0, ge=0.5, le=2.0)


def random_companion() -> dict[str, Any]:
    """A random, valid companion look for a newly created agent."""
    rng = secrets.SystemRandom()
    return CompanionAppearance(
        shape=rng.choice(COMPANION_SHAPES),  # type: ignore[arg-type]
        color=rng.choice(COMPANION_COLORS),
        eyes=rng.choice(("dots", "lines")),  # type: ignore[arg-type]
    ).model_dump()


def validate_avatar_companion(avatar: dict[str, Any]) -> dict[str, Any]:
    """Preserve character/import fields verbatim; validate only our namespace."""
    if "companion" not in avatar:
        return avatar
    companion = CompanionAppearance.model_validate(avatar["companion"])
    return {**avatar, "companion": companion.model_dump()}
