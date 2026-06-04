"""Tests for the [pointer] config section (AI Pointer step 8)."""

from __future__ import annotations

from jarvis.core.config import JarvisConfig, PointerConfig


def test_pointer_config_defaults() -> None:
    cfg = JarvisConfig()
    assert cfg.pointer.enabled is True
    assert cfg.pointer.timeout_s == 0.12
    assert cfg.pointer.crop_radius == 110


def test_pointer_config_extra_allowed() -> None:
    # A future key must not break model_validate during the self-mod
    # pre-validate pipeline (AP-16).
    pc = PointerConfig(enabled=False, some_future_key=123)  # type: ignore[call-arg]
    assert pc.enabled is False


def test_jarvis_config_loads_with_pointer_section() -> None:
    cfg = JarvisConfig(pointer={"enabled": False, "timeout_s": 0.5, "crop_radius": 96})
    assert cfg.pointer.enabled is False
    assert cfg.pointer.timeout_s == 0.5
    assert cfg.pointer.crop_radius == 96
