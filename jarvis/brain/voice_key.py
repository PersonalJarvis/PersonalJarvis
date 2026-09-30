"""Which paid key the realtime voice call owns, so other work can stay off it.

A user who adds an API key to power a realtime voice model (GPT-Live) pays per
token for every other request that happens to share that key. Text chat, agent
reviews and health probes then drain the voice budget silently (live
2026-09-29: the voice call failed with "no credits" after background work had
spent the key). This module answers one question for those callers: does this
brain provider bill the key that the configured voice call depends on?

The answer comes from the plugins' own credential declarations — the realtime
provider's ``credential_candidates`` against the brain provider's secret slots
in ``PROVIDER_SECRET_CANDIDATES`` — never from a provider name (AP-21).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

log = logging.getLogger(__name__)

_REALTIME_GROUP = "jarvis.realtime"


def voice_key_slots(cfg: Any) -> frozenset[str]:
    """Keyring slots the configured realtime voice provider bills, or empty.

    Empty when the voice runs in pipeline mode, no realtime provider is set, or
    the provider signs in without a key (a subscription voice owns no key).
    Never raises: a plugin that cannot load owns nothing.
    """
    voice = getattr(cfg, "voice", None)
    if str(getattr(voice, "mode", "") or "").strip() != "realtime":
        return frozenset()
    brain = getattr(cfg, "brain", None)
    realtime = getattr(brain, "realtime", None)
    provider_id = str(getattr(realtime, "provider", "") or "").strip()
    if not provider_id:
        return frozenset()
    try:
        from jarvis.core.registry import load

        provider_cls = load(_REALTIME_GROUP, provider_id)
    except Exception:  # noqa: BLE001 - an unloadable plugin bills nothing
        log.debug("voice_key_slots: realtime plugin %r not loadable", provider_id, exc_info=True)
        return frozenset()
    candidates = tuple(getattr(provider_cls, "credential_candidates", ()) or ())
    return frozenset(str(slot) for slot, _env in candidates if slot)


def bills_voice_key(cfg: Any, brain_provider: str | None) -> bool:
    """Whether ``brain_provider`` pays with the key the voice call depends on."""
    return _bills(voice_key_slots(cfg), brain_provider)


def _bills(slots: frozenset[str], brain_provider: str | None) -> bool:
    if not slots or not brain_provider:
        return False
    from jarvis.core.config import PROVIDER_SECRET_CANDIDATES

    own = PROVIDER_SECRET_CANDIDATES.get(str(brain_provider).strip(), ())
    return any(slot in slots for slot, _env in own)


def _signed_in_on_subscription(brain_provider: str) -> bool:
    """Whether the provider offers a subscription login AND one is present.

    Such a brain (a coding CLI on its plan) spends a plan, not the voice key,
    so the chain filter never drops it. Without the login it would fall back
    to the shared key slot, so it counts as billing the key like any other.
    Never raises: an unreadable card or a failed probe counts as key-only.
    """
    try:
        from jarvis.ui.web.provider_spec import PROVIDERS, provider_billing
    except Exception:  # noqa: BLE001 - no cards means no subscription to protect
        log.debug("voice_key: provider cards unavailable", exc_info=True)
        return False
    if not any(
        spec.id == brain_provider and provider_billing(spec).startswith("subscription")
        for spec in PROVIDERS
    ):
        return False
    try:
        from jarvis.brain.manager import _keyless_provider_is_rescued_by_oauth

        return _keyless_provider_is_rescued_by_oauth(brain_provider)
    except Exception:  # noqa: BLE001 - a failed probe is not a login
        log.debug("voice_key: login probe failed for %s", brain_provider, exc_info=True)
        return False


def without_voice_key(
    cfg: Any,
    chain: Iterable[tuple[str, Any]],
    *,
    is_alternative: Callable[[str], bool] | None = None,
    keep: Iterable[tuple[str, Any]] = (),
) -> list[tuple[str, Any]]:
    """``chain`` without the entries that bill the voice key, when others remain.

    The realtime voice key pays for the voice call and its thinking model only
    (user mandate 2026-09-29). Every other request — chat turns, scheduled
    routines, background resolves — leaves it out while another entry can
    answer; moving it to the back was not enough, because a depleted fallback
    ahead of it still handed the turn to the key (live 2026-09-29: five test
    chat turns spent $6.82 on the voice key's deep model after OpenRouter ran
    out of credits).

    ``is_alternative`` decides which remaining providers count as able to
    answer (default: every one). When none does, the chain is returned
    unchanged, so a single-key install keeps working on that key (AP-22).
    ``keep`` names entries that stay regardless, such as a tool-calling lead
    no other provider could take or a provider the person switched to. A brain signed in on its subscription never
    counts as billing the key.
    """
    items = list(chain)
    slots = voice_key_slots(cfg)
    if not slots:
        return items
    billed = {
        provider
        for provider, _model in items
        if _bills(slots, provider) and not _signed_in_on_subscription(provider)
    }
    if not billed:
        return items
    accepts = is_alternative or (lambda _provider: True)
    if not any(provider not in billed and accepts(provider) for provider, _ in items):
        return items
    kept = set(keep)
    return [entry for entry in items if entry in kept or entry[0] not in billed]
