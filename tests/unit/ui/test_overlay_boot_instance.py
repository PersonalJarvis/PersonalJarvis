"""The on-screen overlay is an ambient duty (``jarvis.core.instance``): only the
default app draws the Jarvis Bar / orb. A dev instance beside it boots the
NullOverlay at runtime and leaves the configured style — shared through ONE
``jarvis.toml`` — exactly as it is. Regression for 2026-08-23: the dev app drew a
second bar, switching it off persisted ``orb_style = "none"``, and the default
app lost its bar on the next restart.
"""
from __future__ import annotations

import pytest

from jarvis.core.instance import INSTANCE_ENV_VAR, InstanceIdentity
from jarvis.ui.desktop_app import boot_overlay_style
from jarvis.ui.overlay_styles import OVERLAY_STYLES


@pytest.mark.parametrize("configured", OVERLAY_STYLES)
def test_default_instance_boots_the_configured_style(monkeypatch, configured) -> None:
    monkeypatch.delenv(INSTANCE_ENV_VAR, raising=False)
    assert boot_overlay_style(configured) == configured


def test_default_instance_blank_style_means_the_bar(monkeypatch) -> None:
    monkeypatch.delenv(INSTANCE_ENV_VAR, raising=False)
    assert boot_overlay_style("") == "jarvis_bar"
    assert boot_overlay_style(None) == "jarvis_bar"


@pytest.mark.parametrize("configured", OVERLAY_STYLES)
def test_dev_instance_draws_no_overlay_whatever_is_configured(monkeypatch, configured) -> None:
    monkeypatch.setenv(INSTANCE_ENV_VAR, "dev")
    assert boot_overlay_style(configured) == "none"


def test_explicit_identity_wins_over_the_environment(monkeypatch) -> None:
    monkeypatch.setenv(INSTANCE_ENV_VAR, "dev")
    assert boot_overlay_style("jarvis_bar", instance=InstanceIdentity("default")) == "jarvis_bar"
    monkeypatch.delenv(INSTANCE_ENV_VAR, raising=False)
    assert boot_overlay_style("jarvis_bar", instance=InstanceIdentity("dev")) == "none"
