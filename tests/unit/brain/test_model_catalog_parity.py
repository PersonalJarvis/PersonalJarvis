"""Drift guard: every selectable Brain/TTS/STT provider must have a model catalog.

The API-Keys model picker (``BrainModelSelector``) calls
``GET /api/providers/{id}/models`` for every brain provider and the active
TTS/STT provider. That endpoint 400s when ``catalog_spec(provider_id)`` returns
None — so a provider shipped without a catalog entry has a silently broken
picker ("the model dropdown doesn't work for provider X").

This parity test fails the moment a provider is added to ``PROVIDERS`` without a
matching ``model_catalog`` entry, preventing that regression class — the same
single-source-of-truth shape as the BUG-008 enum-drift guards
(see ``docs/anti-drift-three-layer.md``).
"""
from __future__ import annotations

import pytest

from jarvis.brain.model_catalog import catalog_spec
from jarvis.ui.web.provider_spec import PROVIDERS

# Tiers that surface a model/voice picker in the API-Keys view.
_PICKER_TIERS = {"brain", "tts", "stt"}
_SPECS = [s for s in PROVIDERS if s.tier in _PICKER_TIERS]


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.id)
def test_every_provider_has_a_model_catalog(spec):
    cat = catalog_spec(spec.id)
    assert cat is not None, (
        f"Provider '{spec.id}' (tier={spec.tier}) has no model_catalog entry — "
        f"its model picker would 400. Add it in jarvis/brain/model_catalog.py "
        f"(CATALOG_PROVIDERS / CURATED_MODELS for brain, TTS_CATALOG, or "
        f"STT_CATALOG)."
    )


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.id)
def test_catalog_tier_matches_provider_tier(spec):
    cat = catalog_spec(spec.id)
    assert cat is not None  # covered by the test above; guards the access below
    assert cat.tier == spec.tier, (
        f"Provider '{spec.id}' is tier={spec.tier} in provider_spec but "
        f"tier={cat.tier} in model_catalog — the two drifted."
    )
