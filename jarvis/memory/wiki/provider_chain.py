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

from jarvis.core.redact import safe_preview
from jarvis.memory.wiki import telemetry

log = logging.getLogger(__name__)


def _exception_summary(exc: Exception) -> str:
    """Return diagnostic class/status metadata without persisting raw SDK text."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    name = type(exc).__name__
    try:
        code = int(status) if status is not None else None
    except (TypeError, ValueError):
        code = None
    return f"{name} HTTP {code}" if code is not None else name

def credential_ready_wiki_providers(
    *,
    available: set[str] | frozenset[str],
    config: Any,
) -> set[str]:
    """Return registered providers that have a portable credential path.

    API providers are checked through the core endpoint resolver, which already
    implements team-proxy credentials plus keyring, environment, ``.env``, and
    the headless local-file fallback. Providers without a core API-key mapping
    remain eligible because they may authenticate through OAuth or be local.
    """
    from jarvis.core.config import (
        PROVIDER_SECRET_CANDIDATES,
        resolve_provider_endpoint,
    )

    ready: set[str] = set()
    for provider in available:
        if provider not in PROVIDER_SECRET_CANDIDATES:
            ready.add(provider)
            continue
        try:
            endpoint = resolve_provider_endpoint(provider, config=config)
        except Exception:  # noqa: BLE001 - one credential probe cannot hide others
            log.debug(
                "wiki provider credential probe failed for %s", provider,
                exc_info=True,
            )
            continue
        if endpoint.credential:
            ready.add(provider)
    return ready


def build_wiki_provider_chain(
    *,
    primary: str,
    model_override: str,
    available: set[str] | frozenset[str],
    credential_ready: set[str] | frozenset[str] | None = None,
) -> list[tuple[str, str | None]]:
    """Ordered, de-duplicated ``(provider, model)`` attempts for a wiki LLM call.

    ``primary`` (``cfg.provider`` or ``brain.primary``) leads when credential
    ready, then every other registered and credential-ready provider follows in
    stable order. There is no provider-name allowlist: a newly registered Brain
    provider automatically becomes a wiki fallback. An explicit
    ``model_override`` applies only to ``primary``; each fallback resolves its
    own cheap router-tier model or lets its plugin choose a default.
    """
    from jarvis.memory.wiki.curator_llm import _cheap_model_for

    chain: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    override = (model_override or "").strip()
    eligible = set(available)
    if credential_ready is not None:
        eligible.intersection_update(credential_ready)
    for name in (primary, *sorted(eligible)):
        if not name or name in seen or name not in eligible:
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
    validate: Callable[[Any], str | None] | None = None,
    allow_last_rejection: Callable[[str], bool] | None = None,
) -> tuple[Any, str] | None:
    """Try each ``(provider, model)`` until one returns an aggregated response.

    Returns ``(agg, provider_name)`` on the first usable success, or ``None``
    when the whole chain fails. ``validate`` can reject a transport-successful
    response with a short reason; the next provider is then tried before the
    caller gives up on malformed or truncated structured output.
    """
    from jarvis.memory.wiki.curator_llm import instantiate_curator_brain

    # Per-provider failure reasons, collected as we go — feeds both the
    # final log line's diagnostic detail and the health-surface chain-failure
    # record (spec A5) so "openai 401; gemini 429" is visible, not just a
    # generic "ALL N failed" count.
    failure_summaries: list[str] = []
    allowed_rejection_fallback: tuple[Any, str, str] | None = None

    for index, (provider, model) in enumerate(chain):
        try:
            brain = instantiate_curator_brain(registry, provider, model)
        except Exception as exc:  # noqa: BLE001 — a bad provider must not abort the chain
            detail = _exception_summary(exc)
            log.warning(
                "%s: could not instantiate %s (%s) — trying next provider",
                label,
                provider,
                detail,
            )
            failure_summaries.append(f"{provider} instantiate failed: {detail}")
            continue
        if brain is None:
            failure_summaries.append(f"{provider} unavailable")
            continue
        try:
            agg = await asyncio.wait_for(aggregate(brain.complete(request)), timeout=timeout_s)
            if validate is not None:
                try:
                    rejection = validate(agg)
                except Exception as exc:  # noqa: BLE001 - validator faults mean unusable output
                    rejection = (
                        "response validation failed: "
                        f"{_exception_summary(exc)}"
                    )
                if rejection:
                    safe_rejection = safe_preview(rejection, max_chars=240)
                    allowed = (
                        allow_last_rejection is not None
                        and allow_last_rejection(rejection)
                    )
                    if allowed:
                        # Keep a semantically safe fallback (currently the
                        # extractor's valid empty array) while still asking the
                        # remaining providers for one *valid structured*
                        # second opinion. Transport/malformed failures do not
                        # count, but a second allowed answer is agreement and
                        # ends the chain immediately.
                        if allowed_rejection_fallback is not None:
                            log.info(
                                "%s: accepting provider %s output after a "
                                "second provider agreed (%s)",
                                label,
                                provider,
                                safe_rejection,
                            )
                            return agg, provider
                        # If every later provider fails, their failure must not
                        # erase this valid bounded answer.
                        allowed_rejection_fallback = (agg, provider, rejection)
                    if index == len(chain) - 1 and allowed:
                        log.info(
                            "%s: accepting final provider %s output after "
                            "bounded second-opinion attempts (%s)",
                            label,
                            provider,
                            safe_rejection,
                        )
                        return agg, provider
                    log.warning(
                        "%s: provider %s returned unusable output (%s) - "
                        "trying next provider",
                        label,
                        provider,
                        safe_rejection,
                    )
                    failure_summaries.append(
                        f"{provider} unusable output: {safe_rejection}"
                    )
                    try:
                        telemetry.inc("wiki_provider_output_rejected")
                    except Exception:  # noqa: BLE001 - telemetry cannot break fallback
                        log.debug(
                            "%s: output-rejection telemetry failed",
                            label,
                            exc_info=True,
                        )
                    continue
            return agg, provider
        except TimeoutError:
            log.warning(
                "%s: provider %s timed out after %.1fs — trying next provider",
                label,
                provider,
                timeout_s,
            )
            failure_summaries.append(f"{provider} timeout ({timeout_s:.1f}s)")
            continue
        except Exception as exc:  # noqa: BLE001 — try the next family, never dead-end on one
            detail = _exception_summary(exc)
            log.warning(
                "%s: provider %s failed (%s) — trying next provider",
                label,
                provider,
                detail,
            )
            failure_summaries.append(f"{provider} {detail}")
            continue

    if allowed_rejection_fallback is not None:
        agg, provider, rejection = allowed_rejection_fallback
        log.info(
            "%s: accepting provider %s output after later second-opinion "
            "attempts failed (%s)",
            label,
            provider,
            safe_preview(rejection, max_chars=240),
        )
        return agg, provider

    log.error(
        "%s: ALL %d wiki provider(s) failed or returned unusable output — "
        "nothing was written. Check provider health and structured-output logs.",
        label,
        len(chain),
    )
    try:
        telemetry.inc("wiki_all_providers_failed")
    except Exception:  # noqa: BLE001 — telemetry must never break the pipeline
        log.debug("%s: telemetry inc failed", label, exc_info=True)
    try:
        from jarvis.memory.wiki.health import health

        health.record_chain_failure(
            safe_preview(
                "; ".join(failure_summaries)
                if failure_summaries
                else f"{label}: empty provider chain",
                max_chars=800,
            )
        )
    except Exception:  # noqa: BLE001 — health recording must never break the pipeline
        log.debug("%s: health record_chain_failure failed", label, exc_info=True)
    return None


__all__ = [
    "build_wiki_provider_chain",
    "complete_with_fallback",
    "credential_ready_wiki_providers",
]
