"""Provider-agnostic vision-brain dispatch for Computer-Use v2.

A lean re-implementation of the legacy engine's battle-tested dispatch
behavior, without importing the monolith:

* FakeBrain test shim (``manager.complete_text``) first.
* Candidate order = ``BrainManager._build_fallback_chain("fast")`` with the
  per-provider CU model override — never a provider name hardcode (AP-21/22).
* Health/vision filtering via :class:`ComputerUsePlannerSelector` (skips
  dead/cooldown/blind providers) plus the stale-dead-flag last resort.
* The prompt is built PER CANDIDATE via a callback, because the coordinate
  convention (0-1000 grid vs image pixels) depends on which provider the
  call actually lands on — a mid-mission cross-family fallback must not be
  parsed in the wrong space.

Raises :class:`CUNoVisionProviderError` when the whole chain is exhausted —
the engine maps it to exit 3 ("check your keys/credit"), keeping the honest
credential readback the legacy engine had.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jarvis.core.protocols import BrainMessage, BrainRequest, ImageBlock

logger = logging.getLogger(__name__)


class CUBrainCallError(RuntimeError):
    """The model call failed structurally (no provider produced text)."""


class CUNoVisionProviderError(CUBrainCallError):
    """Every candidate was keyless/dead/blind — an account fact, exit 3."""


@dataclass(frozen=True)
class BrainReply:
    """One successful model reply plus its provenance."""

    text: str
    provider: str
    model: str | None


#: Builds (system_prompt, user_message) for the provider the call lands on.
PromptBuilder = Callable[[str, Any], tuple[str, str]]

#: Last (provider, model) pair whose vision calls were logged as serving.
#: Change-triggered: one INFO line per identity switch instead of one per
#: step, so the log names the brain that ACTUALLY steps without flooding.
_serving_logged: tuple[str, str | None] | None = None


def _log_serving(provider: str, model: str | None) -> None:
    """Log the serving brain identity once per change (the ground truth)."""
    global _serving_logged
    if (provider, model) != _serving_logged:
        logger.info(
            "[cu] vision calls served by %s(%s)",
            provider, model or "provider default",
        )
        _serving_logged = (provider, model)


def _explicit_cu_pin(manager: Any, provider: str) -> str | None:
    """The user's RAW Tool Model pin for ``provider``, or ``None``.

    Reads the CANONICAL ``tool_model`` field first, then the legacy
    ``cu_model`` — both are the user's explicit word and must never be
    second-guessed by the speed tune. Reading only ``cu_model`` here was a
    live defect (2026-07-16): the Tool Model tab persists ``tool_model``
    only, so after a restart the selection stopped counting as a pin and
    the speed tune silently swapped the user's chosen model for the fast
    vision sibling — the selection then applied to realtime delegation but
    not to Computer-Use.

    Deliberately NOT ``manager._cu_model`` — that resolver falls back to the
    main model / tier default and therefore never returns ``None``, which
    cannot distinguish "the user pinned this for CU" from "nothing pinned".
    """
    try:
        provider_cfg = getattr(manager, "_provider_cfg", None)
        cfg = provider_cfg(provider) if callable(provider_cfg) else None
        pin = getattr(cfg, "tool_model", None) or getattr(cfg, "cu_model", None)
        return str(pin) if pin else None
    except Exception:  # noqa: BLE001
        return None


def _speed_tune_chain(
    chain: list[tuple[str, str | None]],
    *,
    pinned: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[tuple[str, str | None]]:
    """Per candidate: keep an explicit CU pin verbatim; otherwise put the
    provider's FAST vision-capable model on the mission steps.

    Two problems, one move (both live-measured 2026-07-02):

    * A KNOWN-blind configured model (OpenRouter DeepSeek pin) knocked the
      whole provider out of the chain although the same key unlocks vision
      models (AP-22) — it must be swapped regardless.
    * A flagship configured model made every step THINK for seconds
      (think=60.8s of a 75.8s mission on Opus over OpenRouter). Computer-Use
      issues one small vision call per step — the fast class (flash/haiku/
      mini) answers in a fraction of the time at no relevant quality loss
      for "click/type/verify" decisions.

    Providers whose catalog exposes no modality data (direct Gemini/Claude
    endpoints) return no pick and stay untouched — their configured fast-tier
    model already IS the fast class. Never raises; any lookup problem keeps
    the original pair.
    """
    try:
        from jarvis.brain.model_catalog import (  # noqa: PLC0415
            is_fast_class_model,
            model_capabilities,
            pick_fast_vision_model,
            provider_has_modality_data,
        )
    except Exception:  # noqa: BLE001
        return chain
    tuned: list[tuple[str, str | None]] = []
    for provider, model in chain:
        if provider in pinned:
            tuned.append((provider, model))
            continue
        try:
            caps = model_capabilities(provider, model) if model else {}
            blind = caps.get("vision") is False
            if not blind and is_fast_class_model(model):
                # The configured model is already the fast class (and not
                # known-blind) — keep the user's choice untouched.
                tuned.append((provider, model))
                continue
            alt = pick_fast_vision_model(provider)
            if alt is None and not provider_has_modality_data(provider):
                # Direct provider endpoints expose no modality metadata, so
                # the empty catalog pick means "no data", not "no vision
                # model" — fall back to the provider's curated router-tier
                # (fast) default. Keeps "steps run on the fast class" true
                # for EVERY key, not only catalog-backed gateways. When the
                # catalog HAS data and found nothing, there is genuinely no
                # vision model to offer — no blind guessing.
                try:
                    from jarvis.brain.manager import (  # noqa: PLC0415
                        get_tier_default_model,
                    )

                    candidate = get_tier_default_model("router", provider)
                    if candidate and model_capabilities(
                        provider, candidate,
                    ).get("vision") is not False:
                        alt = candidate
                except Exception:  # noqa: BLE001
                    alt = None
            if alt and alt != model and (blind or model):
                # DEBUG, not INFO: this fires for EVERY unpinned chain
                # CANDIDATE on every step — including providers that never
                # serve a single call. Logged at INFO it reads as "this model
                # is stepping" (live forensic 2026-07-15: the nvidia
                # candidate line was mistaken for the serving brain and
                # produced a false "text-only model drove Computer-Use"
                # diagnosis). The change-triggered "served by" log below is
                # the ground truth for what actually steps.
                if blind:
                    logger.debug(
                        "[cu] chain candidate %s: model %s cannot see the "
                        "screen — candidate swapped to the vision-capable %s",
                        provider, model, alt,
                    )
                else:
                    logger.debug(
                        "[cu] chain candidate %s: %s swapped for the fast "
                        "vision sibling %s (pin a Tool Model in Settings to "
                        "override)",
                        provider, model, alt,
                    )
                tuned.append((provider, alt))
                continue
            if blind:
                # Known-blind and no vision sibling — the selector's
                # blind-skip will drop it; nothing better to offer.
                logger.info(
                    "[cu] %s model %s cannot see the screen and no vision "
                    "sibling is known", provider, model,
                )
        except Exception:  # noqa: BLE001 — tuning is best-effort
            logger.debug("[cu] speed-tune failed for %s", provider, exc_info=True)
        tuned.append((provider, model))
    return tuned


async def call_vision_brain(
    manager: Any,
    *,
    build_prompt: PromptBuilder,
    images: list[ImageBlock],
    max_tokens: int = 256,
    early_stop_json: bool = False,
) -> BrainReply:
    """Dispatch one screenshot-grounded call through the provider chain."""
    # FakeBrain test shim: single-call managers used across the test suite.
    complete_text = getattr(manager, "complete_text", None)
    if complete_text is not None:
        system, user = build_prompt("fake", manager)
        result = await complete_text(system=system, user=user)
        return BrainReply(text=str(result), provider="fake", model=None)

    if not hasattr(manager, "_get_brain"):
        raise CUBrainCallError(
            "BrainManager exposes neither complete_text nor _get_brain — "
            "CU v2 cannot dispatch",
        )

    from jarvis.brain.streaming import (  # noqa: PLC0415
        aggregate,
        aggregate_first_json,
        has_complete_json_action,
        is_length_truncated,
    )
    from jarvis.harness.computer_use_planner import (  # noqa: PLC0415
        ComputerUsePlannerSelector,
        iter_last_resort_vision,
    )

    agg = aggregate_first_json if early_stop_json else aggregate

    chain: list[tuple[str, str | None]] = []
    build_chain = getattr(manager, "_build_fallback_chain", None)
    if callable(build_chain):
        try:
            chain = list(build_chain("fast") or [])
        except Exception:  # noqa: BLE001
            logger.debug("[cu] fallback-chain build failed", exc_info=True)
    if not chain and getattr(manager, "active_provider", None):
        chain = [(manager.active_provider, None)]
    if not chain:
        raise CUBrainCallError("BrainManager fallback chain is empty")

    # Dedicated GLOBAL Computer-Use provider (decoupled from brain.primary).
    # When the user has picked one ([brain.computer_use].provider), hoist it
    # to the HEAD of the chain so it is tried first; everything downstream —
    # the cu_model pin loop below, the speed tune, and the vision/health gate
    # in ComputerUsePlannerSelector — still applies to it, so a blind/dead
    # pick degrades through the rest of the chain instead of bricking CU
    # (AP-21/22). CU-ONLY: never touches ``_build_fallback_chain`` itself,
    # whose "fast" level is shared with normal voice/chat turns.
    cu_provider_fn = getattr(manager, "_cu_provider", None)
    if callable(cu_provider_fn):
        try:
            cu_provider = cu_provider_fn()
        except Exception:  # noqa: BLE001 — resolver must never break dispatch
            cu_provider = ""
        if cu_provider:
            chain = [(p, m) for p, m in chain if p != cu_provider]
            chain.insert(0, (cu_provider, None))

    # Per-provider CU model override (Settings can pin a dedicated CU model).
    # A PIN is the user's explicit word and is never second-guessed; every
    # unpinned candidate is speed-tuned below. CRITICAL: pin detection reads
    # the RAW config field — ``manager._cu_model`` resolves cu_model -> main
    # model -> tier default and therefore ALWAYS returns something, which made
    # every provider look pinned and silently disabled the speed tune (live
    # forensic 2026-07-02 15:53: think=53.6s AFTER the tune shipped).
    cu_model = getattr(manager, "_cu_model", None)
    pinned_providers: set[str] = set()
    if callable(cu_model):
        resolved_chain: list[tuple[str, str | None]] = []
        for provider, model in chain:
            pin = _explicit_cu_pin(manager, provider)
            if pin:
                pinned_providers.add(provider)
                resolved_chain.append((provider, pin))
                continue
            try:
                resolved_chain.append((provider, cu_model(provider) or model))
            except Exception:  # noqa: BLE001
                resolved_chain.append((provider, model))
        chain = resolved_chain

    images_attached = bool(images)
    if images_attached:
        chain = _speed_tune_chain(chain, pinned=pinned_providers)
    selector = ComputerUsePlannerSelector(manager=manager, chain=chain)
    attempted = 0

    async def _try(provider: str, model: str | None, brain: Any) -> BrainReply | None:
        system, user = build_prompt(provider, brain)

        async def _once(tokens: int) -> Any:
            req = BrainRequest(
                messages=(BrainMessage(
                    role="user", content=user, images=tuple(images),
                ),),
                system=system,
                temperature=0.0,
                max_tokens=tokens,
                stream=True,
                # CU calls are small deterministic JSON decisions — internal
                # "thinking" only burns the output budget (2026-07-16: every
                # step died "unterminated JSON", thoughts=304 of 320).
                reasoning_effort="none",
            )
            return await agg(brain.complete(req))

        result = await _once(max_tokens)
        text = (result.text or "").strip()
        if (
            early_stop_json
            and text
            and not has_complete_json_action(text)
            and is_length_truncated(result.finish_reason, text)
        ):
            # The model hit the output-token cap before finishing its JSON —
            # typically a thinking-by-default model whose thoughts consumed
            # the budget despite the reasoning hint (or a provider without a
            # thinking knob at all). ONE retry with real headroom; the
            # early-stop aggregator still cuts the stream at the JSON
            # boundary, so the extra ceiling costs nothing on success.
            retry_tokens = max(2048, max_tokens * 4)
            logger.info(
                "[cu] %s(%s) reply hit the %d-token cap before completing "
                "its JSON — retrying once with %d",
                provider, model, max_tokens, retry_tokens,
            )
            result = await _once(retry_tokens)
            text = (result.text or "").strip()
        if not text:
            selector.record_empty(provider, model)
            return None
        return BrainReply(text=text, provider=provider, model=model)

    for idx, provider, model, brain in selector.iter_candidates(
        images_attached=images_attached,
    ):
        attempted += 1
        try:
            reply = await _try(provider, model, brain)
        except Exception as exc:  # noqa: BLE001
            selector.record_failure(provider, model, exc)
            logger.warning("[cu] brain provider %s(%s) failed: %s", provider, model, exc)
            continue
        if reply is not None:
            if idx > 0:
                logger.info(
                    "[cu] fallback hit: %s(%s) after %d skipped provider(s)",
                    provider, model, idx,
                )
            _log_serving(provider, model)
            return reply

    # Stale-dead-flag resilience: retry every REGISTERED vision provider once.
    if images_attached:
        already = set(chain) | {(e.provider, e.model) for e in selector.errors}
        for provider, model, brain in iter_last_resort_vision(
            manager, already_tried=already,
        ):
            try:
                reply = await _try(provider, model, brain)
            except Exception as exc:  # noqa: BLE001
                selector.record_failure(provider, model, exc)
                continue
            if reply is not None:
                logger.warning(
                    "[cu] LAST-RESORT vision brain %s(%s) reached — normal "
                    "chain had no vision provider (stale dead-flag?)",
                    provider, model,
                )
                _log_serving(provider, model)
                return reply

    raise CUNoVisionProviderError(
        selector.error_message(images_attached=images_attached, attempted=attempted),
    )
