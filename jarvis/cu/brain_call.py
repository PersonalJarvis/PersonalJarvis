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


def _rescue_blind_models(
    chain: list[tuple[str, str | None]],
) -> list[tuple[str, str | None]]:
    """Swap a KNOWN-blind configured model for the provider's best
    vision-capable sibling, per candidate.

    Computer-Use is screenshot-grounded; a text-only model choice (e.g. an
    OpenRouter DeepSeek pin) previously knocked the WHOLE provider out of the
    chain even though the user's key unlocks plenty of vision models — a
    "works with whatever key you have" violation (AP-22). Only an explicit
    ``vision is False`` verdict from the model catalog triggers the swap;
    unknown capability stays untouched (no regression for providers without
    modality metadata). Never raises — any lookup problem keeps the original
    pair.
    """
    try:
        from jarvis.brain.model_catalog import (  # noqa: PLC0415
            model_capabilities,
            pick_vision_model,
        )
    except Exception:  # noqa: BLE001
        return chain
    rescued: list[tuple[str, str | None]] = []
    for provider, model in chain:
        try:
            if model and model_capabilities(provider, model).get("vision") is False:
                alt = pick_vision_model(provider)
                if alt and alt != model:
                    logger.info(
                        "[cu] %s model %s cannot see the screen — using the "
                        "vision-capable %s for this mission",
                        provider, model, alt,
                    )
                    rescued.append((provider, alt))
                    continue
        except Exception:  # noqa: BLE001 — rescue is best-effort
            logger.debug("[cu] vision-model rescue failed for %s", provider,
                         exc_info=True)
        rescued.append((provider, model))
    return rescued


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

    from jarvis.brain.streaming import aggregate, aggregate_first_json  # noqa: PLC0415
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

    # Per-provider CU model override (Settings can pin a dedicated CU model).
    cu_model = getattr(manager, "_cu_model", None)
    if callable(cu_model):
        resolved_chain: list[tuple[str, str | None]] = []
        for provider, model in chain:
            try:
                resolved_chain.append((provider, cu_model(provider) or model))
            except Exception:  # noqa: BLE001
                resolved_chain.append((provider, model))
        chain = resolved_chain

    images_attached = bool(images)
    if images_attached:
        chain = _rescue_blind_models(chain)
    selector = ComputerUsePlannerSelector(manager=manager, chain=chain)
    attempted = 0

    async def _try(provider: str, model: str | None, brain: Any) -> BrainReply | None:
        system, user = build_prompt(provider, brain)
        req = BrainRequest(
            messages=(BrainMessage(
                role="user", content=user, images=tuple(images),
            ),),
            system=system,
            temperature=0.0,
            max_tokens=max_tokens,
            stream=True,
        )
        result = await agg(brain.complete(req))
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
                return reply

    raise CUNoVisionProviderError(
        selector.error_message(images_attached=images_attached, attempted=attempted),
    )
