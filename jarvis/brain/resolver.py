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
from typing import TYPE_CHECKING, Any

from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER
from jarvis.brain.provider_registry import BrainProviderRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator

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
                log.debug("resolver: cache cleared (ConfigReloaded)")

        bus.subscribe_all(_on_reload)
        _subscribed_to_bus_id = id(bus)
    except Exception:  # noqa: BLE001
        # Bus API incompatibility → carry on without cache invalidation.
        log.debug("resolver: bus subscription for ConfigReloaded failed")


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
            "resolve_frontier_brain: no provider choice possible. "
            "Check config.brain.primary + entry_points('jarvis.brain')."
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
                "resolve_frontier_brain: %s/%s not instantiable (%s) — "
                "trying next stage of the fallback chain",
                provider, model or "<default>", type(exc).__name__,
            )
            continue
        _cache[cache_key] = brain
        log.debug(
            "resolve_frontier_brain: %s/%s instantiated", provider, model or "<default>",
        )
        return brain

    raise RuntimeError(
        "resolve_frontier_brain: all stages of the fallback chain failed. "
        f"Last error: {last_err!r}. Chain: {chain}"
    )


def frontier_brain_candidates(
    config: JarvisConfig,
    *,
    bus: EventBus | None = None,
) -> Iterator[Brain]:
    """Instantiable frontier brains along the fallback chain, one per family.

    ``resolve_frontier_brain`` answers "give me ONE brain" and crosses families
    only when a stage cannot be *instantiated*. That gate misses the ordinary
    depleted-key case: most providers construct their client happily without
    checking the balance and only fail at CALL time (429/402). A caller that
    wants the cross-family promise of AP-22 to hold at call time iterates this
    instead: try the first candidate, and when the request itself fails, move
    to the next FAMILY — a second model behind the same depleted key would
    just fail the same way, so each provider appears at most once.

    Lazy on purpose: a candidate is instantiated only when the caller reaches
    it, and the shared cache keeps repeated walks free. Yields nothing when no
    stage can be instantiated — the caller's "no provider" path, not an error.
    """
    _ensure_bus_subscription(bus)
    try:
        chain = list(_resolve_chain(config))
    except Exception:  # noqa: BLE001 - a config problem must not kill the caller
        log.info("frontier_brain_candidates: chain could not be built", exc_info=True)
        return
    yielded: set[str] = set()
    for provider, model in chain:
        if provider in yielded:
            continue
        cache_key = (provider, model or "")
        brain = _cache.get(cache_key)
        if brain is None:
            try:
                brain = _get_registry().instantiate(
                    provider, **({"model": model} if model else {}),
                )
            except Exception as exc:  # noqa: BLE001
                log.info(
                    "frontier_brain_candidates: %s/%s not instantiable (%s)",
                    provider, model or "<default>", type(exc).__name__,
                )
                continue
            _cache[cache_key] = brain
        yielded.add(provider)
        yield brain


def _is_fast_tier(config: JarvisConfig, provider: str, model: str | None) -> bool:
    """Whether ``(provider, model)`` is this install's latency-first tier.

    Classification comes from the tier tables and from the user's OWN router
    configuration — never from a model name or a provider name (AP-21). A model
    the tables do not know is treated as qualified: we filter what we know, we
    do not guess from a name like "flash" or "mini", because that is exactly the
    name-based gating that breaks silently for every provider we did not think
    of.
    """
    name = (model or "").strip()
    if not name:
        return False

    router_cfg = getattr(getattr(config, "brain", None), "router", None)
    if (
        getattr(router_cfg, "provider", None) == provider
        and (getattr(router_cfg, "model", None) or "").strip() == name
    ):
        return True

    fast_default = TIER_DEFAULTS_BY_PROVIDER.get("router", {}).get(provider, "")
    deep_default = TIER_DEFAULTS_BY_PROVIDER.get("deep", {}).get(provider, "")
    # A provider whose fast and deep defaults coincide has only one model worth
    # having; filtering it away would leave that provider with nothing.
    return bool(fast_default) and name == fast_default and name != deep_default


def resolve_quality_brain(
    config: JarvisConfig,
    *,
    bus: EventBus | None = None,
) -> Brain | None:
    """A Brain for work that must not be done by a small model, or None.

    ``resolve_frontier_brain`` walks the whole fallback chain, and that chain
    deliberately ends in small, fast, cheap models so a core path never dies.
    For callers whose OUTPUT is the product rather than a step towards it, that
    trade is wrong: the Agentic IDE's prompt composer writes the brief a coding
    agent then works from, and a brief written by a mini model on a depleted
    primary is worse than an honestly plain deterministic one — and worse
    *invisibly*, which is the part that matters. Nobody inspects a prompt that
    looks fine.

    So this resolver skips the latency-first stages and returns None when none
    of the remaining ones can be instantiated, leaving the caller to degrade
    openly. Never raises: None is the answer, not an error.
    """
    _ensure_bus_subscription(bus)
    try:
        chain = list(_resolve_chain(config))
    except Exception:  # noqa: BLE001 - a config problem must not kill the caller
        log.info("resolve_quality_brain: chain could not be built", exc_info=True)
        return None

    for provider, model in chain:
        if _is_fast_tier(config, provider, model):
            log.debug(
                "resolve_quality_brain: skipping %s/%s (latency-first tier)",
                provider, model,
            )
            continue
        cache_key = (provider, model or "")
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            brain = _get_registry().instantiate(
                provider, **({"model": model} if model else {}),
            )
        except Exception as exc:  # noqa: BLE001
            log.info(
                "resolve_quality_brain: %s/%s not instantiable (%s)",
                provider, model or "<default>", type(exc).__name__,
            )
            continue
        _cache[cache_key] = brain
        return brain

    log.info("resolve_quality_brain: no quality-tier provider reachable")
    return None


def _tool_model_selection(config: JarvisConfig) -> tuple[str, str | None]:
    """The Tool Model the user picked: ``(provider, model)``.

    Mirrors what the Tool Model settings tab persists and displays, so the two
    can never disagree about which model the user chose. Both halves are read
    the same defensive way that tab reads them:

    * The tier carries the provider, under the canonical ``tool_model`` name
      with ``computer_use`` still accepted as its read-time alias.
    * The MODEL is a per-provider override — a user who picks a provider
      usually means "that provider's tool model", not its chat default — with
      the provider's plain ``model`` as the floor when no override is set.

    ``("auto", None)`` means the user never pinned one; the caller decides what
    that is worth. Never raises: a malformed section reads as unset.
    """
    try:
        brain_cfg = config.brain
        tier = getattr(brain_cfg, "tool_model", None) or getattr(
            brain_cfg, "computer_use", None
        )
        provider = str(getattr(tier, "provider", None) or "auto").strip() or "auto"
        if provider == "auto":
            return "auto", None
        provider_cfg = brain_cfg.providers.get(provider)
        model = (
            getattr(provider_cfg, "tool_model", None)
            or getattr(provider_cfg, "cu_model", None)
            or getattr(provider_cfg, "model", None)
        )
        return provider, (str(model).strip() or None) if model else None
    except Exception:  # noqa: BLE001 - an unreadable section is an unset one
        log.info("tool-model selection could not be read", exc_info=True)
        return "auto", None


def resolve_tool_model_brain(
    config: JarvisConfig,
    *,
    bus: EventBus | None = None,
) -> Brain | None:
    """The user's configured Tool Model as a Brain, or None.

    Exists because "which model does my own work" is a question the user has
    already answered once, in the Tool Model tab, and callers whose OUTPUT is
    the product should be able to honour that answer instead of running a
    separate chain that lands somewhere else. The Agentic IDE's prompt writer
    is the first: a user who deliberately pointed the Tool Model at a strong
    model and then found their task briefs written by whichever coding CLI
    happened to be signed in has been given a setting that does not settle
    anything.

    Deliberately NOT a chain. A tier resolver walks candidates until one
    answers, which is right when the goal is that a core path never dies; here
    the goal is the opposite — do what the user picked, or say you could not.
    Falling through to a different model would reintroduce exactly the silent
    substitution this resolver exists to end.

    Returns None when no Tool Model is pinned (``auto``), when the pinned
    provider is not installed in this build, or when it cannot be instantiated
    — a missing key being the ordinary case. Never raises.
    """
    _ensure_bus_subscription(bus)
    provider, model = _tool_model_selection(config)
    if provider == "auto":
        log.debug("resolve_tool_model_brain: no Tool Model pinned")
        return None

    cache_key = (provider, model or "")
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        brain = _get_registry().instantiate(
            provider, **({"model": model} if model else {}),
        )
    except Exception as exc:  # noqa: BLE001 - a caller must degrade, not crash
        log.info(
            "resolve_tool_model_brain: %s/%s not instantiable (%s)",
            provider, model or "<default>", type(exc).__name__,
        )
        return None
    _cache[cache_key] = brain
    return brain


def resolve_vision_brain(
    config: JarvisConfig,
    *,
    bus: EventBus | None = None,
) -> Brain | None:
    """A Brain that can actually LOOK at an image, or None.

    Gated purely on the ``supports_vision`` capability (AP-21) — never on a
    provider name or a model id. Several providers in a normal chain are
    text-only (a subscription CLI, a local model), and handing one an image
    does not fail loudly: it answers about the words around the picture as if
    it had seen it. That is the failure mode this resolver exists to prevent,
    so a provider is skipped unless it declares the capability.

    ``supports_vision`` is read defensively rather than assumed True: a
    provider that does not declare it at all is treated as blind, because the
    caller's whole job here is to describe a picture and a confident
    description of an image nobody looked at is worse than no description.

    Never raises. None means "nothing here can see", and the caller says so
    instead of pretending otherwise.
    """
    _ensure_bus_subscription(bus)
    try:
        chain = list(_resolve_chain(config))
    except Exception:  # noqa: BLE001 - a config problem must not kill the caller
        log.info("resolve_vision_brain: chain could not be built", exc_info=True)
        return None

    for provider, model in chain:
        cache_key = (provider, model or "")
        brain = _cache.get(cache_key)
        if brain is None:
            try:
                brain = _get_registry().instantiate(
                    provider, **({"model": model} if model else {}),
                )
            except Exception as exc:  # noqa: BLE001
                log.info(
                    "resolve_vision_brain: %s/%s not instantiable (%s)",
                    provider, model or "<default>", type(exc).__name__,
                )
                continue
            _cache[cache_key] = brain
        if not getattr(brain, "supports_vision", False):
            log.debug(
                "resolve_vision_brain: skipping %s/%s (supports_vision is not set)",
                provider, model or "<default>",
            )
            continue
        return brain

    log.info("resolve_vision_brain: no vision-capable provider reachable")
    return None


def _subscription_connected(provider: str) -> bool:
    """Whether ``provider``'s subscription login is usable right now.

    Asks the provider CLASS through the registry rather than importing an auth
    service per family here, so a subscription brain added later answers for
    itself with no edit to this module (AP-21). A provider that offers no probe
    is treated as connected: the instantiation that follows is the real gate, a
    false "yes" costs one wasted candidate, and a false "no" would hide a
    working subscription the user is paying for. The drift guard in
    ``tests/unit/brain/test_resolve_subscription_brain.py`` is what keeps that
    lenient default from quietly becoming the normal case.
    """
    try:
        brain_cls = _get_registry().get_class(provider)
    except Exception:  # noqa: BLE001 - an unloadable plugin is simply not usable
        return False
    probe = getattr(brain_cls, "subscription_connected", None)
    if probe is None:
        return True
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001 - a probe must never break a turn
        log.info("resolve_subscription_brain: %s probe failed", provider, exc_info=True)
        return False


def _native_system_channel(provider: str) -> bool:
    """Whether ``provider`` forwards a caller's system contract on a real channel.

    Asked of the provider CLASS, like :func:`_subscription_connected`, so a new
    subscription brain answers for itself (AP-21). ``True`` means the CLI takes
    the contract as its system prompt; ``False`` (or no flag at all) means the
    contract can only be PREPENDED to the user prompt, where an agentic CLI may
    read it as text to react to rather than orders to follow. Never raises — an
    unloadable class simply does not get the preference.
    """
    try:
        brain_cls = _get_registry().get_class(provider)
    except Exception:  # noqa: BLE001 - an unloadable plugin earns no preference
        return False
    return bool(getattr(brain_cls, "native_system_prompt", False))


def _subscription_candidates(config: JarvisConfig) -> list[str]:
    """Registered brain providers billed against a subscription, in card order.

    Membership comes from the provider cards' billing mode — never a name list,
    so a fourth coding CLI joins the chain by shipping a card (AP-21/AP-22).
    ``config`` is accepted for signature symmetry with the other helpers here.
    """
    from jarvis.ui.web.provider_spec import PROVIDERS, provider_billing

    available = set(_get_registry().available())
    return [
        spec.id
        for spec in PROVIDERS
        if spec.tier == "brain"
        and spec.id in available
        and provider_billing(spec).startswith("subscription")
    ]


def resolve_subscription_brain(
    config: JarvisConfig,
    *,
    bus: EventBus | None = None,
    cli_timeout_s: float | None = None,
) -> Brain | None:
    """A Brain billed against a connected subscription, or None.

    For callers whose output IS the product and who are already waiting seconds
    — the Agentic IDE's prompt composer and work splitter. It exists so those
    callers can spend a plan the user already pays for instead of a per-token
    key, and so a downloader whose ONLY credential is a coding subscription
    stops falling through to the deterministic prompt on every instruction.

    Never raises: None means "no subscription can do this", and the caller falls
    through to the API-billed resolver and finally to its own plain layer.

    **A candidate that cannot honour ``structured_prompts`` is SKIPPED, never
    used conversationally.** A CLI brain in conversational mode replaces the
    caller's contract with "answer in one to three short sentences" and returns
    three fluent sentences that read like a valid brief. That is strictly worse
    than returning None, because the caller's own fallback is at least honest
    about being plain.

    Deliberately does NOT populate ``_cache``: its key is ``(provider, model)``
    and is shared with the voice-tier resolvers, so a cached structured instance
    would later be handed to a spoken turn built to expect the conversational
    wrapper.
    """
    _ensure_bus_subscription(bus)
    try:
        candidates = _subscription_candidates(config)
    except Exception:  # noqa: BLE001 - a spec problem must not kill the caller
        log.info("resolve_subscription_brain: candidates unavailable", exc_info=True)
        return None

    # Contract fidelity outranks card order: a CLI with a dedicated system
    # channel obeys the caller's contract, while one that can only prepend it
    # answers as an agent reading orders inside its input. Live 2026-08-11:
    # antigravity, first by card order, wrote every Agentic IDE pane title as a
    # chat acknowledgement ("Understood! I see …") while the signed-in Claude
    # CLI — which takes a real ``--system-prompt`` — was never asked. The sort
    # is stable, so card order still decides among equals, and a preferred
    # provider that is not signed in falls through exactly as before.
    candidates.sort(key=lambda provider: not _native_system_channel(provider))

    for provider in candidates:
        if not _subscription_connected(provider):
            log.debug("resolve_subscription_brain: %s not signed in", provider)
            continue
        kwargs: dict[str, Any] = {"structured_prompts": True}
        model = _deep_model_for(config, provider)
        if model:
            kwargs["model"] = model
        if cli_timeout_s and cli_timeout_s > 0:
            kwargs["cli_timeout_s"] = float(cli_timeout_s)
        try:
            brain = _get_registry().instantiate(provider, **kwargs)
        except TypeError:
            # Signature probe, not a name check (AP-21): no structured mode
            # means no brief, so skip rather than degrade invisibly.
            log.info(
                "resolve_subscription_brain: %s cannot forward a system "
                "contract — skipping rather than answering conversationally",
                provider,
            )
            continue
        except Exception as exc:  # noqa: BLE001
            log.info(
                "resolve_subscription_brain: %s not instantiable (%s)",
                provider,
                type(exc).__name__,
            )
            continue
        log.info("resolve_subscription_brain: writing on %s", provider)
        return brain

    log.info("resolve_subscription_brain: no connected subscription reachable")
    return None


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
    # Wave 4 migration / 2026-06-29 Jarvis-Agents rename: the field was
    # ``sub_jarvis``, now ``worker`` (accepts old key via AliasChoices). We
    # still read it if a configuration contains ``[brain.worker]`` or the
    # legacy ``[brain.sub_jarvis]`` — it serves as a frontier hint for
    # ``resolve_frontier_brain``.
    sub_tier = brain_cfg.worker

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

    # Stage 3 — primary provider. Honor the user's PICKED model
    # ([brain.providers[primary]].model) before the hardcoded deep TIER default —
    # otherwise an OpenRouter user who chose a free model has skill-creation /
    # board-bio resolve onto the paid anthropic/claude-opus default and bills the
    # gateway key (§3/AP-21). The default only applies when nothing is pinned.
    primary = brain_cfg.primary or "claude-api"
    primary_pc = (brain_cfg.providers or {}).get(primary)
    primary_model = (getattr(primary_pc, "model", "") or "").strip() or _default_for(
        "deep", primary,
    )
    chain.append((primary, primary_model or None))

    # Stage 4 — local fallback (Ollama local or similar, last resort)
    if brain_cfg.local_fallback and brain_cfg.local_fallback != primary:
        chain.append(
            (brain_cfg.local_fallback, brain_cfg.local_fallback_model or None),
        )

    # Stage 5 — key-aware cross-family tail (open-source AP-22). Every stage
    # above can resolve onto a KEYLESS provider — most often the ``claude-api``
    # default on a host whose only key is a different family and who never
    # switched ``[brain.primary]``. ``ClaudeAPIBrain`` instantiates fine without
    # a key and only 401s at call time, so background bio/persona/skill turns
    # would repeat-401 forever. Append the families the user ACTUALLY holds a
    # credential for so the resolve can land on the real key. Reuses the
    # manager's key-aware probe (PROVIDER_SECRET_CANDIDATES + reachability),
    # never a fresh hardcoded provider list.
    chain_providers = {provider for provider, _ in chain}
    chain.extend(
        _reachable_keyed_families(config, exclude=frozenset(chain_providers)),
    )

    # Stage 6 — keyless local tail (local-first mandate 2026-07-25): the
    # registered local brains (spec-declared auth_mode "none": ollama,
    # local-openai) close the chain, so a ZERO-key install still lands every
    # background resolve on its own hardware. Deliberately AFTER the keyed
    # families — a cloud family the user set up stays preferred, and a dead
    # local server just fast-fails on its 2 s connect timeout.
    chain_providers = {provider for provider, _ in chain}
    chain.extend(_local_tail(config, exclude=frozenset(chain_providers)))

    # Filter duplicates while preserving order
    seen: set[tuple[str, str | None]] = set()
    deduped: list[tuple[str, str | None]] = []
    for entry in chain:
        if entry in seen:
            continue
        seen.add(entry)
        deduped.append(entry)

    # A keyless claude-api entry (the legacy default) must not short-circuit the
    # resolve ahead of the user's real key: ClaudeAPIBrain instantiates without a
    # key and only 401s at call time. Drop claude-api entries ONLY when no usable
    # Anthropic credential is present AND a reachable keyed family exists to take
    # over. A deliberately-switched non-claude primary is never touched (it stays
    # exactly as before), and a host with NO reachable family keeps claude-api so
    # the failure stays legible.
    if not _provider_reachable(config, "claude-api"):
        has_reachable_alternative = any(
            entry[0] != "claude-api" and _provider_reachable(config, entry[0])
            for entry in deduped
        )
        if has_reachable_alternative:
            deduped = [entry for entry in deduped if entry[0] != "claude-api"]
    return deduped


def _default_for(tier: str, provider: str) -> str:
    """Reads TIER_DEFAULTS_BY_PROVIDER without crashing on an unknown provider."""
    return TIER_DEFAULTS_BY_PROVIDER.get(tier, {}).get(provider, "")


def _deep_model_for(config: JarvisConfig, provider: str) -> str | None:
    """Deep model for *provider*, honoring the user's configured model.

    The user's picked ``[brain.providers[provider]].model`` wins over the
    hardcoded deep tier default — otherwise a cross-family tail for a model-less
    OpenRouter user would bill the paid Anthropic default (§3/AP-21), the same
    precedence the primary stage applies.
    """
    provider_cfg = (config.brain.providers or {}).get(provider)
    picked = (getattr(provider_cfg, "model", "") or "").strip()
    return picked or _default_for("deep", provider) or None


def _provider_reachable(config: JarvisConfig, provider: str) -> bool:
    """Key-aware reachability, mirroring the BrainManager pre-boot key check.

    A brain provider is reachable when it needs no API key (a local/self-hosted
    brain carrying no ``PROVIDER_SECRET_CANDIDATES`` entry, e.g. Ollama), when a
    credential for it is configured, or when it authenticates via an on-disk
    OAuth login (codex). Same source of truth as the manager dead-list — never a
    hardcoded provider-name list (AP-22). ``config`` is accepted for signature
    symmetry with the other resolver helpers and future per-config overrides.
    """
    from jarvis.core.config import PROVIDER_SECRET_CANDIDATES, get_provider_secret

    if provider not in PROVIDER_SECRET_CANDIDATES:
        # No API-key family — a local/self-hosted brain that needs no credential.
        return True
    if get_provider_secret(provider):
        return True
    from jarvis.brain.manager import _keyless_provider_is_rescued_by_oauth

    return _keyless_provider_is_rescued_by_oauth(provider)


def _reachable_keyed_families(
    config: JarvisConfig, *, exclude: frozenset[str] = frozenset(),
) -> list[tuple[str, str | None]]:
    """Key-aware cross-family tail: the brain families the user actually holds a
    credential for (open-source AP-22).

    Iterates the canonical secrets registry (``PROVIDER_SECRET_CANDIDATES``) —
    the same source the BrainManager pre-boot dead-list uses — intersected with
    the registered brain providers and the key-aware reachability probe, so a
    keyless ``claude-api`` default falls through to the user's real key. Not a
    fresh hardcoded provider list: order and membership come from the shared
    registry. ``exclude`` skips families already present in the chain.
    """
    from jarvis.core.config import PROVIDER_SECRET_CANDIDATES

    available = set(_get_registry().available())
    tail: list[tuple[str, str | None]] = []
    for provider in PROVIDER_SECRET_CANDIDATES:
        if provider in exclude or provider not in available:
            # Skip already-chained families and non-brain secret slots
            # (groq / realtime families are not registered brain providers).
            continue
        if not _provider_reachable(config, provider):
            continue
        tail.append((provider, _deep_model_for(config, provider)))
    return tail


def _local_tail(
    config: JarvisConfig, *, exclude: frozenset[str] = frozenset(),
) -> list[tuple[str, str | None]]:
    """Keyless local brains as the chain's last resort (local-first mandate).

    Membership is SPEC-driven — every registered brain provider whose card
    declares ``auth_mode == "none"`` in ``jarvis.ui.web.provider_spec`` —
    never a hardcoded name list (AP-21/22). Card order is preserved.
    OAuth-subscription brains (codex/antigravity) declare their own auth
    modes and can never leak in here.
    """
    from jarvis.ui.web.provider_spec import PROVIDERS

    available = set(_get_registry().available())
    tail: list[tuple[str, str | None]] = []
    for spec in PROVIDERS:
        if spec.tier != "brain" or spec.auth_mode != "none":
            continue
        if spec.id in exclude or spec.id not in available:
            continue
        tail.append((spec.id, _deep_model_for(config, spec.id)))
    return tail


# ----------------------------------------------------------------------
# Test-Hooks
# ----------------------------------------------------------------------

def _reset_for_tests() -> None:
    """Reset cache and registry singleton — for tests only."""
    global _registry, _subscribed_to_bus_id
    _cache.clear()
    _registry = None
    _subscribed_to_bus_id = None
