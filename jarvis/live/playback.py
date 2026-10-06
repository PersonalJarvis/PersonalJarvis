"""Ephemeral source-timed audio/captions for the device-owned playback clock.

These frames are not playback acknowledgements and are never persisted as such.
Transcript history keeps its existing session/segment schema.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


def playback_frame(model: type[BaseModel], **fields: Any) -> dict | None:
    """Reject malformed metadata without logging provider text or raw audio."""
    try:
        return model(**fields).model_dump()
    except ValidationError:
        logging.getLogger(__name__).warning("Invalid voice playback metadata was discarded")
        return None


class AudioTimedFrame(BaseModel):
    type: Literal["audio_timed"] = "audio_timed"
    epoch: int = Field(ge=0)
    audio: str = Field(max_length=1_400_000)
    sample_rate: int = Field(default=24_000, ge=8000, le=192000)
    start_ms: float = Field(ge=0, allow_inf_nan=False)
    end_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> AudioTimedFrame:
        if self.end_ms <= self.start_ms:
            raise ValueError("Audio interval must have positive duration")
        return self


class SpeechTimingFrame(BaseModel):
    type: Literal["speech_timing"] = "speech_timing"
    epoch: int = Field(ge=0)
    line_id: str = Field(min_length=1, max_length=512)
    text: str = Field(max_length=128_000)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    start_ms: float = Field(ge=0, allow_inf_nan=False)
    end_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> SpeechTimingFrame:
        if self.end_ms <= self.start_ms or not 0 <= self.char_start < self.char_end <= utf16_length(
            self.text
        ):
            raise ValueError("Invalid speech interval")
        return self


def utf16_length(text: str) -> int:
    """Browser offsets count UTF-16 code units, including astral characters."""
    return len(text.encode("utf-16-le")) // 2


def source_interval(start: object, end: object) -> bool:
    return (
        isinstance(start, (int, float))
        and not isinstance(start, bool)
        and isinstance(end, (int, float))
        and not isinstance(end, bool)
        and math.isfinite(start)
        and math.isfinite(end)
        and 0 <= start < end
    )
