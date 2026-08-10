"""Provider selection for screenshot-grounded Computer Use planning."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from jarvis.brain.manager import (
    _DEAD_LIST_KINDS,
    _classify_provider_error,
    _is_rate_limit_exc,
)

log = logging.getLogger(__name__)

# Wordings that mean "the provider is momentarily overloaded / had a blip", NOT
# "your account is broken". Kept separate from the manager's classifier because
# only Computer-Use pays the full price for confusing the two: a mission runs one
# vision call PER STEP, so a single 30-second capacity spike used to knock the
# only funded vision brain out for the WHOLE mission (live 2026-08-10 18:39:25 —
# Gemini answered 503 "This model is currently experiencing high demand", CU
# mission-blocked it, fell through to two out-of-credit providers and told the
# user "I can't see the screen right now").
_TRANSIENT_MARKERS: tuple[str, ...] = (
    "service unavailable",
    "unavailable",
    "overloaded",
    "high demand",
    "try again later",
    "temporarily",
    "internal server error",
    "bad gateway",
    "gateway timeout",
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "server disconnected",
)

#: HTTP codes that are capacity/infra, never credentials.
_TRANSIENT_STATUS: frozenset[int] = frozenset({500, 502, 503, 504, 529})

#: How many transient failures one (provider, model) may take before CU stops
#: offering it for the rest of the mission. One blip is forgiven; a provider
#: that is genuinely down falls out instead of costing every step a round-trip.
_MAX_TRANSIENT_PER_CANDIDATE = 2


def is_transient_provider_error(exc: Exception, detail: str | None = None) -> bool:
    """True when *exc* reads as a capacity/infra blip worth one more try.

    Deliberately narrow: an account or credential problem (401/402/403, quota,
    billing) must NEVER land here, or CU would hammer a dead key on every step
    instead of degrading to the honest "check your keys/credit" readback.
    """
    from jarvis.brain.manager import (  # noqa: PLC0415
        _http_status_code,
        _is_account_blocked_exc,
        _is_missing_key_exc,
        _is_rate_limit_exc,
    )

    msg = (detail if detail is not None else str(exc)).lower()
    if _is_rate_limit_exc(exc) or _is_missing_key_exc(msg) or _is_account_blocked_exc(msg):
        return False
    code = _http_status_code(msg)
    if code is not None:
        return code in _TRANSIENT_STATUS
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


@dataclass(frozen=True)
class ProviderAttemptError:
    provider: str
    model: str | None
    kind: str
    detail: str


@dataclass
class ComputerUsePlannerSelector:
    """Select healthy, vision-capable planner brains for Computer Use."""

    manager: Any
    chain: list[tuple[str, str | None]]
    errors: list[ProviderAttemptError] = field(default_factory=list)
    mission_blocked: set[tuple[str, str | None]] = field(default_factory=set)
    blind_skipped: int = 0
    #: Transient (capacity/infra) failures counted per candidate — see
    #: ``_MAX_TRANSIENT_PER_CANDIDATE``.
    transient_hits: dict[tuple[str, str | None], int] = field(default_factory=dict)

    def iter_candidates(
        self, *, images_attached: bool,
    ) -> Iterator[tuple[int, str, str | None, Any]]:
        """Yield provider candidates that are usable for this CU brain call."""
        dead_providers = getattr(self.manager, "_dead_providers", set())
        rate_tracker = getattr(self.manager, "_rate_tracker", None)
        is_available = getattr(rate_tracker, "is_available", None)

        for idx, (provider, model) in enumerate(self.chain):
            key = (provider, model)
            if provider in dead_providers:
                self._record(provider, model, "dead", "skipped dead provider")
                continue
            if key in self.mission_blocked:
                self._record(
                    provider,
                    model,
                    "mission_blocked",
                    "skipped after failure earlier in this mission",
                )
                continue
            if callable(is_available) and not is_available(provider, model):
                self._record(provider, model, "cooldown", "skipped cooldown")
                continue

            try:
                brain = self.manager._get_brain(provider, model)
                if brain is None:
                    raise RuntimeError(
                        f"BrainManager._get_brain({provider!r}, {model!r}) returned None"
                    )
            except Exception as exc:  # noqa: BLE001
                self.record_failure(provider, model, exc)
                log.warning(
                    "ComputerUseLoop brain provider %s(%s) failed: %s",
                    provider, model, exc,
                )
                continue

            if images_attached and not getattr(brain, "supports_vision", True):
                self.blind_skipped += 1
                self._record(
                    provider,
                    model,
                    "blind",
                    "skipped - cannot see the screen (supports_vision=False)",
                )
                log.info(
                    "[cu] skipped screenshot-blind provider %s(%s) - "
                    "supports_vision=False; falling through to a "
                    "vision-capable brain",
                    provider, model,
                )
                continue

            yield idx, provider, model, brain

    def record_empty(self, provider: str, model: str | None) -> None:
        self._record(provider, model, "empty", "empty response")

    def record_failure(
        self, provider: str, model: str | None, exc: Exception,
    ) -> None:
        detail = str(exc)
        kind = self._classify_failure(exc, detail)
        self._record(provider, model, kind, detail[:200])

        if kind == "rate_limit":
            rate_tracker = getattr(self.manager, "_rate_tracker", None)
            mark_rate_limited = getattr(rate_tracker, "mark_rate_limited", None)
            if callable(mark_rate_limited):
                mark_rate_limited(provider, model)
            return

        # Dead-list a terminal-credential failure so it stops leading the chain.
        # Use the SAME shared set the manager uses (_DEAD_LIST_KINDS) instead of a
        # hardcoded subset — the manager added "bad_key" (a bare HTTP 401, e.g.
        # "Error code: 401 - invalid x-api-key") on 2026-06-30, and a local subset
        # silently dropped it here, so a 401 CU provider was re-tried every step
        # instead of being skipped (drift bug: _looks_invalid_auth_error became
        # dead code for any 401 that carried the numeric code).
        if kind in _DEAD_LIST_KINDS:
            dead_providers = getattr(self.manager, "_dead_providers", None)
            if isinstance(dead_providers, set):
                dead_providers.add(provider)
            return

        if kind == "overloaded":
            # A capacity blip is not a broken provider. Blocking it for the rest
            # of the mission is what turned a 30-second Gemini spike into "CU has
            # no eyes at all" (live 2026-08-10). Forgive the first hit; a second
            # one means the outage is real, so the candidate falls out normally.
            key = (provider, model)
            hits = self.transient_hits.get(key, 0) + 1
            self.transient_hits[key] = hits
            if hits < _MAX_TRANSIENT_PER_CANDIDATE:
                log.info(
                    "[cu] %s(%s) had a transient failure (%d/%d) — kept in the "
                    "chain for the next step",
                    provider, model, hits, _MAX_TRANSIENT_PER_CANDIDATE,
                )
                return

        self.mission_blocked.add((provider, model))

    def error_message(self, *, images_attached: bool, attempted: int) -> str:
        tail = "; ".join(self._format_error(err) for err in self.errors[-4:])
        if not tail:
            tail = "no usable providers"
        if images_attached and self.blind_skipped and attempted == 0:
            return (
                "Computer-Use needs a vision-capable brain, but the active "
                "provider and its fallbacks cannot see the screen "
                "(supports_vision=False): "
                + tail
            )
        summary = (
            f"{self.blind_skipped} provider(s) skipped - no vision; "
            if self.blind_skipped else ""
        )
        return "ComputerUseLoop provider chain failed: " + summary + tail

    def _record(
        self, provider: str, model: str | None, kind: str, detail: str,
    ) -> None:
        self.errors.append(ProviderAttemptError(provider, model, kind, detail))

    @staticmethod
    def _format_error(err: ProviderAttemptError) -> str:
        return f"{err.provider}({err.model}): {err.detail}"

    @staticmethod
    def _classify_failure(exc: Exception, detail: str) -> str:
        if _is_rate_limit_exc(exc):
            return "rate_limit"
        kind = _classify_provider_error(detail, default="call_fail")
        if kind == "call_fail" and _looks_invalid_auth_error(detail):
            return "missing_key"
        if kind == "call_fail" and is_transient_provider_error(exc, detail):
            return "overloaded"
        return kind


def iter_last_resort_vision(
    manager: Any, *, already_tried: set[tuple[str, str | None]],
) -> Iterator[tuple[str, str | None, Any]]:
    """Yield ``(provider, model, brain)`` for every REGISTERED vision-capable
    provider, IGNORING the manager's transient ``_dead_providers`` / cooldown
    flags.

    Used only as a LAST RESORT when the normal ``_build_fallback_chain`` reached
    no vision-capable brain — typically because a stale dead-flag filtered the
    one vision provider (e.g. grok) out of the chain entirely (live 2026-06-21
    18:41: CU gave up "no vision" while grok had a live key). A stale dead-flag
    on Computer-Use's only eyes must not permanently disable it. Genuinely
    keyless/broken providers simply raise when dispatched and are skipped by the
    caller, so this never resurrects a truly-dead provider — it only gives the
    one that may be wrongly flagged a real attempt.

    Each ``(provider, model)`` already tried in the normal pass is skipped, so a
    provider that really failed this turn is not retried. Provider-agnostic: the
    only gate is ``supports_vision``; no provider name is special-cased.
    """
    registry = getattr(manager, "_registry", None)
    available: list[str] = []
    if registry is not None and hasattr(registry, "available"):
        try:
            available = list(registry.available())
        except Exception:  # noqa: BLE001
            available = []
    seen = set(already_tried)
    for provider in available:
        picker = getattr(manager, "_fast_model", None)
        model: str | None = None
        if callable(picker):
            try:
                model = picker(provider)
            except Exception:  # noqa: BLE001
                model = None
        key = (provider, model)
        if key in seen:
            continue
        seen.add(key)
        # Last-resort: a provider that cannot even be instantiated is simply
        # skipped (expected on the failure path; logging each would be noise).
        try:
            brain = manager._get_brain(provider, model)
        except Exception:  # noqa: BLE001, S112
            continue
        if brain is None:
            continue
        if not getattr(brain, "supports_vision", True):
            continue
        yield provider, model, brain


def _looks_invalid_auth_error(detail: str) -> bool:
    msg = detail.lower()
    if "invalid x-api-key" in msg or "invalid api key" in msg:
        return True
    if "authentication_error" in msg or "unauthorized" in msg:
        return True
    if "401" in msg and any(
        token in msg
        for token in (
            "api-key",
            "api key",
            "x-api-key",
            "invalid",
            "authentication",
            "unauthorized",
        )
    ):
        return True
    return False
