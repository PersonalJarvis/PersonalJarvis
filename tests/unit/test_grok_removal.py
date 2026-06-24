"""Regression guard: xAI Grok is removed as a BRAIN and SUB-AGENT provider and
from the Ack-Brain, while Grok Voice (TTS) and the xAI credential stay (2026-06-22
maintainer decision). Also pins Gemini as the recommended brain.

The whole point of this file is that a future change which reintroduces Grok as a
selectable/default/fallback brain or sub-agent — or which accidentally drops Grok
Voice / the credential — fails loudly here instead of silently shipping.
"""
from __future__ import annotations


def test_grok_is_not_a_selectable_brain_provider():
    from jarvis.ui.web.provider_spec import PROVIDERS

    brain_ids = {s.id for s in PROVIDERS if s.tier == "brain"}
    assert "grok" not in brain_ids, brain_ids


def test_grok_voice_tts_provider_and_credential_are_kept():
    from jarvis.ui.web.provider_spec import PROVIDERS, all_secret_keys, get_spec

    tts_ids = {s.id for s in PROVIDERS if s.tier == "tts"}
    assert "grok-voice" in tts_ids, tts_ids
    # The xAI key the Grok Voice plugin reads must still be a declared slot.
    assert "grok_api_key" in all_secret_keys()
    # And its help text must no longer advertise a (removed) Grok brain.
    spec = get_spec("grok-voice")
    assert spec is not None
    assert "brain" not in (spec.credential_help or "").lower()


def test_grok_absent_from_brain_model_catalog_but_tts_voice_kept():
    from jarvis.brain.model_catalog import CATALOG_PROVIDERS, CURATED_MODELS, TTS_CATALOG

    assert "grok" not in CATALOG_PROVIDERS
    assert "grok" not in CURATED_MODELS
    assert "grok-voice" in TTS_CATALOG  # Grok Voice stays a selectable TTS voice


def test_grok_absent_from_subagent_mapping():
    from jarvis.missions.worker_runtime.provider_map import (
        JARVIS_TO_OPENCLAW,
        MAPPINGS,
        OPENCLAW_TO_JARVIS,
    )

    assert "grok" not in {m.jarvis for m in MAPPINGS}
    assert "grok" not in JARVIS_TO_OPENCLAW
    assert "xai" not in OPENCLAW_TO_JARVIS


def test_grok_absent_from_api_agent_subagent_slugs():
    from jarvis.missions.init import _API_AGENT_SLUGS

    assert "grok" not in _API_AGENT_SLUGS


def test_grok_absent_from_ack_brain():
    from jarvis.brain.ack_brain.config import SUPPORTED_PROVIDERS
    from jarvis.brain.ack_brain.providers import REGISTRY

    assert "grok" not in SUPPORTED_PROVIDERS
    assert "grok" not in REGISTRY


def test_grok_absent_from_frontier_resolver():
    from jarvis.brain.frontier_resolver import SUPPORTED_PROVIDERS

    assert "grok" not in SUPPORTED_PROVIDERS


def test_grok_absent_from_brain_manager_tables():
    from jarvis.brain.manager import (
        _MAIN_BRAIN_FALLBACK_PROVIDER_ORDER,
        _PROVIDER_DISPLAY_NAMES,
        TIER_DEFAULTS_BY_PROVIDER,
    )

    assert "grok" not in TIER_DEFAULTS_BY_PROVIDER["router"]
    assert "grok" not in TIER_DEFAULTS_BY_PROVIDER["deep"]
    assert "grok" not in _MAIN_BRAIN_FALLBACK_PROVIDER_ORDER
    assert "grok" not in _PROVIDER_DISPLAY_NAMES


def test_grok_brain_entrypoint_removed_grok_voice_tts_kept():
    from importlib.metadata import entry_points

    brain = {e.name for e in entry_points(group="jarvis.brain")}
    tts = {e.name for e in entry_points(group="jarvis.tts")}
    assert "grok" not in brain, brain
    assert "grok-voice" in tts, tts


def test_gemini_is_the_recommended_brain():
    from jarvis.ui.web.provider_spec import get_spec

    gem = get_spec("gemini")
    assert gem is not None
    assert gem.tier == "brain"
    assert gem.recommended is True
    assert gem.recommended_model == "gemini-3.5-flash"
    # The recommendation must be exclusive: no other provider carries the badge.
    from jarvis.ui.web.provider_spec import PROVIDERS

    recommended = {s.id for s in PROVIDERS if s.recommended}
    assert recommended == {"gemini"}, recommended


def test_provider_chain_last_ditch_fallback_is_no_longer_grok():
    """The SubJarvisWorker last-ditch fallback used to be ('grok','grok-4.3').
    With Grok out of MAPPINGS that hardcoded stub would crash ``to_provider_slug``
    at spawn time, so the OpenClaw-routed fallback must no longer mention grok and
    must use a provider that still maps to an OpenClaw slug.

    (Note: a *configured* sub-agent provider can legitimately be a direct-worker
    slug like ``antigravity``/``codex`` that has no OpenClaw mapping — that is the
    worker factory's job, not this fallback's. This test only pins the hardcoded
    last-ditch stub, which IS OpenClaw-routed.)
    """
    import inspect

    import pytest

    from jarvis.missions.worker_runtime.provider_map import (
        UnknownJarvisProviderError,
        to_provider_slug,
    )
    from jarvis.missions.workers import provider_chain

    src = inspect.getsource(provider_chain)
    assert "grok" not in src.lower(), "grok still referenced in provider_chain.py"
    # The remaining gemini stub maps cleanly; the old grok stub would have raised.
    to_provider_slug("gemini")
    with pytest.raises(UnknownJarvisProviderError):
        to_provider_slug("grok")
