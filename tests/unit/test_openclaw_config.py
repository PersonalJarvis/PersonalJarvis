"""Tests fuer OpenClawConfig + OpenClawNotificationConfig (Welle 2).

Drei Bloecke gemaess docs/openclaw-bridge.md §4.2:
  1. Schema-Defaults (Pydantic-Konstruktion ohne TOML-Daten)
  2. Live-Load aus jarvis.toml + Pydantic-Auto-Unmarshal
  3. Validierungs-Fehlerpfade (ungueltige Werte, fehlende Required-Felder)

Pattern uebernommen aus tests/unit/test_router_vision_config.py (Wave-1 B4).
"""
from __future__ import annotations

import tomllib

import pytest
from pydantic import ValidationError

from jarvis.core.config import (
    DEFAULT_CONFIG_FILE,
    HarnessConfig,
    OpenClawConfig,
    OpenClawNotificationConfig,
    load_config,
)


# ----------------------------------------------------------------------
# 1. Schema-Defaults — gueltiger minimaler Block
# ----------------------------------------------------------------------

def test_openclaw_config_defaults_with_required_version():
    """Minimaler gueltiger Block: nur ``version`` (AD-21 Pin), Rest Defaults."""
    cfg = OpenClawConfig(version="2026.5.7")
    assert cfg.enabled is False                  # default off bis Welle 3
    assert cfg.version == "2026.5.7"
    assert cfg.binary_path == "openclaw"
    # AD-6: leer = Bridge resolved aus cfg.brain.primary, kein Anthropic-Lock.
    assert cfg.model is None
    assert cfg.time_cap_min == 30                # AD-19
    assert cfg.concurrency == 3                  # AD-13
    assert cfg.cost_cap_eur is None              # AD-20 reserved
    assert cfg.state_dir_root == "data/openclaw_state"  # AD-23

    # Notification-Sub-Block traegt eigene Defaults.
    assert isinstance(cfg.notification, OpenClawNotificationConfig)
    assert cfg.notification.via == "announcement_bus"
    assert cfg.notification.toast is True
    assert cfg.notification.voice_when_active is True


def test_openclaw_notification_defaults():
    """Notification-Sub-Config laedt sauber ohne Args."""
    n = OpenClawNotificationConfig()
    assert n.via == "announcement_bus"           # AD-17
    assert n.toast is True
    assert n.voice_when_active is True


def test_harness_config_openclaw_optional_default_none():
    """Ohne expliziten Block bleibt ``HarnessConfig.openclaw is None``.

    Garantiert: bestehende Configs ohne ``[harness.openclaw]`` laden weiter.
    """
    h = HarnessConfig()
    assert h.openclaw is None


def test_openclaw_model_can_be_pinned_explicitly():
    """Wer pinnen will, kann jede Provider/Model-Combo angeben — kein
    Anthropic-Lock im Schema."""
    for slug in (
        "anthropic/claude-opus-4-7",
        "google/gemini-3.1-pro-preview",
        "xai/grok-4-fast",
        "openai/gpt-4o",
    ):
        cfg = OpenClawConfig(version="2026.5.7", model=slug)
        assert cfg.model == slug


# ----------------------------------------------------------------------
# 2. Live-Load aus jarvis.toml
# ----------------------------------------------------------------------

def test_openclaw_section_present_in_jarvis_toml():
    """Raw-TOML: ``[harness.openclaw]`` existiert mit Plan-Werten."""
    toml_path = DEFAULT_CONFIG_FILE
    assert toml_path.exists(), f"jarvis.toml nicht an {toml_path}"

    with toml_path.open("rb") as f:
        data = tomllib.load(f)

    assert "harness" in data, "[harness] Top-Level fehlt"
    assert "openclaw" in data["harness"], "[harness.openclaw] fehlt"
    sec = data["harness"]["openclaw"]

    # Pflicht-Schema-Felder pro Bridge-Doku §4.2.
    assert sec["enabled"] is True                # default on since Welle 4 merged
    assert isinstance(sec["version"], str) and sec["version"]
    assert sec["binary_path"] == "openclaw"
    assert sec["time_cap_min"] == 30
    assert sec["concurrency"] == 3
    assert sec["state_dir_root"] == "data/openclaw_state"

    # Notification-Subsektion — AD-17.
    assert "notification" in sec
    notif = sec["notification"]
    assert notif["via"] == "announcement_bus"
    assert notif["toast"] is True
    assert notif["voice_when_active"] is True

    # ``model`` und ``cost_cap_eur`` sind bewusst NICHT in der TOML
    # gesetzt (auskommentiert) — der Loader muss sie als None auffassen.
    assert "model" not in sec
    assert "cost_cap_eur" not in sec


def test_openclaw_config_unmarshalled_via_load_config():
    """Pydantic-Auto-Unmarshal landet im richtigen Feld."""
    cfg = load_config(DEFAULT_CONFIG_FILE)
    assert cfg.harness.openclaw is not None, "[harness.openclaw] nicht geparsed"

    oc = cfg.harness.openclaw
    assert isinstance(oc, OpenClawConfig)
    assert oc.enabled is True
    assert oc.version  # AD-21 Pin gesetzt
    assert oc.binary_path == "openclaw"
    assert oc.model is None                      # auskommentiert in TOML
    assert oc.time_cap_min == 30
    assert oc.concurrency == 3
    assert oc.cost_cap_eur is None               # auskommentiert in TOML
    assert oc.state_dir_root == "data/openclaw_state"

    assert isinstance(oc.notification, OpenClawNotificationConfig)
    assert oc.notification.via == "announcement_bus"
    assert oc.notification.toast is True
    assert oc.notification.voice_when_active is True


# ----------------------------------------------------------------------
# 3. Validierungs-Fehlerpfade
# ----------------------------------------------------------------------

def test_openclaw_missing_version_raises():
    """``version`` ist Required (AD-21 Pin). Ohne -> ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        OpenClawConfig()  # noqa: PIE790 — wir wollen den Fehler

    msg = str(exc_info.value).lower()
    assert "version" in msg


def test_openclaw_concurrency_must_be_positive():
    """``concurrency`` hat Field(ge=1). 0 oder negativ -> ValidationError."""
    with pytest.raises(ValidationError):
        OpenClawConfig(version="2026.5.7", concurrency=0)
    with pytest.raises(ValidationError):
        OpenClawConfig(version="2026.5.7", concurrency=-1)


def test_openclaw_concurrency_capped_at_ten():
    """``concurrency`` hat Field(le=10). 11 -> ValidationError.

    Schutz vor versehentlich exorbitanter Parallel-Last (AD-13: 3 default,
    Range 1..10 ist die operativ vernuenftige Spanne).
    """
    with pytest.raises(ValidationError):
        OpenClawConfig(version="2026.5.7", concurrency=11)


def test_openclaw_time_cap_min_must_be_positive():
    """``time_cap_min`` hat Field(ge=1). 0 -> ValidationError."""
    with pytest.raises(ValidationError):
        OpenClawConfig(version="2026.5.7", time_cap_min=0)


def test_openclaw_cost_cap_eur_must_be_non_negative():
    """``cost_cap_eur`` hat Field(ge=0). Negativ -> ValidationError."""
    with pytest.raises(ValidationError):
        OpenClawConfig(version="2026.5.7", cost_cap_eur=-1.0)


def test_openclaw_rejects_unknown_keys():
    """``extra="forbid"`` faengt Tippfehler im TOML.

    Gegen Three-Layer-Drift (BUG-008): wenn jemand ``[harness.openclaw]``
    um ``concurrencey = 3`` (Tippfehler) erweitert, soll Pydantic schreien
    statt den Wert stillschweigend zu droppen.
    """
    with pytest.raises(ValidationError) as exc_info:
        OpenClawConfig(version="2026.5.7", concurrencey=3)
    assert "extra" in str(exc_info.value).lower()


def test_openclaw_notification_rejects_unknown_keys():
    """Notification-Subblock erbt strict-mode."""
    with pytest.raises(ValidationError):
        OpenClawNotificationConfig(via="bus", unknown_flag=True)


def test_openclaw_enabled_must_be_bool():
    """``enabled`` ist bool-typed; String-Wert wird im strict-mode abgelehnt.

    Pydantic-v2 coerced normalerweise "true"/"false" zu bool; mit
    ``model_config = ConfigDict(extra="forbid")`` bleibt diese Coercion
    erhalten, aber harte Typ-Verletzungen wie int/float werden gefangen
    sobald sie keinen sauberen bool-Cast haben.
    """
    # Klare Typ-Verletzung: dict statt bool.
    with pytest.raises(ValidationError):
        OpenClawConfig(version="2026.5.7", enabled={"foo": "bar"})


def test_openclaw_round_trip_with_full_block():
    """Vollstaendiger Block — alle Felder explizit — laedt sauber."""
    cfg = OpenClawConfig(
        version="2026.5.7",
        enabled=True,
        binary_path="C:/tools/openclaw/openclaw.cmd",
        model="anthropic/claude-opus-4-7",
        time_cap_min=15,
        concurrency=2,
        cost_cap_eur=8.5,
        state_dir_root="C:/jarvis/openclaw_state",
        notification=OpenClawNotificationConfig(
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
