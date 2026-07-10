"""The shared OpenRouter credential exposes every selectable app surface."""
from __future__ import annotations

import pytest

from jarvis.brain.model_catalog import ModelCatalog, catalog_spec
from jarvis.ui.web.provider_spec import get_spec


@pytest.mark.parametrize("provider_id", ["openrouter", "openrouter-tts", "openrouter-stt"])
def test_every_openrouter_surface_uses_the_shared_credential(provider_id: str) -> None:
    provider = get_spec(provider_id)
    assert provider is not None
    assert provider.secret_keys == ("openrouter_api_key",)


def test_every_openrouter_surface_has_a_model_catalog() -> None:
    brain = catalog_spec("openrouter")
    tts = catalog_spec("openrouter-tts")
    stt = catalog_spec("openrouter-stt")

    assert brain is not None and brain.tier == "brain" and brain.live is True
    assert tts is not None and tts.tier == "tts" and tts.selects == "model"
    assert stt is not None and stt.tier == "stt" and stt.selects == "model"
    assert brain.curated
    assert tts.curated
    assert stt.curated


@pytest.mark.asyncio
async def test_openrouter_voice_catalogs_are_available_without_a_network_call(tmp_path) -> None:
    catalog = ModelCatalog(cache_path=tmp_path / "catalog.json")

    tts = await catalog.list_models("openrouter-tts")
    stt = await catalog.list_models("openrouter-stt")

    assert tts.source == "curated" and tts.models
    assert stt.source == "curated" and stt.models
