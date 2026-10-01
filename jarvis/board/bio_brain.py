"""Which brain writes the Board bio — the background billing rule applied.

The bio is background work: an unlocked achievement or a refresh click starts
it, never a voice or chat turn. It therefore follows
:mod:`jarvis.brain.background_policy` (maintainer rule 2026-09-30, "auf Abo
auslegen"):

* **Subscription mode** (a subscription is connected, or was within the
  policy's memory window): the bio is written on the first policy-allowed
  subscription that forwards the caller's contract, else on a free local
  model. When neither is usable the resolve raises
  :class:`~jarvis.brain.background_policy.BackgroundDeferred`; the generator
  keeps the old bio and the next board event tries again. It never slides onto
  a per-token API key.
* **Key-only install** (no subscription ever connected): today's frontier
  chain, unchanged, so a single-key download keeps a working bio (AGENTS.md
  "any single key must work").
* ``[board.bio].override_provider/override_model`` is the user's deliberate
  pin and is honoured as-is in both modes, even when it names a keyed
  provider: it is the only way the bio bills a per-token key while a
  subscription is connected. An override that cannot be instantiated falls
  through to the subscription path, never to the keyed chain.

Provider membership comes from the provider cards' billing mode (through the
policy), never from a provider name (AP-21).

Blocking by design: the policy's login probes and a CLI brain's construction
touch the filesystem or a vendor CLI, so callers run this in a worker thread
(``BioGenerator`` does).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from jarvis.brain import background_policy
from jarvis.brain import resolver as brain_resolver

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.core.config import JarvisConfig
    from jarvis.core.protocols import Brain

log = logging.getLogger(__name__)

#: Budget for one bio generation, and the per-call CLI budget handed to a
#: subscription brain. A subscription CLI pays a cold start before it writes a
#: word; the old 30 s cap was sized for a warm HTTP API.
BIO_TIMEOUT_S = 90.0


def resolve_bio_brain(config: JarvisConfig, *, bus: EventBus | None = None) -> Brain:
    """The brain the next bio may be written on.

    Raises:
        BackgroundDeferred: subscription mode, and no subscription or local
            model is usable right now. Nothing was billed.
        RuntimeError: key-only install whose frontier chain is exhausted
            (``resolve_frontier_brain``'s own contract).
    """
    policy = background_policy.background_providers(_brain_provider_ids())
    if not policy.subscription_mode:
        return brain_resolver.resolve_frontier_brain(config, bus=bus)

    override = _override_entry(config)
    if override is not None:
        # The user's deliberate pin (like an agent seat): the ONLY way the bio
        # bills a per-token key while a subscription is connected.
        brain = _instantiate(*override)
        if brain is not None:
            log.info("bio brain: power-user override %s", override[0])
            return brain

    brain = _subscription_brain(config, policy)
    if brain is not None:
        return brain

    for provider, model in _local_candidates(config, policy):
        brain = _instantiate(provider, model)
        if brain is not None:
            log.info("bio brain: local model on %s", provider)
            return brain

    raise background_policy.BackgroundDeferred(
        f"No subscription or local model can write the bio right now ({policy.reason})."
    )


def _subscription_brain(
    config: JarvisConfig, policy: background_policy.BackgroundProviders,
) -> Any | None:
    """The first policy-allowed subscription that takes the bio's contract.

    Same shape as ``resolve_subscription_brain`` — card order, a CLI with a
    real system channel first, ``structured_prompts`` required — but it walks
    EVERY subscription the policy allows. That resolver stops at its first
    connected candidate, which the policy may still refuse (a card with an
    API-key slot whose login is not confirmed), and a second subscription
    that could have written the bio would never be asked.
    """
    try:
        candidates = [
            name for name in brain_resolver._subscription_candidates(config)
            if policy.permits(name)
        ]
    except Exception:  # noqa: BLE001 - a card problem means no subscription, not a crash
        log.info("bio brain: subscription candidates unavailable", exc_info=True)
        return None
    candidates.sort(key=lambda name: not brain_resolver._native_system_channel(name))

    for provider in candidates:
        kwargs: dict[str, Any] = {
            "structured_prompts": True,
            "cli_timeout_s": BIO_TIMEOUT_S,
        }
        model = brain_resolver._deep_model_for(config, provider)
        if model:
            kwargs["model"] = model
        try:
            brain = brain_resolver._get_registry().instantiate(provider, **kwargs)
        except TypeError:
            # No structured mode means the bio contract would be replaced by a
            # conversational wrapper; skip rather than write a chat answer.
            log.info("bio brain: %s cannot forward a system contract, skipped", provider)
            continue
        except Exception as exc:  # noqa: BLE001 - an unusable candidate is skipped
            log.info(
                "bio brain: subscription %s not instantiable (%s)",
                provider, type(exc).__name__,
            )
            continue
        log.info("bio brain: subscription %s", provider)
        return brain
    return None


def _brain_provider_ids() -> list[str]:
    """Every brain card id, in card order — the policy picks what may bill."""
    from jarvis.ui.web.provider_spec import PROVIDERS

    return [spec.id for spec in PROVIDERS if spec.tier == "brain"]


def _override_entry(config: JarvisConfig) -> tuple[str, str | None] | None:
    bio_cfg = getattr(getattr(config, "board", None), "bio", None)
    provider = (getattr(bio_cfg, "override_provider", None) or "").strip()
    if not provider:
        return None
    model = (getattr(bio_cfg, "override_model", None) or "").strip()
    return provider, model or None


def _local_candidates(
    config: JarvisConfig, policy: background_policy.BackgroundProviders,
) -> list[tuple[str, str | None]]:
    """Keyless local brains the policy allows, the user's own picks first.

    A local primary or ``local_fallback`` leads (with the model the user chose
    for it); every other registered local runtime follows in card order, the
    same membership as the resolver's local tail.
    """
    brain_cfg = config.brain
    local_fallback = (brain_cfg.local_fallback or "").strip()
    fallback_model = (brain_cfg.local_fallback_model or "").strip() or None
    preferred = [brain_cfg.primary or "", local_fallback]
    ordered = list(dict.fromkeys(
        [name for name in preferred if name in policy.allowed] + list(policy.allowed)
    ))
    try:
        available = set(brain_resolver._get_registry().available())
    except Exception:  # noqa: BLE001 - no registry means no local brain, not a crash
        log.info("bio brain: brain registry unavailable", exc_info=True)
        return []
    candidates: list[tuple[str, str | None]] = []
    for name in ordered:
        if not background_policy.keyless_local(name) or name not in available:
            continue
        if name == local_fallback and fallback_model:
            candidates.append((name, fallback_model))
        else:
            candidates.append((name, brain_resolver._deep_model_for(config, name)))
    return candidates


def _instantiate(provider: str, model: str | None) -> Any | None:
    try:
        return brain_resolver._get_registry().instantiate(
            provider, **({"model": model} if model else {}),
        )
    except Exception as exc:  # noqa: BLE001 - an unusable candidate is skipped, not fatal
        log.info(
            "bio brain: %s/%s not instantiable (%s)",
            provider, model or "<default>", type(exc).__name__,
        )
        return None


__all__ = ["BIO_TIMEOUT_S", "resolve_bio_brain"]
