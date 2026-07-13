"""Tests for JarvisAgentHarnessConfig + JarvisAgentNotificationConfig (Wave 2).

Three blocks per docs/openclaw-bridge.md §4.2:
  1. Schema defaults (Pydantic construction without TOML data)
  2. Live-load from jarvis.toml + Pydantic auto-unmarshal
  3. Validation error paths (invalid values, missing required fields)

Pattern adopted from tests/unit/test_router_vision_config.py (Wave-1 B4).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.core.config import (
    HarnessConfig,
    JarvisAgentHarnessConfig,
    JarvisAgentNotificationConfig,
    load_config,
)

_LEGACY_HARNESS_CONFIG = """\
[harness]
enabled = ["python-script"]

[harness.openclaw]
enabled = true
version = "2026.5.7"
binary_path = "openclaw"
time_cap_min = 30
concurrency = 3
state_dir_root = "data/openclaw_state"

[harness.openclaw.notification]
via = "announcement_bus"
toast = true
voice_when_active = true
"""


def _write_legacy_harness_config(directory: Path) -> Path:
    config_path = directory / "jarvis.toml"
    config_path.write_text(_LEGACY_HARNESS_CONFIG, encoding="utf-8")
    return config_path


# ----------------------------------------------------------------------
# 1. Schema defaults — valid minimal block
# ----------------------------------------------------------------------

def test_jarvis_agent_harness_config_defaults_with_required_version():
    """Minimal valid block: only ``version`` (AD-21 pin), rest are defaults."""
    cfg = JarvisAgentHarnessConfig(version="2026.5.7")
    assert cfg.enabled is False                  # default off until Wave 3
    assert cfg.version == "2026.5.7"
    assert cfg.binary_path == "openclaw"
    # AD-6: empty = bridge resolves from cfg.brain.primary, no Anthropic lock.
    assert cfg.model is None
    assert cfg.time_cap_min == 30                # AD-19
    assert cfg.concurrency == 3                  # AD-13
    assert cfg.cost_cap_eur is None              # AD-20 reserved
    assert cfg.state_dir_root == "data/openclaw_state"  # AD-23

    # Notification sub-block carries its own defaults.
    assert isinstance(cfg.notification, JarvisAgentNotificationConfig)
    assert cfg.notification.via == "announcement_bus"
    assert cfg.notification.toast is True
    assert cfg.notification.voice_when_active is True


def test_jarvis_agent_notification_defaults():
    """Notification sub-config loads cleanly without args."""
    n = JarvisAgentNotificationConfig()
    assert n.via == "announcement_bus"           # AD-17
    assert n.toast is True
    assert n.voice_when_active is True


def test_harness_config_openclaw_optional_default_none():
    """Without an explicit block, ``HarnessConfig.jarvis_agent is None``.

    Guarantees: existing configs without ``[harness.openclaw]`` / ``[harness.jarvis_agent]``
    still load. The field was renamed openclaw → jarvis_agent in the 2026-06-29
    Jarvis-Agents rename; the TOML alias accepts both section names.
    """
    h = HarnessConfig()
    assert h.enabled == ["python-script"]
    assert h.jarvis_agent is None


def test_jarvis_agent_harness_model_can_be_pinned_explicitly():
    """Anyone who wants to pin can specify any provider/model combo —
    no Anthropic lock in the schema."""
    for slug in (
        "anthropic/claude-opus-4-7",
        "google/gemini-3.1-pro-preview",
        "xai/grok-4-fast",
        "openai/gpt-4o",
    ):
        cfg = JarvisAgentHarnessConfig(version="2026.5.7", model=slug)
        assert cfg.model == slug


# ----------------------------------------------------------------------
# 2. Live-load from jarvis.toml
# ----------------------------------------------------------------------

def test_legacy_openclaw_section_is_accepted_from_portable_fixture(tmp_path: Path):
    """The legacy section remains readable without maintainer-local config."""
    toml_path = _write_legacy_harness_config(tmp_path)

    with toml_path.open("rb") as f:
        data = tomllib.load(f)

    assert "harness" in data, "[harness] top-level key missing"
    assert "openclaw" in data["harness"], "[harness.openclaw] missing"
    sec = data["harness"]["openclaw"]

    # Required schema fields per bridge docs §4.2.
    assert sec["enabled"] is True                # default on since Wave 4 merged
    assert isinstance(sec["version"], str) and sec["version"]
    assert sec["binary_path"] == "openclaw"
    assert sec["time_cap_min"] == 30
    assert sec["concurrency"] == 3
    assert sec["state_dir_root"] == "data/openclaw_state"

    # Notification sub-section — AD-17.
    assert "notification" in sec
    notif = sec["notification"]
    assert notif["via"] == "announcement_bus"
    assert notif["toast"] is True
    assert notif["voice_when_active"] is True

    # ``model`` and ``cost_cap_eur`` are intentionally NOT set in TOML
    # (commented out) — the loader must treat them as None.
    assert "model" not in sec
    assert "cost_cap_eur" not in sec


def test_jarvis_agent_harness_config_unmarshalled_via_load_config(tmp_path: Path):
    """Pydantic auto-unmarshal lands in the correct field (jarvis_agent).

    jarvis.toml still uses the old ``[harness.openclaw]`` section name for
    back-compat; the ``validation_alias`` on HarnessConfig.jarvis_agent
    accepts both names, so this field is populated regardless.
    """
    cfg = load_config(_write_legacy_harness_config(tmp_path))
    assert cfg.harness.jarvis_agent is not None, (
        "[harness.openclaw] / [harness.jarvis_agent] not parsed"
    )

    oc = cfg.harness.jarvis_agent
    assert isinstance(oc, JarvisAgentHarnessConfig)
    assert oc.enabled is True
    assert oc.version  # AD-21 pin set
    assert oc.binary_path == "openclaw"
    assert oc.model is None                      # commented out in TOML
    assert oc.time_cap_min == 30
    assert oc.concurrency == 3
    assert oc.cost_cap_eur is None               # commented out in TOML
    assert oc.state_dir_root == "data/openclaw_state"

    assert isinstance(oc.notification, JarvisAgentNotificationConfig)
    assert oc.notification.via == "announcement_bus"
    assert oc.notification.toast is True
    assert oc.notification.voice_when_active is True


# ----------------------------------------------------------------------
# 3. Validation error paths
# ----------------------------------------------------------------------

def test_jarvis_agent_harness_missing_version_raises():
    """``version`` is Required (AD-21 pin). Without it -> ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        JarvisAgentHarnessConfig()  # noqa: PIE790 — we want the error

    msg = str(exc_info.value).lower()
    assert "version" in msg


def test_jarvis_agent_harness_concurrency_must_be_positive():
    """``concurrency`` has Field(ge=1). 0 or negative -> ValidationError."""
    with pytest.raises(ValidationError):
        JarvisAgentHarnessConfig(version="2026.5.7", concurrency=0)
    with pytest.raises(ValidationError):
        JarvisAgentHarnessConfig(version="2026.5.7", concurrency=-1)


def test_jarvis_agent_harness_concurrency_capped_at_ten():
    """``concurrency`` has Field(le=10). 11 -> ValidationError.

    Guards against accidentally enormous parallel load (AD-13: default 3,
    range 1..10 is the operationally sensible span).
    """
    with pytest.raises(ValidationError):
        JarvisAgentHarnessConfig(version="2026.5.7", concurrency=11)


def test_jarvis_agent_harness_time_cap_min_must_be_positive():
    """``time_cap_min`` has Field(ge=1). 0 -> ValidationError."""
    with pytest.raises(ValidationError):
        JarvisAgentHarnessConfig(version="2026.5.7", time_cap_min=0)


def test_jarvis_agent_harness_cost_cap_eur_must_be_non_negative():
    """``cost_cap_eur`` has Field(ge=0). Negative -> ValidationError."""
    with pytest.raises(ValidationError):
        JarvisAgentHarnessConfig(version="2026.5.7", cost_cap_eur=-1.0)


def test_jarvis_agent_harness_rejects_unknown_keys():
    """``extra="forbid"`` catches typos in TOML.

    Against three-layer drift (BUG-008): if someone extends
    ``[harness.openclaw]`` with ``concurrencey = 3`` (typo), Pydantic
    should raise instead of silently dropping the value.
    """
    with pytest.raises(ValidationError) as exc_info:
        JarvisAgentHarnessConfig(version="2026.5.7", concurrencey=3)
    assert "extra" in str(exc_info.value).lower()


def test_jarvis_agent_notification_rejects_unknown_keys():
    """Notification sub-block inherits strict mode."""
    with pytest.raises(ValidationError):
        JarvisAgentNotificationConfig(via="bus", unknown_flag=True)


def test_jarvis_agent_harness_enabled_must_be_bool():
    """``enabled`` is bool-typed; a dict value is rejected.

    Pydantic-v2 normally coerces "true"/"false" to bool; with
    ``model_config = ConfigDict(extra="forbid")`` that coercion is preserved,
    but hard type violations like passing a dict are caught.
    """
    with pytest.raises(ValidationError):
        JarvisAgentHarnessConfig(version="2026.5.7", enabled={"foo": "bar"})


def test_jarvis_agent_harness_round_trip_with_full_block():
    """Full block — all fields explicit — loads cleanly."""
    cfg = JarvisAgentHarnessConfig(
        version="2026.5.7",
        enabled=True,
        binary_path="C:/tools/openclaw/openclaw.cmd",
        model="anthropic/claude-opus-4-7",
        time_cap_min=15,
        concurrency=2,
        cost_cap_eur=8.5,
        state_dir_root="C:/jarvis/openclaw_state",
        notification=JarvisAgentNotificationConfig(
            via="announcement_bus",
            toast=False,
            voice_when_active=False,
        ),
    )
    assert cfg.enabled is True
    assert cfg.binary_path == "C:/tools/openclaw/openclaw.cmd"
    assert cfg.model == "anthropic/claude-opus-4-7"
    assert cfg.time_cap_min == 15
    assert cfg.concurrency == 2
    assert cfg.cost_cap_eur == 8.5
    assert cfg.notification.toast is False
    assert cfg.notification.voice_when_active is False
