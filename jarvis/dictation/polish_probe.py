"""Does this wording provider actually work — here, on this host, right now?

The provider cards answer "ready" as soon as a key STRING is stored. For the
wording pass that claim is weaker than anywhere else in the app, because the
pass is INVISIBLE when it fails: it delivers the raw transcript and says
nothing. An out-of-credits account, a model the local server never pulled, a
family whose answer arrives after the latency budget — all three look exactly
like a healthy provider on the card, and all three mean the user's dictation is
never tidied up. Measured on one real install, four of five cards that read
"ready" could not format a sentence.

So this module asks the provider itself, with the same client the real pass
uses, and returns the shared ``ProviderTestResult`` vocabulary so the card, the
tab dot and the CLI all speak one language.

**Why it lives in the dictation layer.** ``jarvis/brain/provider_test.py`` owns
the per-tier probes for every other card, and this one may not live there: no
module under ``jarvis/brain/`` may name the wording pass, because a call site
there would put a model call one import away from the spoken hot path (AP-11,
guarded by ``tests/unit/dictation/test_polish_boot_safety.py``). The status
vocabulary and the error classifier are imported FROM that module rather than
restated, so the two can never drift (BUG-008 class).

Every import here is deferred to call time — the probe must never appear on a
boot path (AP-26).
"""
from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

log = logging.getLogger(__name__)

#: The one-line sample the probe sends. Deliberately tiny and deterministic: it
#: costs a fraction of a cent, and what is measured is whether the family
#: answers within the user's budget — not how well it writes. Lower-cased and
#: unpunctuated so a working model has something to do.
PROBE_SYSTEM = (
    "Rewrite the user's text with correct capitalization and punctuation. "
    "Change no words. Answer with the rewritten text only."
)
PROBE_USER = "we can ship it on tuesday"

#: Default budget when the config carries none, mirroring ``DictationConfig``.
_DEFAULT_BUDGET_MS = 1200.0

#: How far above the user's budget the probe still waits. It exists to tell
#: "answers late" apart from "never answers": both fail the budget, but only one
#: of them is worth the sentence "raise the limit or pick a faster provider".
_CEILING_FACTOR = 4.0
_CEILING_FLOOR_S = 8.0


def _default_make_client(family: Any, model: str) -> Any:
    from jarvis.dictation.polish_client import build_polish_client

    return build_polish_client(family, model=model)


def _resolve_probe_model(family: Any, dictation_cfg: Any, model: str) -> str:
    """The model the real pass would use for *family*; its default on trouble."""
    if model:
        return model
    try:
        from jarvis.dictation.polish_client import resolve_model

        return resolve_model(family, dictation_cfg, primary_id=getattr(family, "id", ""))
    except Exception as exc:  # noqa: BLE001 — a resolver hiccup is not a verdict
        log.debug("polish probe model resolution failed (%s); using the default.", exc)
        return str(getattr(family, "default_model", "") or "")


async def probe_polish_family(
    family: Any,
    cfg: Any,
    *,
    make_client: Any | None = None,
    model: str = "",
) -> Any:
    """Run ONE real formatting call through *family* and classify the outcome.

    *cfg* is the whole app config; only ``cfg.dictation`` is read (for the
    latency budget and the model pin). Returns a
    ``jarvis.brain.provider_test.ProviderTestResult``. Never raises: a probe
    that blows up must produce a verdict, not an exception the card cannot
    render.
    """
    from jarvis.brain.provider_test import (
        ERROR,
        NOT_CONFIGURED,
        OK,
        UNREACHABLE,
        ProviderTestResult,
        classify_provider_error,
    )

    provider = ""
    dictation_cfg = getattr(cfg, "dictation", None)
    try:
        from jarvis.ui.web.provider_spec import dictation_spec_id

        provider = dictation_spec_id(str(getattr(family, "id", "")))
    except Exception:  # noqa: BLE001 — the card id is cosmetic here
        provider = str(getattr(family, "id", ""))

    if family is None:
        return ProviderTestResult(
            provider, ERROR, "This card has no provider family behind it."
        )

    budget_ms = float(getattr(dictation_cfg, "polish_timeout_ms", 0) or _DEFAULT_BUDGET_MS)
    probe_model = _resolve_probe_model(family, dictation_cfg, model)

    builder = make_client or _default_make_client
    try:
        client = builder(family, probe_model)
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        return ProviderTestResult(provider, classify_provider_error(detail), str(exc))
    if client is None:
        # ``build_polish_client`` answers None for "no credential" and for "the
        # SDK this transport needs is not installed" — ordinary states on some
        # install, neither of them an integration bug.
        return ProviderTestResult(
            provider,
            NOT_CONFIGURED,
            "No usable credential or transport for this provider.",
        )

    ceiling_s = max(budget_ms / 1000.0 * _CEILING_FACTOR, _CEILING_FLOOR_S)
    started = perf_counter()
    try:
        text = await asyncio.wait_for(
            client.complete(
                PROBE_SYSTEM,
                PROBE_USER,
                # Reasoning-capable providers count hidden work against this
                # ceiling too. 64 tokens can therefore yield HTTP 200 with no
                # visible text; 256 remains small for a one-sentence probe while
                # leaving room for the final answer.
                max_output_tokens=256,
                temperature=0.0,
                timeout_s=ceiling_s,
            ),
            timeout=ceiling_s,
        )
    except TimeoutError:
        # Not silent: the timeout becomes the card's visible verdict, which is
        # the whole point of the probe. A log line would duplicate what the user
        # is about to read on screen.
        elapsed = (perf_counter() - started) * 1000.0
        return ProviderTestResult(
            provider, UNREACHABLE, f"No answer within {ceiling_s:.0f}s.", elapsed
        )
    except Exception as exc:  # noqa: BLE001 — every failure becomes a verdict
        elapsed = (perf_counter() - started) * 1000.0
        detail = f"{type(exc).__name__}: {exc}"
        return ProviderTestResult(
            provider, classify_provider_error(detail), str(exc), elapsed
        )

    elapsed = (perf_counter() - started) * 1000.0
    if not (text or "").strip():
        return ProviderTestResult(provider, ERROR, "Answered with no text.", elapsed)
    if elapsed > budget_ms:
        # Reached, authenticated, answered — and still unusable HERE, which is
        # the only thing the user cares about. The sentence carries the
        # measurement, the budget AND the fix, because choosing between "raise
        # the limit" and "pick a faster provider" is the user's call.
        return ProviderTestResult(
            provider,
            UNREACHABLE,
            (
                f"Answered in {elapsed:.0f} ms — slower than the {budget_ms:.0f} ms "
                "wording limit, so dictation would deliver the raw text. Raise the "
                "limit or pick a faster provider."
            ),
            elapsed,
        )
    return ProviderTestResult(provider, OK, f"Answered in {elapsed:.0f} ms.", elapsed)


__all__ = ["PROBE_SYSTEM", "PROBE_USER", "probe_polish_family"]
