"""Frontier auto-switch — at boot Hauptjarvis checks whether newer models are
available and switches automatically (user mandate 2026-04-28).

Procedure:
1. Resolver queries /v1/models per Hauptjarvis provider (claude-api, gemini,
   openai). The former Sub-Jarvis tier (Wave-4 migration: removed) is
   explicitly excluded.
2. If new model > old model: mutate ``BrainProviderConfig`` in place
   (Pydantic ``extra="allow"`` permits mutation).
3. Emit a ``FrontierModelSwitched`` event on the bus.
4. Record the switch in the ``_pending_switches`` list — the frontend shows a
   modal, the user clicks OK -> POST /api/frontier/ack -> list cleared.

No smoke-test in the boot path: that would cost 4 additional API round-trips
(1 per provider) and delay boot by 1-2 s. If the new model is broken, the first
brain call will fail and the existing fallback mechanism (RateLimitTracker,
dead-providers) kicks in.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from jarvis.brain.frontier_resolver import (
    STALE_MODELS,
    SUPPORTED_PROVIDERS,
    FrontierResolver,
)
from jarvis.core.bus import EventBus
from jarvis.core.config_writer import set_brain_provider_model
from jarvis.core.events import FrontierModelSwitched

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FrontierSwitch:
    """A completed frontier switch (for the modal / audit log)."""

    provider: str
    tier: str           # "fast" | "deep"
    old_model: str
    new_model: str
    switched_at: float


# Module singleton: unacknowledged switches.
# Frontend reads via GET /api/frontier/pending; acknowledged via POST /api/frontier/ack.
_pending_lock = threading.Lock()
_pending_switches: list[FrontierSwitch] = []


def get_pending_switches() -> list[FrontierSwitch]:
    """Return switches that have not yet been acknowledged (REST hook).

    Fetched by the frontend on render-mount and after every WebSocket
    reconnect — prevents the modal from being lost if the user had no
    tab open during the switch.
    """
    with _pending_lock:
        return list(_pending_switches)


def get_pending_switches_as_dict() -> list[dict[str, Any]]:
    """JSON-serialisable variant for FastAPI responses."""
    with _pending_lock:
        return [asdict(s) for s in _pending_switches]


def ack_pending_switches() -> int:
    """Mark all pending switches as acknowledged (user OK). Returns count."""
    with _pending_lock:
        count = len(_pending_switches)
        _pending_switches.clear()
        return count


def _add_pending(switch: FrontierSwitch) -> None:
    with _pending_lock:
        _pending_switches.append(switch)


async def apply_frontier_resolution(
    config: Any,                        # JarvisConfig — Any to avoid circular import
    resolver: FrontierResolver,
    bus: EventBus | None,
) -> list[FrontierSwitch]:
    """Boot hook: determine frontier models and mutate config + emit events.

    Acts ONLY on Hauptjarvis providers (see SUPPORTED_PROVIDERS). The
    ``[brain.sub_jarvis]`` legacy block (Wave-4 migration) is left untouched
    by its explicit ``model = "..."`` override — only
    ``config.brain.providers[<p>].{model,deep_model}`` is updated here.

    Returns the list of completed switches (may be empty if already on frontier).

    Gated behind ``[brain] frontier_auto_apply`` (default False, user mandate
    2026-06-20 "providers must NOT switch by themselves"): when disabled this is
    a complete no-op — no /v1/models query, no TOML write, no in-memory mutation,
    no event — and the configured models are kept verbatim. A newer model is then
    only ever adopted by an explicit pick in the per-provider model picker.
    """
    switches: list[FrontierSwitch] = []

    if not bool(getattr(config.brain, "frontier_auto_apply", False)):
        log.debug(
            "Frontier auto-switch disabled (brain.frontier_auto_apply=False) — "
            "keeping configured models verbatim."
        )
        return switches

    providers_dict = getattr(config.brain, "providers", None) or {}

    for prov_name in SUPPORTED_PROVIDERS:
        provider_cfg = providers_dict.get(prov_name)
        if provider_cfg is None:
            continue

        # Fast-Tier (model)
        old_fast = getattr(provider_cfg, "model", "") or ""
        try:
            new_fast = await resolver.resolve_latest(prov_name, "fast")
        except Exception as exc:  # noqa: BLE001 — resolver must not stop the boot.
            log.warning(
                "Resolver-Crash fuer %s/fast: %s — keep TOML-Default.",
                prov_name, exc,
            )
            new_fast = None

        # Defense-in-depth: never downgrade to stale models, even if the
        # resolver pick lands there for some reason (e.g. a new model name
        # matches the heuristic but is actually old).
        if new_fast in STALE_MODELS:
            log.warning(
                "Resolver proposed %s/fast=%s — STALE, ignored. TOML stays %s.",
                prov_name, new_fast, old_fast,
            )
            new_fast = None

        if new_fast and new_fast != old_fast:
            try:
                provider_cfg.model = new_fast
            except (AttributeError, TypeError) as exc:
                log.warning(
                    "Could not set %s.model (%s) — TOML default kept.",
                    prov_name, exc,
                )
            else:
                # In-memory mutation is not enough — _phase2_full_brain
                # calls cfg.load_config() fresh from disk each time. We
                # must also persist to TOML, otherwise the switch is lost
                # on the next brain build.
                try:
                    set_brain_provider_model(prov_name, model=new_fast)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Could not persist %s.model to jarvis.toml (%s) — "
                        "switch lives in-memory only until restart.",
                        prov_name, exc,
                    )
                switch = FrontierSwitch(
                    provider=prov_name, tier="fast",
                    old_model=old_fast, new_model=new_fast,
                    switched_at=time.time(),
                )
                switches.append(switch)
                _add_pending(switch)
                if bus is not None:
                    await bus.publish(FrontierModelSwitched(
                        provider=prov_name, tier="fast",
                        old_model=old_fast, new_model=new_fast,
                    ))
                log.info(
                    "Frontier-Switch %s/fast: %s -> %s (TOML persistiert)",
                    prov_name, old_fast, new_fast,
                )

        # Deep tier (deep_model) — not all providers have this
        old_deep = getattr(provider_cfg, "deep_model", "") or ""
        if not old_deep:
            continue
        try:
            new_deep = await resolver.resolve_latest(prov_name, "deep")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Resolver-Crash fuer %s/deep: %s — keep TOML-Default.",
                prov_name, exc,
            )
            new_deep = None

        if new_deep in STALE_MODELS:
            log.warning(
                "Resolver proposed %s/deep=%s — STALE, ignored.",
                prov_name, new_deep,
            )
            new_deep = None

        if new_deep and new_deep != old_deep:
            try:
                provider_cfg.deep_model = new_deep
            except (AttributeError, TypeError) as exc:
                log.warning(
                    "Could not set %s.deep_model (%s) — keeping current value.",
                    prov_name, exc,
                )
            else:
                try:
                    set_brain_provider_model(prov_name, deep_model=new_deep)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Could not persist %s.deep_model (%s).",
                        prov_name, exc,
                    )
                switch = FrontierSwitch(
                    provider=prov_name, tier="deep",
                    old_model=old_deep, new_model=new_deep,
                    switched_at=time.time(),
                )
                switches.append(switch)
                _add_pending(switch)
                if bus is not None:
                    await bus.publish(FrontierModelSwitched(
                        provider=prov_name, tier="deep",
                        old_model=old_deep, new_model=new_deep,
                    ))
                log.info(
                    "Frontier-Switch %s/deep: %s -> %s (TOML persistiert)",
                    prov_name, old_deep, new_deep,
                )

    return switches
