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

Two jobs, one pass
------------------
The same machinery also runs the TRANSLATE pass (``[dictation].translate``),
which delivers the dictation in a fixed language whatever the speaker used. It
is the same chain, the same breaker, the same ceiling and the same fail-open
contract — only the prompt (:mod:`jarvis.dictation.translate_prompt`) and the
guard set (``translate_drift_reason``) differ, and formatting happens inside the
SAME call so a translation costs one round trip, not two. ``translate_to``
selects the mode; :func:`resolve_translate_target` decides it.

Precision mode rides on top of both
-----------------------------------
``[dictation].polish_precision`` licenses ONE further step in either pass:
replacing a word with a more precise one that means the same thing. It is a
prompt clause (:func:`jarvis.dictation.polish_prompt.precision_block`, shared by
both prompts so the switch means one thing) plus a matched guard
(``precision_drift_reason``), and the two must always travel together — the
ordinary guard rejects a substitution as ``lost_term``, which is the answer this
mode is built to produce. That guard trade is why it ships OFF: everything else
in this module can be defaulted on because it only ever costs a formatting pass,
and this one costs a check.

The contract, in one sentence
-----------------------------
**This pass can only ever be a no-op; it can never be a loss.**
:attr:`PolishOutcome.text` starts life as the raw transcript and is replaced
only after every deterministic guard in :mod:`jarvis.dictation.polish_guards`
has passed. :func:`polish_transcript` never raises on a failure of its own — it
catches ``Exception`` — and every failure path returns the user's own words with
a status that says what happened. It deliberately does NOT catch
``BaseException``: ``SystemExit``, ``KeyboardInterrupt`` and ``CancelledError``
are the process being told to stop, not a formatting failure, and turning one of
them into a ``provider_error`` would leave a task that refuses to die.

Four things bound it
--------------------
1. **A hard latency ceiling.** One ``asyncio.wait_for`` around the WHOLE pass —
   the credential sweep included, since that is exactly where a locked keyring
   blocks — default 1200 ms. On expiry the in-flight work is cancelled and the
   raw text is delivered. A formatter that makes the user wait has already
   failed at its job.
2. **A key-aware, family-crossing chain** (:func:`resolve_polish_chain`,
   AP-22). No key in any family means an empty chain, status ``unavailable``,
   and behaviour byte-identical to before this feature existed — the AP-23
   gate: the maintainer's credentials must never be what makes the default
   safe.
3. **A privacy floor.** When the configured recognizer transcribes on this
   machine, the chain never crosses to a cloud family on its own; if no local
   model answers, the status is ``local_only`` and the raw text is delivered.
   The user can still opt in by pinning a cloud ``polish_provider``.
4. **A circuit breaker.** Three consecutive provider errors or timeouts open it
   for 120 s, after which the pass short-circuits to ``unavailable`` without
   dialling out. A dead provider must not add 1.2 s to every dictation.

Nothing blocks the event loop
-----------------------------
The chain resolution walks up to seven credential slots and reads the config
file. On a Linux desktop with a locked keyring or a slow D-Bus Secret Service
that blocks for seconds, and on the event loop it would take the microphone
drain task, the WebSocket and the Jarvis Bar down with it — the same shape as
the ``load_config`` freeze in the bug register. So it runs in a worker thread,
inside the ceiling, and its answer is cached against
:func:`polish_chain_fingerprint`: one sweep per settings change, not one per
dictation.

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
    family_recently_unreachable,
    note_family_failure,
    polish_chain_fingerprint,
    reset_credential_cache,
    reset_reachability_cache,
    resolve_model,
    resolve_polish_chain,
)
from jarvis.dictation.polish_guards import (
    drift_reason,
    normalize_for_compare,
    precision_drift_reason,
    translate_drift_reason,
)
from jarvis.dictation.polish_prompt import (
    build_polish_prompt,
    build_polish_user_message,
)
from jarvis.dictation.translate_prompt import build_translate_prompt

log = logging.getLogger(__name__)

#: Every status the pass may report — the SSOT for this vocabulary, mirrored in
#: TypeScript and in the locale files and pinned by a parity test, exactly like
#: ``jarvis.dictation.outcomes.DICTATION_OUTCOMES``.
#:
#: Only ``applied`` means the delivered text differs from what came in. Every
#: other value means the user got their own words back, and says why.
POLISH_STATUSES: Final[tuple[str, ...]] = (
    "applied",        # the polished text was delivered
    "translated",     # the text was delivered in [dictation].translate_target
    "unchanged",      # the model returned text equal to raw after normalization
    "off",            # [dictation].polish = false
    "unavailable",    # no key in any family / client build failed / breaker open
    "local_only",     # on-device recognizer, and no on-device model answered
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
_DEFAULT_MAX_INPUT_CHARS = 0  # no cap; the clock and the drift guards bound it
_DEFAULT_MIN_WORDS = 4
_DEFAULT_MAX_OUTPUT_TOKENS = 1200
_DEFAULT_TEMPERATURE = 0.0
_DEFAULT_MAX_SHRINK = 0.55
_DEFAULT_MAX_GROWTH = 1.20

# The translate pass's own word-count band. Far wider than the polish one on
# purpose: a faithful translation legitimately changes length in both directions
# (German compounds collapse into one English word; English phrasal verbs expand
# into German subclauses), so the polish band would reject correct answers by
# construction. What is left is still a real backstop against a model that
# answered the transcript instead of translating it.
_DEFAULT_TRANSLATE_MAX_SHRINK = 0.40
_DEFAULT_TRANSLATE_MAX_GROWTH = 2.50

# Precision mode's own word-count band, and the reason it is not the polish one.
# Substitution moves the count in BOTH directions and further than repunctuation
# does: collapsing padding into a verb shortens ("make a decision" -> "decide",
# "the list of stuff it needs to run" -> "its dependencies"), while naming a
# thing the speaker described in a placeholder can lengthen. The polish ceiling
# of 1.20 is tuned for a pass that may only ever shrink or stay flat, so reusing
# it would reject correct answers by construction — the same mistake as running
# a translation through the polish band.
#
# Still deliberately tight, and tighter than the translate band: growth is what
# an answer, an explanation or a model that started embellishing looks like, and
# ornamentation is precisely this mode's documented failure direction. Module
# constants rather than config keys on purpose — the drift band is a safety
# floor, not a preference, and this feature already asks the user to give up one
# guard. Handing them a dial that quietly widens what is left would turn one
# understood trade into an unbounded one (AP-31: no unread config switch).
_DEFAULT_PRECISION_MAX_SHRINK = 0.45
_DEFAULT_PRECISION_MAX_GROWTH = 1.35

#: The value ``[dictation].translate_target`` uses to mean "no fixed target".
#: Shared spelling with ``[dictation].language`` so one word means one thing
#: across the block.
_AUTO = "auto"

#: The smallest per-call timeout we will hand a transport. A request given 0 s
#: fails in a way that looks like a provider outage instead of like our deadline.
_MIN_CALL_TIMEOUT_S = 0.05


@dataclass(frozen=True, slots=True)
class PolishOutcome:
    """The result of one polish attempt.

    ``text`` is ALWAYS safe to deliver: it is the raw transcript on every status
    except ``applied``. ``reason`` is machine-readable (a guard code from
    :data:`jarvis.dictation.polish_guards.DRIFT_REASONS`, or a short cause like
    ``no_credential`` / ``circuit_open`` / ``deadline`` / ``local_unreachable``)
    and empty when nothing went wrong.
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
    #: Every family the resolved chain offered keeps the text on this machine.
    #: Carried out of the chain so a failure is reported as ``local_only`` —
    #: "the recognizer runs here and no local model answered" — instead of a
    #: generic ``provider_error`` that would read like a cloud outage and send
    #: the user hunting for an API key they deliberately did not want to use.
    on_device_only: bool = False


_BREAKER: CircuitBreaker | None = None

#: The last resolved chain and the fingerprint it was resolved for. Process-wide
#: because the sweep behind it is process-wide state (credentials and the config
#: file), and ``None`` until the first dictation — nothing is resolved at import
#: or at boot (AP-26).
_CHAIN_CACHE: tuple[tuple[Any, ...], tuple[PolishFamily, ...]] | None = None


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
    """Forget everything the pass learned about this host.

    That is the breaker, the resolved provider chain, every memoised credential
    and every local endpoint that was found unreachable — one call, because all
    of them answer the same question ("what can this machine reach right now")
    and leaving any behind produces the confusing half-state where a repaired
    key is visible to one and not the others.

    Two real callers: ``PUT /api/dictation/settings``
    (:mod:`jarvis.ui.web.dictation_routes`), so a user who just switched
    provider, repaired a key or started a local model gets the new answer on
    their next dictation rather than waiting out a cooldown earned by the old
    one; and tests, which need a deterministic clock instead of sleeping for
    two minutes.

    Cheap and synchronous — it only drops state, so it is safe to call from the
    event loop inside a request handler.
    """
    global _BREAKER, _CHAIN_CACHE
    _CHAIN_CACHE = None
    reset_credential_cache()
    reset_reachability_cache()
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


def precision_enabled(cfg: Any) -> bool:
    """Whether the pass may also sharpen the WORD CHOICE. No I/O.

    Ships OFF, and an absent key reads as OFF, for a stronger reason than the
    translate switch has: this one trades away a guard. The rare-token check
    that rejects an answer in which an uncommon word silently vanished cannot
    coexist with a mode whose job is replacing uncommon words, so precision runs
    against :func:`~jarvis.dictation.polish_guards.precision_drift_reason` with
    that check dropped. Inheriting that from a default nobody chose is exactly
    the class of surprise this package refuses to ship.

    Read independently of :func:`polish_enabled` so the answer is the SAME
    whichever pass is running — with the formatter off and a translation on,
    precision still applies to the translation.
    """
    return bool(getattr(cfg, "polish_precision", False))


def translate_enabled(cfg: Any) -> bool:
    """Whether dictation is set to come out in a fixed language. No I/O.

    Ships OFF, and an absent key reads as OFF. Unlike the polish pass — which
    changes how words are written and can safely default on — this changes WHICH
    WORDS COME OUT, and a config that predates the feature must never acquire
    that silently.
    """
    return bool(getattr(cfg, "translate", False))


def resolve_translate_target(cfg: Any) -> str:
    """The language this dictation must be delivered in; ``""`` for no change.

    Module-level and public for the same reason
    ``jarvis.speech.pipeline.resolve_dictation_language`` is: TWO paths finish a
    dictation — the live delivery and the Restore route — and a second copy of
    this decision is how one recording ends up translated by one button and not
    by the other.

    **It reads the CONFIG and nothing else.** That is the whole design, and it
    is a correction of the first version, which also skipped the translation
    when the RECOGNIZED language already matched the target. That sounded like a
    saved round trip and behaved like a coin flip: the recognizer's language tag
    is documented-unreliable (``resolve_dictation_language`` exists because it
    confidently reports German dictations as English), so with an English target
    a mislabelled dictation silently kept its German — and the user watched
    their text alternate between two languages with nothing they touched
    explaining it. A switch that is on has to mean one thing.

    ``""`` on the three ways there is genuinely nothing to do, all of them
    statements the USER made rather than guesses about them:

    * the switch is off,
    * no target is set, or the target is ``auto`` — which is not a language,
    * the dictation LANGUAGE IS PINNED to the target. A pin is the one language
      signal a person sets deliberately, so "I dictate in English and I want
      English out" is a coherent instruction to do nothing, and the settings
      screen says so out loud rather than leaving the switch looking inert.

    Anything else translates. A dictation that turns out to be in the target
    language anyway is handled by the prompt (it cleans the text up instead of
    re-wording it) and costs the one model call the polish pass would have made
    regardless.
    """
    if not translate_enabled(cfg):
        return ""
    target = str(getattr(cfg, "translate_target", "") or "").strip().lower()
    if not target or target == _AUTO:
        return ""
    # Compare on the primary subtag so a "de-DE" pin and a "de" target are one
    # language rather than two.
    pinned = str(getattr(cfg, "language", _AUTO) or _AUTO).strip().lower()
    if pinned and pinned != _AUTO:
        if pinned.replace("_", "-").split("-", 1)[0] == target:
            return ""
    return target


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
    translate_to: str = "",
) -> PolishOutcome:
    """Format *raw* without changing what it says. Never raises, never loses text.

    ``language`` is the ALREADY-RESOLVED transcript language (the pipeline
    resolves it before it gets here); it steers the guards and appears in the
    prompt only as a weak, explicitly-unreliable hint. ``protected_terms`` are
    spellings the model may not "correct" — the STT dictionary, the wake word,
    the user's name. ``timeout_s`` overrides the configured ceiling; it exists
    for tests and for the settings-screen dry run.

    ``translate_to`` switches the pass into TRANSLATE mode: the text comes back
    in that language, cleaned up, and the status is ``translated`` rather than
    ``applied``. It is an already-resolved target code — the caller decides
    whether a translation is wanted at all
    (:func:`resolve_translate_target`), so ``""`` means "format only" and any
    non-empty value means "yes, into this".

    Translating and formatting are ONE model call, never two. Chaining them would
    double the latency of a pass that sits between the key release and the words
    appearing, and the polish half would be discarded by the translation anyway.
    So the translate prompt carries the cleanup rules too.

    The contract is unchanged in both modes, with one honest caveat. This pass
    can still only ever be a no-op: on a timeout, a dead provider, a missing key
    or a failed guard the user gets their own words back. But in translate mode
    a no-op is *visible* — the text arrives in the language it was spoken in —
    where a skipped polish is merely unpunctuated. That is why the status is
    reported on the history row and why the failure statuses are worth reading.
    """
    started = time.perf_counter()
    source = str(raw or "")
    target = str(translate_to or "").strip().lower()
    translating = bool(target)

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
        # A translation runs even with the polish switch off: the user asked for
        # their words in another language, and "the formatter is off" is not an
        # answer to that. The reverse also holds — with translation off this is
        # the plain polish gate it always was.
        if not polish_enabled(cfg) and not translating:
            return _result("off")
        if not source.strip():
            return _result("skipped_short", reason="empty_input")

        # ``polish_min_words`` deliberately does NOT apply to a translation.
        # Skipping the formatter on "call me back" is invisible; skipping the
        # translation on it delivers a German sentence into an English document,
        # and a feature that works on long dictations but not short ones reads as
        # broken rather than as tuned. The empty check above is the only floor.
        if not translating:
            min_words = _cfg_int(
                cfg, "polish_min_words", _DEFAULT_MIN_WORDS, lo=0, hi=1000
            )
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

        budget_s = _timeout_budget(cfg, timeout_s)
        attempt = _Attempt()
        # Resolved ONCE and used for both the prompt and the guard below. Those
        # two are a matched pair — the precision prompt licenses substitutions
        # that the ordinary guard rejects — so reading the switch twice is how
        # they end up disagreeing and the mode silently rejects its own correct
        # answers.
        precision = precision_enabled(cfg)
        if translating:
            system = build_translate_prompt(
                target_language=target,
                style=style,
                protected_terms=protected_terms,
                precision=precision,
            )
        else:
            system = build_polish_prompt(
                language=language,
                style=style,
                protected_terms=protected_terms,
                precision=precision,
            )
        # The SAME fenced user message either way — the delimiter and the
        # ``meta_output`` guard that watches for it are one mechanism, and a
        # dictation shaped like an instruction has to stay material in both
        # passes.
        user = build_polish_user_message(source)

        try:
            polished = await asyncio.wait_for(
                _resolve_and_run(
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
            if attempt.error == "no_credential":
                # The AP-23 path: nothing on this host was worth dialling. No
                # breaker failure — there was no provider to blame. Honest,
                # logged once per dictation at debug, and byte-identical to the
                # behaviour before the polish pass existed.
                log.debug(
                    "dictation polish found no usable provider family; "
                    "delivering the unpolished transcript."
                )
                return _result("unavailable", reason="no_credential")

            await breaker.record_failure()

            if attempt.on_device_only:
                # The privacy floor did its job: the recognizer transcribes on
                # this machine, so the chain was never allowed to cross to a
                # cloud family, and nothing local answered. Said out loud,
                # because "no polish happened" for THIS reason is a setup fact
                # the user can act on (start a local model, or pin a cloud one
                # deliberately) — not an outage.
                log.info(
                    "dictation polish stayed on this machine because the "
                    "recognizer does (%s); no local model answered, so the "
                    "unpolished transcript was delivered.",
                    attempt.error or "unreachable",
                )
                return _result(
                    "local_only",
                    provider=attempt.provider,
                    model=attempt.model,
                    reason=attempt.error or "local_unreachable",
                )

            return _result(
                "provider_error",
                provider=attempt.provider,
                model=attempt.model,
                reason=attempt.error or "no_provider",
            )

        await breaker.record_success()

        if normalize_for_compare(polished) == normalize_for_compare(source):
            if translating and not _already_in_target(source, target):
                # The same string back is a SUCCESS for a formatter and a
                # FAILURE for a translator: the model declined the job and the
                # user is about to paste the language they were translating out
                # of. Reported as the rejection it is, so the history row does
                # not claim "nothing needed doing" about a translation that
                # never happened.
                #
                # Unless the text was already in the target language, which is
                # the one case where no change IS the right answer.
                # ``resolve_translate_target`` normally keeps those off this
                # path, but it can only do so when the recognizer named a
                # language; on an undecided tag a clean English sentence bound
                # for English arrives here, and calling that a rejection would
                # invent a failure out of a correct no-op.
                log.info(
                    "dictation translation to %r came back unchanged on %r; "
                    "delivering the original transcript.",
                    target,
                    attempt.provider,
                )
                return _result(
                    "rejected_drift",
                    provider=attempt.provider,
                    model=attempt.model,
                    reason="not_translated",
                )
            # The model agreed there was nothing to do. Deliver the RAW string,
            # not its normalized twin — "unchanged" has to mean unchanged.
            return _result(
                "unchanged", provider=attempt.provider, model=attempt.model
            )

        if translating:
            reason = translate_drift_reason(
                source,
                polished,
                target_language=target,
                protected=protected_terms,
                max_shrink=_cfg_float(
                    cfg,
                    "translate_drift_max_shrink",
                    _DEFAULT_TRANSLATE_MAX_SHRINK,
                    lo=0.0,
                    hi=1.0,
                ),
                max_growth=_cfg_float(
                    cfg,
                    "translate_drift_max_growth",
                    _DEFAULT_TRANSLATE_MAX_GROWTH,
                    lo=1.0,
                    hi=10.0,
                ),
            )
        elif precision:
            # The matched guard for the precision prompt. Same checks as
            # ``drift_reason`` minus rare-token preservation, which a mode built
            # to replace uncommon words would fail on nearly every correct
            # answer, and with its own word-count band — substitution moves the
            # count in both directions where repunctuation only ever shrinks it.
            reason = precision_drift_reason(
                source,
                polished,
                language=language,
                protected=protected_terms,
                max_shrink=_DEFAULT_PRECISION_MAX_SHRINK,
                max_growth=_DEFAULT_PRECISION_MAX_GROWTH,
            )
        else:
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
                "dictation %s rejected by the %s guard on %r; delivering the "
                "original transcript.",
                "translation" if translating else "polish",
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
            "translated" if translating else "applied",
            provider=attempt.provider,
            model=attempt.model,
            text=polished,
        )
    except Exception:
        # Deliberately ``Exception`` and not ``BaseException``. Everything that
        # is genuinely OUR bug — a guard that raised, a malformed response, a
        # transport that surprised us — arrives here and costs the user nothing
        # but a formatting pass. The non-``Exception`` cases are a different
        # kind of event entirely: ``CancelledError``, ``KeyboardInterrupt`` and
        # ``SystemExit`` mean the process is being told to stop, they belong to
        # whoever raised them, and converting one of them into a
        # "provider_error" would leave a task that refuses to die.
        log.warning(
            "dictation polish failed unexpectedly; delivering the unpolished "
            "transcript.",
            exc_info=True,
        )
        return _result("provider_error", reason="unexpected")


def _already_in_target(text: str, target: str) -> bool:
    """Whether *text* is confidently already in *target*. Never guesses.

    Only ``de``/``en``/``es`` are decidable — that is what the shared detector
    knows — and an undecided answer is ``False`` on purpose: this exists to
    excuse an unchanged answer, and excusing one on a hunch would let a model
    that simply refused to translate look like a success.
    """
    if not target:
        return False
    from jarvis.core.turn_language import detect_text_language

    return detect_text_language(text) == target


async def _resolve_chain(cfg: Any) -> tuple[PolishFamily, ...]:
    """The provider chain, swept OFF the event loop and cached until it changes.

    Two separate defects meet here. The sweep walks up to seven credential
    slots, each of which can reach the OS keyring, plus the config file; on a
    Linux desktop with a locked keyring or a slow D-Bus Secret Service that
    blocks for SECONDS, and on the event loop it takes the microphone drain
    task, the WebSocket and the Jarvis Bar with it. And doing it per dictation
    is pure waste: nothing it reads can change between two dictations unless
    the user changed a setting, which is precisely what
    :func:`polish_chain_fingerprint` notices.

    A cancelled ``to_thread`` leaves its worker running to completion — a
    thread cannot be cancelled — but it writes nothing, so a pass that hit its
    ceiling mid-sweep simply sweeps again next time.
    """
    global _CHAIN_CACHE

    try:
        fingerprint: tuple[Any, ...] | None = polish_chain_fingerprint(cfg)
    except Exception as exc:  # noqa: BLE001 — an unfingerprintable host re-sweeps
        # Never fatal, and never a stale answer either: with no fingerprint we
        # decline to read OR write the cache and pay for a fresh sweep.
        log.debug("polish chain fingerprint failed (%s); resolving afresh.", exc)
        fingerprint = None

    cached = _CHAIN_CACHE
    if fingerprint is not None and cached is not None and cached[0] == fingerprint:
        return cached[1]

    chain = await asyncio.to_thread(resolve_polish_chain, cfg)
    if fingerprint is not None:
        _CHAIN_CACHE = (fingerprint, chain)
    return chain


async def _resolve_and_run(
    cfg: Any,
    *,
    system: str,
    user: str,
    attempt: _Attempt,
    deadline: float,
    max_output_tokens: int,
    temperature: float,
) -> str | None:
    """Resolve the chain and then walk it — both inside the caller's ceiling.

    The resolution lives in here rather than in front of the ``wait_for``
    because it is not free, and time spent outside the ceiling is time the user
    waits that nothing bounds. Inside it, the worst case is the same ceiling
    they were already promised, and a sweep that overruns it costs a formatting
    pass rather than a stalled desktop.

    ``None`` means "nothing to deliver", with the cause left on *attempt*: an
    empty chain sets ``no_credential`` (nothing was dialled and no provider is
    to blame), anything else carries the per-family error the walk recorded.
    """
    chain = await _resolve_chain(cfg)
    if not chain:
        attempt.error = "no_credential"
        return None

    # Derived from the chain rather than re-asked, so the answer the privacy
    # rule gave and the answer reported to the user cannot disagree.
    attempt.on_device_only = all(family.runs_on_device for family in chain)
    primary = chain[0]
    attempt.provider = primary.id
    attempt.model = resolve_model(primary, cfg, primary_id=primary.id)

    return await _run_chain(
        chain,
        cfg,
        system=system,
        user=user,
        attempt=attempt,
        deadline=deadline,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )


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

    A local endpoint that was found missing a moment ago is stepped over
    without dialling it: nothing on this machine started listening in the last
    few seconds, and the walk that pins the local family in front of a cloud
    one would otherwise pay that refusal on every delivery.
    """
    primary_id = chain[0].id
    for family in chain:
        model = resolve_model(family, cfg, primary_id=primary_id)
        attempt.provider = family.id
        attempt.model = model

        if family_recently_unreachable(family):
            attempt.error = "local_unreachable"
            continue

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
            # Only an on-device endpoint that answered NOTHING is remembered;
            # the function decides that, so this site stays a plain report of
            # what happened.
            note_family_failure(family, exc)
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
    "precision_enabled",
    "reset_polish_state",
    "resolve_translate_target",
    "translate_enabled",
]
