"""The dictation polish pass — a second, generative formatting stage.

What it is
----------
The deterministic cleanup (:mod:`jarvis.dictation.cleanup`) can delete a
hesitation sound and repair the space it left behind. It cannot put a sentence
back together: it cannot punctuate, cannot repair the capital letter a
transcription segment boundary broke every ~8 seconds, cannot resolve "at 2,
actually 3" into "at 3", and cannot turn a spoken enumeration into a list. No
amount of regex closes that gap — a second generative pass does, and the market
reference tool's own published architecture confirms that is how they close it.

The contract, in one sentence
-----------------------------
**This pass can only ever be a no-op; it can never be a loss.**
:attr:`PolishOutcome.text` starts life as the raw transcript and is replaced
only after every deterministic guard in :mod:`jarvis.dictation.polish_guards`
has passed. :func:`polish_transcript` never raises — it catches
``BaseException`` (re-raising only ``CancelledError``, which belongs to whoever
cancelled us) — and every failure path returns the user's own words with a
status that says what happened.

Three things bound it
---------------------
1. **A hard latency ceiling.** One ``asyncio.wait_for`` around the WHOLE
   provider chain, default 1200 ms. On expiry the in-flight call is cancelled
   and the raw text is delivered. A formatter that makes the user wait has
   already failed at its job.
2. **A key-aware, family-crossing chain** (:func:`resolve_polish_chain`,
   AP-22). No key in any family means an empty chain, status ``unavailable``,
   and behaviour byte-identical to before this feature existed — the AP-23
   gate: the maintainer's credentials must never be what makes the default
   safe.
3. **A circuit breaker.** Three consecutive provider errors or timeouts open it
   for 120 s, after which the pass short-circuits to ``unavailable`` without
   dialling out. A dead provider must not add 1.2 s to every dictation.

AP-26: nothing here is imported at module scope by the speech pipeline. The
pipeline imports this module INSIDE ``_finish_dictation``, exactly the way it
already imports ``clean_transcript``, and ``httpx`` / ``google-genai`` are
imported deeper still — inside client construction, on the first dictation,
never at boot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

# The breaker is the SAME async-safe three-state machine the Flash-Brain uses to
# survive a degraded provider. Re-using it rather than copying it means one
# implementation of "when is a provider considered down", with one set of tests.
from jarvis.brain.ack_brain.circuit_breaker import CircuitBreaker
from jarvis.dictation.cleanup import count_words
from jarvis.dictation.polish_client import (
    PolishFamily,
    aclose_shared_client,
    build_polish_client,
    resolve_model,
    resolve_polish_chain,
)
from jarvis.dictation.polish_guards import drift_reason, normalize_for_compare
from jarvis.dictation.polish_prompt import (
    build_polish_prompt,
    build_polish_user_message,
)

log = logging.getLogger(__name__)

#: Every status the pass may report — the SSOT for this vocabulary, mirrored in
#: TypeScript and in the locale files and pinned by a parity test, exactly like
#: ``jarvis.dictation.outcomes.DICTATION_OUTCOMES``.
#:
#: Only ``applied`` means the delivered text differs from what came in. Every
#: other value means the user got their own words back, and says why.
POLISH_STATUSES: Final[tuple[str, ...]] = (
    "applied",        # the polished text was delivered
    "unchanged",      # the model returned text equal to raw after normalization
    "off",            # [dictation].polish = false
    "unavailable",    # no key in any family / client build failed / breaker open
    "skipped_short",  # below polish_min_words
    "skipped_long",   # above polish_max_input_chars
    "timeout",        # exceeded polish_timeout_ms
    "provider_error",  # HTTP / SDK failure after the fallback chain
    "rejected_drift",  # a guard fired -> raw delivered
)

#: Consecutive provider failures that open the breaker, and how long it stays
#: open. Deliberately NOT config keys: this is a safety floor, not a preference,
#: and a user who could set the threshold to 99 would be configuring their own
#: dictation to hang on a dead provider.
POLISH_BREAKER_THRESHOLD: Final[int] = 3
POLISH_BREAKER_COOLDOWN_S: Final[int] = 120

# Defaults for every [dictation].polish_* key. Read through getattr() with these
# fallbacks so this module is correct both before and after the config fields
# land, and on an older jarvis.toml that has never heard of them.
_DEFAULT_TIMEOUT_MS = 1200
_DEFAULT_MAX_INPUT_CHARS = 4000
_DEFAULT_MIN_WORDS = 4
_DEFAULT_MAX_OUTPUT_TOKENS = 1200
_DEFAULT_TEMPERATURE = 0.0
_DEFAULT_MAX_SHRINK = 0.55
_DEFAULT_MAX_GROWTH = 1.20

#: The smallest per-call timeout we will hand a transport. A request given 0 s
#: fails in a way that looks like a provider outage instead of like our deadline.
_MIN_CALL_TIMEOUT_S = 0.05


@dataclass(frozen=True, slots=True)
class PolishOutcome:
    """The result of one polish attempt.

    ``text`` is ALWAYS safe to deliver: it is the raw transcript on every status
    except ``applied``. ``reason`` is machine-readable (a guard code from
    :data:`jarvis.dictation.polish_guards.DRIFT_REASONS`, or a short cause like
    ``no_credential`` / ``circuit_open`` / ``deadline``) and empty when nothing
    went wrong.
    """

    text: str
    status: str
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    reason: str = ""


@dataclass(slots=True)
class _Attempt:
    """Which family was in flight, so a timeout can name who it waited on."""

    provider: str = ""
    model: str = ""
    error: str = ""


_BREAKER: CircuitBreaker | None = None


def _breaker() -> CircuitBreaker:
    """The process-wide breaker, built on first use (never at import)."""
    global _BREAKER
    if _BREAKER is None:
        _BREAKER = CircuitBreaker(
            threshold=POLISH_BREAKER_THRESHOLD,
            cooldown_s=POLISH_BREAKER_COOLDOWN_S,
        )
    return _BREAKER


def reset_polish_state(*, now: Callable[[], float] | None = None) -> None:
    """Rebuild the breaker, optionally on an injected clock.

    Two real callers: a settings change (a user who just fixed their key should
    not wait out a cooldown earned by the broken one), and tests, which need a
    deterministic clock instead of sleeping for two minutes.
    """
    global _BREAKER
    _BREAKER = CircuitBreaker(
        threshold=POLISH_BREAKER_THRESHOLD,
        cooldown_s=POLISH_BREAKER_COOLDOWN_S,
        now=now or time.monotonic,
    )


def polish_enabled(cfg: Any) -> bool:
    """Whether the polish pass is switched on. Cheap: no I/O, no import cost.

    Called on the dictation path before anything heavier happens, so it must
    stay a single attribute read. An absent key reads as OFF — a config that
    predates the feature must not silently acquire it.
    """
    return bool(getattr(cfg, "polish", False))


async def aclose() -> None:
    """Release the pooled HTTP client. Safe to call repeatedly, and on a host
    that never ran a polish call at all."""
    await aclose_shared_client()


async def polish_transcript(
    raw: str,
    *,
    language: str,
    cfg: Any,
    protected_terms: Sequence[str] = (),
    style: str = "neutral",
    timeout_s: float | None = None,
) -> PolishOutcome:
    """Format *raw* without changing what it says. Never raises, never loses text.

    ``language`` is the ALREADY-RESOLVED transcript language (the pipeline
    resolves it before it gets here); it steers the guards and appears in the
    prompt only as a weak, explicitly-unreliable hint. ``protected_terms`` are
    spellings the model may not "correct" — the STT dictionary, the wake word,
    the user's name. ``timeout_s`` overrides the configured ceiling; it exists
    for tests and for the settings-screen dry run.
    """
    started = time.perf_counter()
    source = str(raw or "")

    def _result(
        status: str,
        *,
        provider: str = "",
        model: str = "",
        reason: str = "",
        text: str | None = None,
    ) -> PolishOutcome:
        return PolishOutcome(
            text=source if text is None else text,
            status=status,
            provider=provider,
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            reason=reason,
        )

    try:
        if not polish_enabled(cfg):
            return _result("off")
        if not source.strip():
            return _result("skipped_short", reason="empty_input")

        min_words = _cfg_int(cfg, "polish_min_words", _DEFAULT_MIN_WORDS, lo=0, hi=1000)
        if count_words(source) < min_words:
            return _result("skipped_short", reason="below_min_words")

        max_chars = _cfg_int(
            cfg, "polish_max_input_chars", _DEFAULT_MAX_INPUT_CHARS, lo=0, hi=200_000
        )
        if max_chars and len(source) > max_chars:
            return _result("skipped_long", reason="above_max_chars")

        breaker = _breaker()
        if await breaker.is_open():
            return _result("unavailable", reason="circuit_open")

        chain = resolve_polish_chain(cfg)
        if not chain:
            # The AP-23 path: no text-model credential anywhere on this host.
            # Honest, logged once per dictation at debug, and byte-identical to
            # the behaviour before the polish pass existed.
            log.debug(
                "dictation polish has no credential in any provider family; "
                "delivering the unpolished transcript."
            )
            return _result("unavailable", reason="no_credential")

        budget_s = _timeout_budget(cfg, timeout_s)
        attempt = _Attempt(
            provider=chain[0].id,
            model=resolve_model(chain[0], cfg, primary_id=chain[0].id),
        )
        system = build_polish_prompt(
            language=language, style=style, protected_terms=protected_terms
        )
        user = build_polish_user_message(source)

        try:
            polished = await asyncio.wait_for(
                _run_chain(
                    chain,
                    cfg,
                    system=system,
                    user=user,
                    attempt=attempt,
                    deadline=time.monotonic() + budget_s,
                    max_output_tokens=_cfg_int(
                        cfg,
                        "polish_max_output_tokens",
                        _DEFAULT_MAX_OUTPUT_TOKENS,
                        lo=64,
                        hi=8192,
                    ),
                    temperature=_cfg_float(
                        cfg, "polish_temperature", _DEFAULT_TEMPERATURE, lo=0.0, hi=2.0
                    ),
                ),
                budget_s,
            )
        except TimeoutError:
            # asyncio.wait_for has already cancelled the in-flight call.
            await breaker.record_failure()
            log.info(
                "dictation polish exceeded its %d ms ceiling on %r; delivering "
                "the unpolished transcript.",
                int(budget_s * 1000),
                attempt.provider,
            )
            return _result(
                "timeout",
                provider=attempt.provider,
                model=attempt.model,
                reason="deadline",
            )

        if polished is None:
            await breaker.record_failure()
            return _result(
                "provider_error",
                provider=attempt.provider,
                model=attempt.model,
                reason=attempt.error or "no_provider",
            )

        await breaker.record_success()

        if normalize_for_compare(polished) == normalize_for_compare(source):
            # The model agreed there was nothing to do. Deliver the RAW string,
            # not its normalized twin — "unchanged" has to mean unchanged.
            return _result(
                "unchanged", provider=attempt.provider, model=attempt.model
            )

        reason = drift_reason(
            source,
            polished,
            language=language,
            protected=protected_terms,
            max_shrink=_cfg_float(
                cfg, "polish_drift_max_shrink", _DEFAULT_MAX_SHRINK, lo=0.0, hi=1.0
            ),
            max_growth=_cfg_float(
                cfg, "polish_drift_max_growth", _DEFAULT_MAX_GROWTH, lo=1.0, hi=5.0
            ),
        )
        if reason:
            log.info(
                "dictation polish rejected by the %s guard on %r; delivering the "
                "unpolished transcript.",
                reason,
                attempt.provider,
            )
            return _result(
                "rejected_drift",
                provider=attempt.provider,
                model=attempt.model,
                reason=reason,
            )

        return _result(
            "applied",
            provider=attempt.provider,
            model=attempt.model,
            text=polished,
        )
    except asyncio.CancelledError:
        # Cancellation belongs to whoever cancelled us — swallowing it here
        # would leave a task that refuses to stop.
        raise
    except BaseException:  # noqa: BLE001 — the text must survive ANY failure
        log.warning(
            "dictation polish failed unexpectedly; delivering the unpolished "
            "transcript.",
            exc_info=True,
        )
        return _result("provider_error", reason="unexpected")


async def _run_chain(
    chain: Sequence[PolishFamily],
    cfg: Any,
    *,
    system: str,
    user: str,
    attempt: _Attempt,
    deadline: float,
    max_output_tokens: int,
    temperature: float,
) -> str | None:
    """Walk the family chain until one answers; ``None`` when none does.

    Each call gets the REMAINING budget rather than the full one, so a first
    family that burns 900 ms cannot let the second family push the pass past the
    ceiling. The caller's ``wait_for`` is still the hard stop — this is the
    transport-level courtesy that stops a dead socket being held open after we
    have stopped caring about it.
    """
    primary_id = chain[0].id
    for family in chain:
        model = resolve_model(family, cfg, primary_id=primary_id)
        attempt.provider = family.id
        attempt.model = model

        client = build_polish_client(family, model=model)
        if client is None:
            attempt.error = "no_client"
            continue

        remaining = max(_MIN_CALL_TIMEOUT_S, deadline - time.monotonic())
        try:
            text = await client.complete(
                system,
                user,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                timeout_s=remaining,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — cross to the next FAMILY (AP-22)
            attempt.error = "provider_error"
            log.warning(
                "dictation polish provider %r failed (%s); crossing to the next "
                "family.",
                family.id,
                exc,
            )
            continue

        if text and text.strip():
            return text
        attempt.error = "empty_response"
    return None


def _timeout_budget(cfg: Any, override_s: float | None) -> float:
    """The hard ceiling for one polish pass, in seconds."""
    if override_s is not None:
        try:
            return max(_MIN_CALL_TIMEOUT_S, float(override_s))
        except (TypeError, ValueError):
            pass
    ms = _cfg_int(cfg, "polish_timeout_ms", _DEFAULT_TIMEOUT_MS, lo=200, hi=5000)
    return ms / 1000.0


def _cfg_int(cfg: Any, name: str, default: int, *, lo: int, hi: int) -> int:
    """Read an int setting, clamped, with the default on anything unusable.

    Tolerant rather than strict on purpose: this runs after the microphone has
    closed and the user's words are already in hand. A malformed value in
    ``jarvis.toml`` must cost them a setting, never a dictation.
    """
    try:
        value = int(getattr(cfg, name, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _cfg_float(cfg: Any, name: str, default: float, *, lo: float, hi: float) -> float:
    """Read a float setting, clamped, with the default on anything unusable."""
    try:
        value = float(getattr(cfg, name, default))
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN compares unequal to itself and clamps to nothing.
        return default
    return max(lo, min(hi, value))


__all__ = [
    "POLISH_BREAKER_COOLDOWN_S",
    "POLISH_BREAKER_THRESHOLD",
    "POLISH_STATUSES",
    "PolishOutcome",
    "aclose",
    "polish_enabled",
    "polish_transcript",
    "reset_polish_state",
]
