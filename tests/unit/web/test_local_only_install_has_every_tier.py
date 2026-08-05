"""A download whose only hardware is their own must reach every tier.

Personal Jarvis is aimed at an arbitrary downloader (CLAUDE.md section 3), and
a meaningful share of them will not enter a single cloud credential — that is
the whole point of the local providers. For those installs "mostly local" is
not a state the product should have: one keyless gap means the voice mode, the
recognizer or the low-latency path silently stops being available, and the user
learns that only when they try to speak.

Realtime was exactly that gap until 2026-08-05: brain, recognizer, voice and
wording all had a keyless card, and realtime had three cards that all billed a
hosted account. This is the gate that keeps the set complete — a new tier, or a
tier whose only local card is removed, fails here rather than in a user's
first-run.
"""

from __future__ import annotations

import pytest

from jarvis.ui.web.provider_spec import PROVIDERS, ProviderSpec, provider_billing

#: Every tier a voice-first install actually traverses. Deliberately a literal
#: list, not derived from PROVIDERS: deriving it would make a tier that lost
#: its last local card disappear from the check along with the coverage.
TIERS_A_LOCAL_INSTALL_NEEDS: tuple[str, ...] = (
    "brain",
    "stt",
    "tts",
    "realtime",
    "dictation",
)


def _keyless_cards(tier: str) -> list[ProviderSpec]:
    """Cards in ``tier`` that need no credential at all.

    "Keyless" is the card's own auth mode — the same definition the brain
    resolver uses for its local tail and the model-probe budget uses for its
    load allowance, never a provider name (AP-21).
    """
    return [s for s in PROVIDERS if s.tier == tier and s.auth_mode == "none"]


@pytest.mark.parametrize("tier", TIERS_A_LOCAL_INSTALL_NEEDS)
def test_every_tier_offers_a_keyless_local_card(tier: str) -> None:
    cards = _keyless_cards(tier)
    assert cards, (
        f"The '{tier}' tier has no keyless card, so an install with no cloud "
        "credential cannot use it at all. Ship a local option in the same "
        "change rather than leaving the tier hosted-only."
    )


@pytest.mark.parametrize("tier", TIERS_A_LOCAL_INSTALL_NEEDS)
def test_keyless_cards_are_billed_as_local(tier: str) -> None:
    """The badge follows the auth mode, so a keyless card never renders as
    "billed per token" — the line a downloader reads before deciding."""
    for spec in _keyless_cards(tier):
        assert provider_billing(spec) == "local", spec.id


def _inherits_an_address(spec: ProviderSpec) -> bool:
    """Whether this card follows a server address configured on another card.

    A self-hosted server has ONE address. The dictation-polish card reaches the
    same Ollama server the brain card points at, so it deliberately has no
    second field — but it must genuinely follow that address rather than keep a
    localhost copy that breaks the moment the server moves.
    """
    from jarvis.dictation.polish_client import POLISH_FAMILIES

    return any(
        family.id in spec.id and bool(family.endpoint_provider)
        for family in POLISH_FAMILIES
    )


def test_a_self_hosted_card_says_where_its_server_lives() -> None:
    """A local card that cannot be pointed at a server is only usable on a
    default port nobody promised. Every keyless card either runs in-process
    (its own on-device runtime), exposes an editable server URL, or follows one
    the user already set on another card."""
    from jarvis.speech.local_models import get_local_provider

    for spec in PROVIDERS:
        if spec.auth_mode != "none":
            continue
        runs_in_process = get_local_provider(spec.id) is not None
        assert runs_in_process or spec.supports_base_url or _inherits_an_address(spec), (
            f"'{spec.id}' is keyless but neither ships an on-device runtime nor "
            "lets the user say where its server is."
        )


def test_the_dictation_card_follows_the_server_the_user_configured() -> None:
    """The concrete case behind the rule above: a user who moved their Ollama
    to another box used to keep a second, invisible copy of the address here,
    pinned to localhost, and dictation polish silently talked to nothing."""
    from jarvis.core.config import BrainConfig, BrainProviderConfig, JarvisConfig
    from jarvis.dictation.polish_client import POLISH_FAMILIES

    family = next(f for f in POLISH_FAMILIES if f.endpoint_provider)
    import jarvis.core.config as cfg

    conf = JarvisConfig(
        brain=BrainConfig(
            providers={"ollama": BrainProviderConfig(base_url="http://gpu.lan:11434")}
        )
    )
    original = cfg.load_config
    cfg.load_config = lambda: conf  # type: ignore[assignment]
    try:
        assert family.effective_base_url == "http://gpu.lan:11434/v1"
        # And a server on another machine is keyless but NOT on-device — the
        # privacy promise has to follow the address, not the billing model.
        assert family.runs_on_device is False
    finally:
        cfg.load_config = original  # type: ignore[assignment]


def test_the_realtime_tier_keeps_a_self_hosted_option() -> None:
    """Named explicitly because this tier is the one that was hosted-only, and
    because a self-hosted realtime card must never be an implicit fallback: a
    call routed into an endpoint the user did not choose is worse than the
    honest "no realtime provider available"."""
    from jarvis.core.registry import load
    from jarvis.realtime.protocol import RealtimeProvider

    local_cards = _keyless_cards("realtime")
    assert local_cards, "realtime lost its self-hosted card"
    for spec in local_cards:
        provider_cls = load("jarvis.realtime", spec.id, protocol=RealtimeProvider)
        assert getattr(provider_cls, "supports_realtime", False) is True
        assert getattr(provider_cls, "implicit_usage_fallback_allowed", True) is False
        # Keyless means no credential slots — a card that declared them would
        # be built the key way and demand one it does not have.
        assert not getattr(provider_cls, "credential_candidates", ())
