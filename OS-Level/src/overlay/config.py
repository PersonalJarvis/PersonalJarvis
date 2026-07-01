"""OverlayConfig — Pydantic v2 model per Plan §21.1.

Hot-reload is a Phase 9.2 topic. This only has the model + ``from_toml``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def _validate_hex_color(value: str) -> str:
    if not _HEX_COLOR_RE.match(value):
        raise ValueError(f"expected '#RRGGBB', got: {value!r}")
    return value


class OverlayThemeConfig(BaseModel):
    """Colors & glow geometry. Plan §7.1, §7.2, §21.1."""

    model_config = ConfigDict(extra="forbid")

    yellow_primary: str = "#FFC700"
    yellow_soft: str = "#FFE066"
    yellow_amber: str = "#FFB300"
    black: str = "#0A0A0A"
    glow_width_px: int = Field(default=14, ge=4, le=40)
    hue_drift_degrees: int = Field(default=15, ge=0, le=30)

    @field_validator("yellow_primary", "yellow_soft", "yellow_amber", "black")
    @classmethod
    def _check_hex(cls, v: str) -> str:
        return _validate_hex_color(v)


class OverlayMascotConfig(BaseModel):
    """Mascot position + drag behavior. Plan §13.4, §21.1."""

    model_config = ConfigDict(extra="forbid")

    position_monitor: str = ""
    position_x_relative: int = 200
    position_y_relative: int = 80
    size_px: int = Field(default=160, ge=80, le=256)
    draggable: bool = True
    snap_to_edges_px: int = Field(default=16, ge=0, le=32)
    hidden_for_session: bool = False


class OverlayConfig(BaseModel):
    """Top-level [overlay] section. Plan §21.1."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    edge_glow_enabled: bool = True
    mascot_enabled: bool = True
    all_monitors: bool = False
    hide_on_fullscreen: bool = True
    ignore_busy_state: bool = False
    hide_from_capture: bool = True
    respect_reduced_motion: bool = True
    animations_enabled: bool = True

    fps_idle: int = Field(default=1, ge=0, le=30)
    fps_active: int = Field(default=30, ge=1, le=60)
    fps_burst: int = Field(default=60, ge=1, le=60)
    idle_timeout_s: int = Field(default=30, ge=5, le=600)
    hide_timeout_s: int = Field(default=300, ge=60, le=3600)

    ws_port: int = Field(default=7842, ge=1024, le=65535)
    ws_host: str = "127.0.0.1"
    ws_port_range_max: int = Field(default=7852, ge=1024, le=65535)
    fallback_pipe: str = "\\\\.\\pipe\\jarvis-overlay"
    shm_cursor_name_prefix: str = "jarvis-cursor"
    heartbeat_interval_s: int = Field(default=1, ge=1, le=10)
    heartbeat_timeout_s: int = Field(default=3, ge=2, le=30)

    cursor_trail_enabled: bool = True
    cursor_stream_hz: int = Field(default=60, ge=10, le=120)

    theme: OverlayThemeConfig = Field(default_factory=OverlayThemeConfig)
    mascot: OverlayMascotConfig = Field(default_factory=OverlayMascotConfig)

    @field_validator("ws_host")
    @classmethod
    def _check_loopback(cls, v: str) -> str:
        if v not in _LOOPBACK_HOSTS:
            raise ValueError(f"ws_host must be loopback, got: {v!r}")
        return v

    @classmethod
    def from_toml(cls, path: str | Path) -> "OverlayConfig":
        """Read the ``[overlay]`` section from TOML. If missing: use defaults (Plan §21.4)."""
        toml_path = Path(path)
        with toml_path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        section = data.get("overlay", {})
        return cls.model_validate(section)
