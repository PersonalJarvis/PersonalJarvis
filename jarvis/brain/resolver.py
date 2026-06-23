"""Frontier brain resolver for background generation tasks.

Provides ``resolve_frontier_brain(config)``: returns a ``Brain`` instance
that uses the **frontier/deep model of the currently configured primary
provider** — dynamically, without hardcoded model names.

Multi-provider requirement (see memory ``feedback_brain_providers.md``):
background generation tasks (BioGenerator, persona descriptions,
skill authoring) MUST respect the provider chosen by the user.
A user who only has a Gemini API key gets a Gemini bio. A user who has
Claude configured gets Opus. Never hardcode model names such as
``claude-opus-4-8`` directly in code.

Fallback order:

1. ``config.board.bio.override_provider/override_model`` (power-user pin).
2. ``config.brain.sub_jarvis`` (BrainTierConfig — Wave 4 legacy entry,
   still read if jarvis.toml still contains the block; values are used as
   a frontier hint).
3. ``config.brain.primary`` + ``TIER_DEFAULTS_BY_PROVIDER['deep']``
   (implicit default frontier lookup).

If a provider cannot be instantiated (no API key, service down, plugin not
loaded), we fall through all three stages and ultimately through the
``configured_fallbacks`` of the tier config. Only after a complete failure
does ``resolve_frontier_brain`` raise a ``RuntimeError``.

Cache strategy: singleton cache, invalidated on ``ConfigReloaded`` event
(subscriber is registered lazily on the first resolve call).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER
from jarvis.brain.provider_registry import BrainProviderRegistry

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.core.config import JarvisConfig
    from jarvis.core.protocols import Brain

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Singleton-Cache
# ----------------------------------------------------------------------

# Cache is config-dependent: invalidated on the ConfigReloaded event.
# Key is (provider_name, model_name) so that a user switch via the UI
# takes effect immediately without an app restart.
_cache: dict[tuple[str, str], Brain] = {}
_registry: BrainProviderRegistry | None = None
_subscribed_to_bus_id: int | None = None


def _get_registry() -> BrainProviderRegistry:
    global _registry
    if _registry is None:
        _registry = BrainProviderRegistry()
    return _registry


def _ensure_bus_subscription(bus: EventBus | None) -> None:
    """Subscribe to ConfigReloaded and clear the cache.

    Idempotent: subscribes at most once per bus instance.
    """
    global _subscribed_to_bus_id
    if bus is None:
        return
    if _subscribed_to_bus_id == id(bus):
        return
    try:
        from jarvis.core.events import ConfigReloaded

        async def _on_reload(event: object) -> None:
            if isinstance(event, ConfigReloaded):
                _cache.clear()
                log.debug("resolver: cache geleert (ConfigReloaded)")

        bus.subscribe_all(_on_reload)
        _subscribed_to_bus_id = id(bus)
    except Exception:  # noqa: BLE001
        # Bus-API-Inkompatibilitaet → ohne Cache-Invalidation weiterleben.
        log.debug("resolver: Bus-Subscription fuer ConfigReloaded fehlgeschlagen")


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def resolve_frontier_brain(
    config: JarvisConfig,
    *,
    bus: EventBus | None = None,
) -> Brain:
    """Returns a ``Brain`` instance for the frontier/deep model.

    Args:
        config: Current Jarvis config.
        bus: Optional. When provided, the cache is invalidated on
            ``ConfigReloaded`` (idempotent).

    Returns:
        An instantiated ``Brain`` implementation.

    Raises:
        RuntimeError: When neither the override, nor the tier config, nor
            the default could be instantiated.
    """
    _ensure_bus_subscription(bus)
    chain = list(_resolve_chain(config))
    if not chain:
        raise RuntimeError(
            "resolve_frontier_brain: keine Provider-Wahl moeglich. "
            "Pruefe config.brain.primary + entry_points('jarvis.brain')."
        )

    last_err: Exception | None = None
    for provider, model in chain:
        cache_key = (provider, model or "")
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            brain = _get_registry().instantiate(
                provider, **({"model": model} if model else {}),
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.info(
                "resolve_frontier_brain: %s/%s nicht instanziierbar (%s) — "
                "naechste Stufe der Fallback-Chain",
                provider, model or "<default>", type(exc).__name__,
            )
            continue
        _cache[cache_key] = brain
        log.debug(
            "resolve_frontier_brain: %s/%s instanziiert", provider, model or "<default>",
        )
        return brain

    raise RuntimeError(
        "resolve_frontier_brain: alle Stufen der Fallback-Chain fehlgeschlagen. "
        f"Letzter Fehler: {last_err!r}. Chain: {chain}"
    )


# ----------------------------------------------------------------------
# Internal — Chain-Building
# ----------------------------------------------------------------------

def _resolve_chain(config: JarvisConfig) -> list[tuple[str, str | None]]:
    """Builds the ordered (provider, model) list for the fallback chain.

    Order:
      1. Power-user override (board.bio.override_*).
      2. brain.sub_jarvis (Wave 4 legacy: tier config with its own fallbacks).
      3. brain.primary with TIER_DEFAULTS_BY_PROVIDER[deep][primary].
      4. local_fallback from brain.local_fallback (last resort).

    Duplicates are filtered — the same (provider, model) pair appears only once.
    """
    chain: list[tuple[str, str | None]] = []

    # Stage 1 — override (power user)
    bio_cfg = getattr(getattr(config, "board", None), "bio", None)
    if bio_cfg is not None and bio_cfg.override_provider:
        chain.append((bio_cfg.override_provider, bio_cfg.override_model or None))

    brain_cfg = config.brain
    # Wave 4 migration: the ``sub_jarvis`` field on BrainConfig was marked
    # as legacy (see core/config.py). We still read it if a configuration
    # still contains ``[brain.sub_jarvis]`` — it serves as a frontier hint
    # for ``resolve_frontier_brain``.
    sub_tier = brain_cfg.sub_jarvis

    # Stage 2 — legacy sub-jarvis tier config from Wave 3 / pre-Wave-4
    # (user had explicitly set the frontier tier there; remains readable)
    if sub_tier is not None and sub_tier.provider:
        primary_model = sub_tier.model or _default_for("deep", sub_tier.provider)
        chain.append((sub_tier.provider, primary_model or None))
        if sub_tier.fallback_provider:
            fb_model = sub_tier.fallback_model or _default_for(
                "deep", sub_tier.fallback_provider,
            )
            chain.append((sub_tier.fallback_provider, fb_model or None))
        if sub_tier.fallback_provider_2:
            fb2_model = sub_tier.fallback_model_2 or _default_for(
                "deep", sub_tier.fallback_provider_2,
            )
            chain.append((sub_tier.fallback_provider_2, fb2_model or None))

    # Stage 3 — primary provider (with frontier default lookup)
    primary = brain_cfg.primary or "claude-api"
    primary_model = _default_for("deep", primary)
    chain.append((primary, primary_model or None))

    # Stage 4 — local fallback (Ollama local or similar, last resort)
    if brain_cfg.local_fallback and brain_cfg.local_fallback != primary:
        chain.append(
            (brain_cfg.local_fallback, brain_cfg.local_fallback_model or None),
        )

    # Filter duplicates while preserving order
    seen: set[tuple[str, str | None]] = set()
    deduped: list[tuple[str, str | None]] = []
    for entry in chain:
        if entry in seen:
            continue
        seen.add(entry)
        deduped.append(entry)
    return deduped


def _default_for(tier: str, provider: str) -> str:
    """Reads TIER_DEFAULTS_BY_PROVIDER without crashing on an unknown provider."""
    return TIER_DEFAULTS_BY_PROVIDER.get(tier, {}).get(provider, "")


# ----------------------------------------------------------------------
# Test-Hooks
# ----------------------------------------------------------------------

def _reset_for_tests() -> None:
    """Reset cache and registry singleton — for tests only."""
    global _registry, _subscribed_to_bus_id
    _cache.clear()
    _registry = None
    _subscribed_to_bus_id = None
