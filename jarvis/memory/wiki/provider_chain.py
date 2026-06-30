"""Key-aware provider fallback for wiki-tier LLM calls (extractor + curator).

Why this exists
---------------
The main ``BrainManager`` survives a dead / throttled / keyless provider by
looping over a key-aware fallback CHAIN (``manager._build_fallback_chain``): it
tries the next provider on any failure and only gives up when none is reachable.
The wiki extractor (stage 1) and curator (stage 2) did NOT — each resolved to a
SINGLE provider (``cfg.provider`` or ``brain.primary``) and, on any error,
returned an empty list with only a WARNING.

Live forensic 2026-06-30: the user's openrouter key was over its total limit
(403), gemini was out of prepaid credit (429) AND claude-api auth was rejected
(401) at various moments. Whenever the wiki's single resolved provider was the
one erroring, the whole pipeline silently no-op'd — nothing journaled, nothing
written — even though the main voice brain limped along via its own fallback
chain and a working provider existed. The wiki looked dead while the user had
credit. This module gives the wiki the SAME resilience, plus an HONEST signal
when the whole chain is exhausted (AP-22 single-provider brick / AP-23
maintainer-config coupling).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from jarvis.memory.wiki import telemetry

log = logging.getLogger(__name__)

# Cross-family fallback order, mirroring manager._build_fallback_chain step 3: a
# separate-quota family first (claude-api), then the gateways. Each is tried
# ONLY if registered (registry.available()); a keyless / throttled one fails
# fast and the loop crosses to the next FAMILY — never a same-family dead-end.
_CROSS_FAMILY_ORDER: tuple[str, ...] = ("claude-api", "gemini", "openrouter", "openai")


def build_wiki_provider_chain(
    *,
    primary: str,
    model_override: str,
    available: set[str] | frozenset[str],
) -> list[tuple[str, str | None]]:
    """Ordered, de-duplicated ``(provider, model)`` attempts for a wiki LLM call.

    ``primary`` (``cfg.provider`` or ``brain.primary``) leads, then the
    cross-family order. Only providers in ``available`` survive. An explicit
    ``model_override`` (``cfg.model``) applies ONLY to ``primary`` — a fallback
    provider gets its OWN cheap router-tier model, never the primary's model id
    (sending a gemini model id to claude-api would 404).
    """
    from jarvis.memory.wiki.curator_llm import _cheap_model_for

    chain: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    override = (model_override or "").strip()
    for name in (primary, *_CROSS_FAMILY_ORDER):
        if not name or name in seen or name not in available:
            continue
        seen.add(name)
        model = override if (name == primary and override) else _cheap_model_for(name)
        chain.append((name, model or None))
    return chain


async def complete_with_fallback(
    *,
    registry: Any,
    chain: list[tuple[str, str | None]],
    request: Any,
    timeout_s: float,
    label: str,
    aggregate: Callable[[Any], Any],
) -> tuple[Any, str] | None:
    """Try each ``(provider, model)`` until one returns an aggregated response.

    Returns ``(agg, provider_name)`` on the first success, or ``None`` when the
    WHOLE chain failed. A single provider failure is a WARNING (visible, not
    fatal); an exhausted chain is an ERROR plus a ``wiki_all_providers_failed``
    telemetry bump — the HONEST signal that the wiki could reach NO provider,
    instead of the old silent empty return.
    """
    from jarvis.memory.wiki.curator_llm import instantiate_curator_brain

    for provider, model in chain:
        try:
            brain = instantiate_curator_brain(registry, provider, model)
        except Exception as exc:  # noqa: BLE001 — a bad provider must not abort the chain
            log.warning("%s: could not instantiate %s (%s) — trying next provider", label, provider, exc)
            continue
        if brain is None:
            continue
        try:
            agg = await asyncio.wait_for(aggregate(brain.complete(request)), timeout=timeout_s)
            return agg, provider
        except TimeoutError:
            log.warning("%s: provider %s timed out after %.1fs — trying next provider", label, provider, timeout_s)
            continue
        except Exception as exc:  # noqa: BLE001 — try the next family, never dead-end on one
            log.warning("%s: provider %s failed (%s) — trying next provider", label, provider, exc)
            continue

    log.error(
        "%s: ALL %d wiki provider(s) failed — no LLM reachable this round; nothing "
        "was written. Check API keys / quota in the API-Keys section.",
        label,
        len(chain),
    )
    try:
        telemetry.inc("wiki_all_providers_failed")
    except Exception:  # noqa: BLE001 — telemetry must never break the pipeline
        pass
    return None


__all__ = ["build_wiki_provider_chain", "complete_with_fallback"]
