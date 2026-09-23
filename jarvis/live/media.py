"""Ephemeral browser media measurements; never transcripts or stored usage."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

Level = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class MediaLevels(BaseModel):
    """One normalized snapshot from the client that owns this voice session."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["media_levels"]
    input_level: Level
    output_level: Level
    input_active: StrictBool
    playback_active: StrictBool
