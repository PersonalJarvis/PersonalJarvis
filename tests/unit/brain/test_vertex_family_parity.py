"""Drift guard: Vertex AI is wired into EVERY tier, not just the brain.

The point of the Vertex family is that one Google Cloud setup serves the whole
stack — the thinking model, the tool model, voice input, voice output, the
realtime socket, and the subagents. Each of those is a separate registration in
a separate table, and a family that reaches only some of them is the failure
mode that reads as "I added my key and it did nothing": the card appears, the
key saves, and the tier the user actually wanted silently keeps running on
another account.

Same single-source-of-truth shape as the other BUG-008 enum-drift guards
(``docs/anti-drift-three-layer.md``): the tests below assert the registrations
exist and agree with each other, so removing one half of a pair fails here
rather than in a user's voice session.

Deliberately free of network, SDK and credentials — every assertion is about
registration tables and class attributes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER
from jarvis.brain.model_catalog import catalog_spec
from jarvis.brain.provider_registry import BrainProviderRegistry
from jarvis.core.config import (
    JARVIS_AGENT_SECRET_CANDIDATES,
    PROVIDER_SECRET_CANDIDATES,
)
from jarvis.core.registry import list_plugins
from jarvis.ui.web.provider_spec import PROVIDERS

#: One provider id per tier. The whole promise of the family in one table.
VERTEX_IDS_BY_TIER = {
    "brain": "vertex",
    "stt": "vertex-stt",
    "tts": "vertex-tts",
    "realtime": "vertex-live",
}

#: Entry-point group each tier's plugin is discovered through.
_GROUP_BY_TIER = {
    "brain": "jarvis.brain",
    "stt": "jarvis.stt",
    "tts": "jarvis.tts",
    "realtime": "jarvis.realtime",
}


# ── every tier has a registered plugin AND a card ────────────────────────────


@pytest.mark.parametrize(("tier", "provider_id"), sorted(VERTEX_IDS_BY_TIER.items()))
def test_every_tier_registers_a_vertex_plugin(tier: str, provider_id: str) -> None:
    installed = list_plugins(_GROUP_BY_TIER[tier])
    assert provider_id in installed, (
        f"{provider_id!r} is not registered in {_GROUP_BY_TIER[tier]!r}. Without "
        f"the entry point the {tier} tier cannot resolve Vertex at all, however "
        f"complete the rest of the wiring looks."
    )


@pytest.mark.parametrize(("tier", "provider_id"), sorted(VERTEX_IDS_BY_TIER.items()))
def test_every_tier_has_a_selectable_card(tier: str, provider_id: str) -> None:
    """A plugin with no card is a feature the app has but does not offer."""
    spec = next((s for s in PROVIDERS if s.id == provider_id), None)
    assert spec is not None, f"no ProviderSpec for {provider_id!r}"
    assert spec.tier == tier
    assert spec.hidden is False
    assert spec.auth_mode == "api_key"
    # The Cloud-project path must be documented ON the card: it is the setup
    # that stores no key, so a user reading only the key field would conclude
    # Vertex needs one.
    assert spec.alt_credential is not None
    assert "vertex_project" in (spec.alt_credential.credential_help or "")


@pytest.mark.parametrize(("tier", "provider_id"), sorted(VERTEX_IDS_BY_TIER.items()))
def test_every_card_has_a_picker_catalog(tier: str, provider_id: str) -> None:
    """Realtime has no picker; the other three would 400 without a catalog."""
    if tier == "realtime":
        pytest.skip("the realtime tier has no model/voice picker")
    spec = catalog_spec(provider_id)
    assert spec is not None and spec.tier == tier
    assert spec.curated, f"{provider_id!r} would open an empty picker"


# ── the credential is ONE credential, shared across the tiers ────────────────


def test_the_vertex_key_slot_is_shared_by_every_tier() -> None:
    """Set Vertex up once. A second slot per tier would be a second setup."""
    shared = "vertex_api_key"
    brain_slots = {slot for slot, _env in PROVIDER_SECRET_CANDIDATES["vertex"]}
    realtime_slots = {slot for slot, _env in PROVIDER_SECRET_CANDIDATES["vertex-live"]}
    agent_slots = {slot for slot, _env in JARVIS_AGENT_SECRET_CANDIDATES["vertex"]}
    assert shared in brain_slots
    assert shared in realtime_slots
    assert shared in agent_slots


def test_realtime_and_agent_tiers_keep_their_own_dedicated_slot_first() -> None:
    """A scoped key must win on its own surface, exactly like its siblings."""
    assert PROVIDER_SECRET_CANDIDATES["vertex-live"][0][0] == "realtime_vertex_api_key"
    assert JARVIS_AGENT_SECRET_CANDIDATES["vertex"][0][0] == "jarvis_agent_vertex_api_key"


def test_vertex_never_reads_a_gemini_slot_and_gemini_never_reads_a_vertex_one() -> None:
    """The two accounts are separate, and a cross-read would be a silent failure.

    Feeding an AI Studio key to the Vertex endpoint does not degrade — it 401s
    every single call, while the UI shows a configured provider. An honest "no
    credential" is strictly better, so the families stay disjoint in BOTH
    directions.
    """
    vertex_families = ("vertex", "vertex-live")
    gemini_families = ("gemini", "gemini-live")
    vertex_slots = {
        slot for family in vertex_families for slot, _env in PROVIDER_SECRET_CANDIDATES[family]
    }
    gemini_slots = {
        slot for family in gemini_families for slot, _env in PROVIDER_SECRET_CANDIDATES[family]
    }
    assert not (vertex_slots & gemini_slots), (
        "a slot is shared between the Vertex and Gemini families — one of them "
        f"will be handed a key its endpoint rejects: {sorted(vertex_slots & gemini_slots)}"
    )


# ── brain: pinned route, and the same models as its sibling ──────────────────


def test_the_brain_plugin_pins_the_vertex_route() -> None:
    """Unpinned, an ``AIza`` Cloud key would be probed onto AI Studio and fail."""
    from jarvis.plugins.brain.vertex import VertexBrain

    assert VertexBrain.pinned_route == "vertex"
    assert VertexBrain.provider_id == "vertex"
    assert VertexBrain.name == "vertex"
    # Inherited, not reimplemented: the whole point of the subclass.
    from jarvis.plugins.brain.gemini import GeminiBrain

    assert issubclass(VertexBrain, GeminiBrain)
    assert VertexBrain.supports_tools is True
    assert VertexBrain.supports_vision is True


def test_the_gemini_brain_is_left_on_the_inferring_route() -> None:
    """The existing behaviour for a key in a Gemini slot must not change."""
    from jarvis.plugins.brain.gemini import GeminiBrain

    assert GeminiBrain.pinned_route is None
    assert GeminiBrain.provider_id == "gemini"


@pytest.mark.parametrize("tier", ["router", "deep"])
def test_tier_defaults_match_gemini_exactly(tier: str) -> None:
    """Vertex serves the same catalogue; a second literal list would drift."""
    defaults = TIER_DEFAULTS_BY_PROVIDER[tier]
    assert defaults["vertex"] == defaults["gemini"]
    assert defaults["vertex"], "an empty default would fall back to the plugin anchor"


def test_the_brain_is_registered_and_loadable() -> None:
    registry = BrainProviderRegistry()
    assert "vertex" in registry.available(), registry.failed()
    assert registry.get_class("vertex").name == "vertex"


def test_the_tool_model_tier_can_select_vertex() -> None:
    """The tool model is its own selection, and must reach Vertex like any other."""
    from jarvis.brain import resolver

    resolver._reset_for_tests()
    config = SimpleNamespace(
        brain=SimpleNamespace(
            tool_model=SimpleNamespace(provider="vertex"),
            providers={"vertex": SimpleNamespace(model="gemini-3.5-flash")},
        )
    )
    brain = resolver.resolve_tool_model_brain(config)
    resolver._reset_for_tests()
    assert brain is not None, "the tool-model tier could not instantiate Vertex"
    assert brain.name == "vertex"


# ── speech tiers: own credential family, and in the cross-family order ───────


def test_stt_knows_the_vertex_family_and_will_cross_to_it() -> None:
    from jarvis.plugins.stt import (
        _STT_CROSS_FAMILY_ORDER,
        _STT_SECRET_CANDIDATES,
        stt_family_id,
    )

    assert "vertex-stt" in _STT_SECRET_CANDIDATES
    assert "vertex-stt" in _STT_CROSS_FAMILY_ORDER
    # A distinct family: a depleted AI Studio key must be able to cross HERE.
    assert stt_family_id("vertex-stt") != stt_family_id("gemini-api")


def test_tts_knows_the_vertex_family_and_will_cross_to_it() -> None:
    from jarvis.plugins.tts import (
        _TTS_CROSS_FAMILY_ORDER,
        _TTS_SECRET_CANDIDATES,
        _canonical_tts_name,
    )

    assert "vertex-tts" in _TTS_SECRET_CANDIDATES
    assert "vertex-tts" in _TTS_CROSS_FAMILY_ORDER
    # Spelling tolerance, like every other TTS family (BUG-008 class).
    for spelling in ("vertex", "vertex-tts", "Vertex-AI"):
        assert _canonical_tts_name(spelling) == "vertex-tts"
    # The bare "gemini" spelling must still mean the AI Studio card.
    assert _canonical_tts_name("gemini") == "gemini-flash-tts"


def test_realtime_declares_a_distinct_credential_family() -> None:
    """Equal families would turn a real cross-account fallback into a doomed retry."""
    from jarvis.plugins.realtime.gemini_live import (
        GeminiLiveProvider,
        VertexLiveProvider,
    )

    assert VertexLiveProvider.credential_family == "vertex"
    assert VertexLiveProvider.credential_family != GeminiLiveProvider.credential_family
    assert VertexLiveProvider.supports_realtime is True
    assert issubclass(VertexLiveProvider, GeminiLiveProvider)


def test_realtime_is_eligible_on_a_keyless_cloud_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory skips keyless API providers unless they declare another login.

    Without that declaration the documented production setup — a Cloud project,
    no key anywhere — would never even become a realtime candidate.
    """
    from jarvis.core import config as cfg
    from jarvis.plugins.realtime.gemini_live import VertexLiveProvider

    monkeypatch.setattr(cfg, "vertex_credential_configured", lambda *_a, **_k: True)
    assert VertexLiveProvider.external_login_ready(None) is True

    monkeypatch.setattr(cfg, "vertex_credential_configured", lambda *_a, **_k: False)
    assert VertexLiveProvider.external_login_ready(None) is False


# ── subagents ────────────────────────────────────────────────────────────────


def test_a_vertex_subagent_runs_on_vertex_rather_than_falling_back_to_claude() -> None:
    """Picking a provider must RUN that provider — the standing mandate here."""
    from jarvis.missions.init import (
        _API_AGENT_SLUGS,
        _select_subagent_worker_kind,
        subagent_runs_on_claude_fallback,
    )

    assert "vertex" in _API_AGENT_SLUGS
    assert _select_subagent_worker_kind("vertex", "") == "api_agent"
    # A per-step model must not divert it either (the hard-lock property).
    assert _select_subagent_worker_kind("vertex", "gemini-3.5-flash") == "api_agent"
    assert subagent_runs_on_claude_fallback("vertex") is False


def test_the_subagent_worker_can_build_a_vertex_brain() -> None:
    from jarvis.missions.workers.api_agent_worker import (
        _BRAIN_BY_PROVIDER,
        _DEFAULT_MODEL,
        _build_brain,
    )

    assert "vertex" in _BRAIN_BY_PROVIDER
    assert _DEFAULT_MODEL["vertex"] == _DEFAULT_MODEL["gemini"]
    brain = _build_brain("vertex", _DEFAULT_MODEL["vertex"])
    assert brain.name == "vertex"


# ── the keyless Cloud project counts as configured, everywhere it is asked ───


@pytest.mark.parametrize("provider_id", sorted(VERTEX_IDS_BY_TIER.values()))
def test_a_keyless_cloud_project_reads_as_configured_on_every_card(
    provider_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise Google's documented production setup shows as "open".

    Every card's state, every switch gate and the Test button all flow through
    ``is_credential_present``. With a key probe alone, an install whose Vertex
    access is a service account would be told to add a key it does not need —
    the "I set it up and nothing happened" failure.
    """
    from jarvis.brain import app_control
    from jarvis.core import config as cfg

    spec = next(s for s in PROVIDERS if s.id == provider_id)
    monkeypatch.setattr(cfg, "vertex_credential_configured", lambda *_a, **_k: True)
    with cfg.override_provider_secrets({"vertex": None, "vertex-live": None}):
        assert app_control.is_credential_present(spec) is True


@pytest.mark.parametrize("provider_id", sorted(VERTEX_IDS_BY_TIER.values()))
def test_no_key_and_no_project_still_reads_as_unconfigured(
    provider_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rescue must not turn every Vertex card permanently green."""
    from jarvis.brain import app_control
    from jarvis.core import config as cfg

    spec = next(s for s in PROVIDERS if s.id == provider_id)
    monkeypatch.setattr(cfg, "vertex_credential_configured", lambda *_a, **_k: False)
    with cfg.override_provider_secrets({"vertex": None, "vertex-live": None}):
        assert app_control.is_credential_present(spec) is False


def test_the_keyless_rescue_is_scoped_to_vertex(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing Gemini key must still read as missing, probe or no probe."""
    from jarvis.brain import app_control
    from jarvis.core import config as cfg

    spec = next(s for s in PROVIDERS if s.id == "gemini")
    monkeypatch.setattr(cfg, "vertex_credential_configured", lambda *_a, **_k: True)
    with cfg.override_provider_secrets({"gemini": None}):
        assert app_control.is_credential_present(spec) is False


def test_the_subagent_tab_can_map_the_vertex_slug() -> None:
    from jarvis.missions.worker_runtime.provider_map import (
        env_vars_for,
        to_jarvis_from_worker_slug,
        to_worker_slug,
        validate_configured_providers,
    )

    assert to_worker_slug("vertex") == "vertex"
    assert to_jarvis_from_worker_slug("vertex") == "vertex"
    assert env_vars_for("vertex")[0] == "VERTEX_API_KEY"
    assert validate_configured_providers(["vertex"]) == []
