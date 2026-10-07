"""Which providers work the user never asked for may bill.

Background work — memory curation, profile text, anything a timer or an event
starts rather than a user turn — must never become a surprise invoice. The
maintainer's rule (2026-09-30), after ~5 EUR vanished in three hours while
nobody was at the machine: **once a subscription is connected, background work
runs on subscriptions (and free local models) only.** When the subscription is
logged out, out of credit, rate limited or simply failing, the work waits and
retries later; it never slides onto a per-token API key.

"Connected" is deliberately sticky. A login probe answers for the moment it is
asked, and the moments that matter are exactly the bad ones: an expired OAuth
token, a CLI that crashed during the probe, a plan whose usage window is spent.
Before this module a single ``False``/``None`` probe emptied the subscription
set and every stored key rejoined the wiki chain. So a subscription seen
connected once keeps the install in subscription mode for
:data:`SUBSCRIPTION_MEMORY_S`; only a user who stays signed out for that long is
treated as a key-only install again.

An install that never connected a subscription is unaffected: its keys stay
usable for background work, so a single-key download keeps a working product
(AGENTS.md "any single key must work").

Provider membership comes from the provider cards' billing mode, never a name
list (AP-21), so a new subscription CLI or local runtime joins by shipping a
card.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.protocols import CapacityDeferred

log = logging.getLogger(__name__)

#: How long one connected sighting keeps the install in subscription mode.
SUBSCRIPTION_MEMORY_S = 30 * 24 * 3600.0

#: How long a login probe answer stands. The probe shells out to a vendor CLI,
#: and background callers ask on every batch.
_PROBE_TTL_S = 30.0

_lock = threading.Lock()
_probe_cache: dict[str, tuple[float, bool | None]] = {}

#: Test seam: replaces the provider-class login probe.
_probe_override: Callable[[str], bool | None] | None = None


class BackgroundDeferred(CapacityDeferred):
    """No provider background work may bill is usable right now; retry later."""


@dataclass(frozen=True, slots=True)
class BackgroundProviders:
    """The providers a background job may use, and why."""

    subscription_mode: bool
    allowed: tuple[str, ...]
    reason: str

    def permits(self, provider: str) -> bool:
        return provider in self.allowed


def _marker_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "data" / "subscription_seen.json"


def _read_markers() -> dict[str, float]:
    try:
        raw = json.loads(_marker_path().read_text(encoding="utf-8"))
    except FileNotFoundError:  # no marker file yet: nothing has been recorded
        return {}
    except (OSError, ValueError):
        log.warning("background policy: unreadable subscription marker, starting empty")
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): float(ts)
        for name, ts in raw.items()
        if isinstance(ts, (int, float))
    }


def _write_markers(markers: dict[str, float]) -> None:
    path = _marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".subscription_seen.")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(markers, handle)
        os.replace(tmp, path)
    except OSError:
        # A read-only data dir only loses the stickiness, never a turn.
        log.warning("background policy: could not persist subscription marker", exc_info=True)


def _billing(provider: str) -> str:
    """``subscription`` / ``subscription_or_api`` / ``local`` / ``api``.

    A provider without a card is treated as ``api``: in subscription mode an
    unknown provider is not allowed to bill.
    """
    from jarvis.ui.web.provider_spec import PROVIDERS, provider_billing

    for spec in PROVIDERS:
        if spec.id == provider and spec.tier == "brain":
            return provider_billing(spec)
    return "api"


def billing_kind(provider: str) -> str:
    """Public read of :func:`_billing` for callers that gate on it."""
    return _billing(provider)


def subscription_capable(provider: str) -> bool:
    return _billing(provider).startswith("subscription")


def keyless_local(provider: str) -> bool:
    return _billing(provider) == "local"


def _registry_probe(provider: str) -> bool | None:
    """Ask the provider class whether its subscription login is usable.

    Module-level so the test suite can replace it once for every test: the
    real probe shells out to the vendor CLI and reads the developer's login.
    """
    try:
        from jarvis.brain.provider_registry import BrainProviderRegistry

        provider_class = BrainProviderRegistry().get_class(provider)
        probe = getattr(provider_class, "subscription_connected", None)
        return bool(probe()) if callable(probe) else None
    except Exception:  # noqa: BLE001 - an unknown answer, not a broken caller
        log.debug("background policy: login probe failed for %s", provider, exc_info=True)
        return None


def login_state(provider: str) -> bool | None:
    """Memoised subscription-login probe: True, False, or None (unknown)."""
    now = time.monotonic()
    with _lock:
        cached = _probe_cache.get(provider)
        if cached is not None and now - cached[0] < _PROBE_TTL_S:
            return cached[1]
    probe = _probe_override if _probe_override is not None else _registry_probe
    result = probe(provider)
    with _lock:
        _probe_cache[provider] = (time.monotonic(), result)
    if result is True:
        note_connected(provider)
    return result


def note_connected(provider: str) -> None:
    """Record that *provider*'s subscription was usable just now."""
    with _lock:
        markers = _read_markers()
        markers[provider] = time.time()
        _write_markers(markers)


def forget(provider: str | None = None) -> None:
    """Drop the sticky marker (an explicit disconnect in the app, or tests)."""
    with _lock:
        if provider is None:
            _probe_cache.clear()
            markers: dict[str, float] = {}
        else:
            _probe_cache.pop(provider, None)
            markers = {name: ts for name, ts in _read_markers().items() if name != provider}
        _write_markers(markers)


def subscription_mode(candidates: Iterable[str] = ()) -> bool:
    """Whether the install runs background work on subscriptions only.

    True while any subscription-capable candidate is signed in now, or one was
    seen signed in within :data:`SUBSCRIPTION_MEMORY_S`.
    """
    for provider in candidates:
        if subscription_capable(provider) and login_state(provider) is True:
            return True
    cutoff = time.time() - SUBSCRIPTION_MEMORY_S
    return any(ts >= cutoff for ts in _read_markers().values())


def background_providers(candidates: Iterable[str]) -> BackgroundProviders:
    """Filter *candidates* (in order) to the ones background work may bill.

    Subscription mode keeps a subscription only while its login is not known
    to be signed out (a ``subscription_or_api`` card only while signed in, since
    signed out it would spend its key slot) plus keyless local runtimes. Without
    a subscription the candidates pass through unchanged.
    """
    ordered = list(dict.fromkeys(name for name in candidates if name))
    if not subscription_mode(ordered):
        return BackgroundProviders(False, tuple(ordered), "no subscription connected")
    allowed: list[str] = []
    for name in ordered:
        billing = _billing(name)
        if billing == "local":
            allowed.append(name)
        elif billing == "subscription" and login_state(name) is not False:
            allowed.append(name)
        elif billing == "subscription_or_api" and login_state(name) is True:
            allowed.append(name)
    reason = "subscriptions and local models only" if allowed else (
        "subscription connected but not usable right now; waiting instead of using an API key"
    )
    return BackgroundProviders(True, tuple(allowed), reason)


def reset_for_tests() -> None:
    global _probe_override
    with _lock:
        _probe_cache.clear()
    _probe_override = None


__all__ = [
    "SUBSCRIPTION_MEMORY_S",
    "BackgroundDeferred",
    "BackgroundProviders",
    "background_providers",
    "billing_kind",
    "forget",
    "keyless_local",
    "login_state",
    "note_connected",
    "subscription_capable",
    "subscription_mode",
]
