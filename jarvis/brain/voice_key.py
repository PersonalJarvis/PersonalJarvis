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
    slots = voice_key_slots(cfg)
    if not slots or not brain_provider:
        return False
    from jarvis.core.config import PROVIDER_SECRET_CANDIDATES

    own = PROVIDER_SECRET_CANDIDATES.get(str(brain_provider).strip(), ())
    return any(slot in slots for slot, _env in own)
