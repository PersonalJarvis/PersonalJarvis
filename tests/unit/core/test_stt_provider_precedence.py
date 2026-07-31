"""The user's explicit STT provider choice is authoritative at boot."""

from __future__ import annotations

import pytest

from jarvis.core import config as config_module
from jarvis.core.config import STTConfig
from jarvis.ui.web.provider_spec import PROVIDERS

STT_PROVIDER_IDS = tuple(spec.id for spec in PROVIDERS if spec.tier == "stt")


@pytest.mark.parametrize("selected", STT_PROVIDER_IDS)
def test_user_selected_provider_ignores_a_stale_environment_override(
    selected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = next(provider for provider in STT_PROVIDER_IDS if provider != selected)
    monkeypatch.setenv("JARVIS__STT__PROVIDER", stale)
    data = {
        "stt": {
            "provider": selected,
            "provider_user_selected": True,
        }
    }

    result = config_module._apply_env_overrides(data)

    assert result["stt"]["provider"] == selected


def test_headless_environment_override_still_works_without_a_user_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS__STT__PROVIDER", "openai-api")
    data = {"stt": {"provider": "faster-whisper"}}

    result = config_module._apply_env_overrides(data)

    assert result["stt"]["provider"] == "openai-api"
    assert STTConfig().provider_user_selected is False
