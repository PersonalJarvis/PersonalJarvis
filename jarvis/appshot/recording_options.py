"""Validated recording preferences and per-source limits, without desktop imports."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Resolution = Literal["720p", "1080p", "1440p", "2160p", "native"]
FrameRate = Literal[30, 60, 120]


class RecordingOptions(BaseModel):
    """One immutable snapshot for a recording, including shortcut starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    resolution: Resolution = "1080p"
    fps: FrameRate = 60
    bitrate_mbps: int = Field(default=12, ge=1, le=100, strict=True)
    system_audio: bool = False

    @classmethod
    def from_config(cls, block: object) -> RecordingOptions:
        return cls(**{
            key: getattr(block, f"recording_{key}", field.default)
            for key, field in cls.model_fields.items()
        })


def output_size(width: int, height: int, resolution: Resolution) -> tuple[int, int]:
    """Fit inside an oriented preset without upscaling or distorting the source."""
    if width < 1 or height < 1:
        raise ValueError("The recording area must have positive dimensions.")
    factor = 1.0
    if resolution != "native":
        short = int(resolution[:-1])
        long = round(short * 16 / 9)
        bound_w, bound_h = (long, short) if width >= height else (short, long)
        factor = min(1.0, bound_w / width, bound_h / height)
    # H.264 4:2:0 needs even dimensions. At most one padding pixel at native size.
    if factor == 1:
        return width + width % 2, height + height % 2
    return max(2, int(width * factor) // 2 * 2), max(2, int(height * factor) // 2 * 2)


def effective_fps(requested: int, refresh_hz: float | None) -> int:
    """Use the selected display's current rate, never another monitor's maximum."""
    hz = refresh_hz if refresh_hz and math.isfinite(refresh_hz) else 60.0
    # 59.94/119.88 are the conventional 60/120 Hz modes.
    ceiling = max(1, round(hz))
    available = [rate for rate in (30, 60, 120) if rate <= ceiling]
    return min(requested, max(available) if available else ceiling)
