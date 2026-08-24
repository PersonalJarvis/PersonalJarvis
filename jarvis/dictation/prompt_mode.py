"""Prompt Mode — a dictation comes out as the prompt a coding agent should get.

What it is
----------
The user holds the dictation key, describes a task in their own words — half
sentences, false starts, several small tasks in one breath — and what lands
in the field is a finished prompt for an AI coding agent: the goal, the
context that was stated, the constraints, and nothing invented. Plain text,
in the language they spoke, written the way a person would say it to a
colleague. It is the Agentic IDE's prompt doctrine
(:mod:`jarvis.agentic_ide.prompt_blueprint`) aimed at a transcript instead of
a pane.

Why it rides the polish pass's fast chain
-----------------------------------------
The maintainer's bar is 5-7 s at most from the moment the transcript is
available to the prompt appearing, with quality and completeness ahead of
speed inside that window (2026-08-24). That rules out the Agentic IDE's
writer: a thinking-grade API model spends 3-15 s, a subscription CLI 10-20 s
of cold start, and neither fits the window reliably. The polish
pass already owns the only lane on this host that answers in well under a
second — the key-aware family chain in :mod:`jarvis.dictation.polish_client`
(a fast small model, with the family's stronger fast model when asked, the
same one the translate pass uses). So Prompt Mode is that lane with a
different system prompt, a slightly longer ceiling than the formatter's, and
its own guards.

What it keeps from the polish pass and what it does not
-------------------------------------------------------
Kept: the shape. Never raises, never loses text, one status per attempt,
fail-open to the ordinary passes, the fenced user message that keeps a
dictation shaped like an instruction from becoming one
(:func:`jarvis.dictation.polish_prompt.build_polish_user_message`), the
protected-terms block, the breaker.

Not kept: the drift guards. They measure how far the words moved, and here
they are supposed to move. Prompt Mode judges the SHAPE of the answer — not
empty, not markdown, not cut off, protected spellings still present.

Ships OFF. Unlike the formatter it changes WHAT the text says, on purpose,
and sends the words to a cloud model on most installs (the polish pass's
on-device rule still applies: a local recognizer keeps the chain local).

Pure orchestration — the imports that cost anything are inside the function
(AP-26), so ``import jarvis.dictation.prompt_mode`` is free on the boot path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from typing import Any, Final

from jarvis.agentic_ide.prompt_blueprint import (
    FORBIDDEN_SUBJECTS_RULE,
    GOAL_NOT_IMPLEMENTATION_RULE,
    looks_truncated,
)
from jarvis.dictation.polish import PolishOutcome
from jarvis.dictation.polish_prompt import (
    build_polish_user_message,
    build_protected_block,
)

log = logging.getLogger(__name__)

#: Bumped whenever the wording below changes in a way that could change model
#: behaviour, for the same reason ``POLISH_PROMPT_VERSION`` exists: a quality
#: regression must be attributable to a prompt revision, not a provider.
#:
#: v2: plain text instead of the markdown skeleton, the transcript's own
#: language instead of English, a spoken register (the humanizer rules, cut
#: to the six that matter at this latency), and an explicit rule that several
#: tasks spoken at once all survive. Measured live 2026-08-24: v1 answered
#: with ``## Task`` headings in English and folded two dictated tasks into one.
PROMPT_MODE_PROMPT_VERSION: Final[int] = 2

#: The status a successful Prompt Mode delivery reports on the history row.
#: Lives in ``POLISH_STATUSES`` (the shared vocabulary) — restated here as a
#: named constant so call sites compare against a name, not a string literal.
STATUS_PROMPTED: Final[str] = "prompted"

# The ceiling and its bounds. The maintainer's bar is 5-7 s from the finished
# transcript; the fast chain answers a prompt of this size in 0.5-2 s, so 6 s
# is the hard stop with room for a cross-family retry, and 10 s is as far as
# anyone may push it before the feature stops being dictation.
_DEFAULT_TIMEOUT_MS: Final[int] = 6_000
_MIN_TIMEOUT_MS: Final[int] = 2_000
_MAX_TIMEOUT_MS: Final[int] = 10_000

# Generous on purpose: a long dictation with five tasks must come back whole,
# and a cut-off answer is rejected as truncated and costs the whole pass. A
# fast model emits 1500 tokens in about a second. Temperature 0: this is
# rewriting, not writing.
_MAX_OUTPUT_TOKENS: Final[int] = 1_500
_TEMPERATURE: Final[float] = 0.0

# The composer's two shared rules plus what a transcript-only, latency-bound
# pass needs on top. No skeleton: the answer is prose, and a model handed a
# skeleton reproduces it.
_SYSTEM_PROMPT: Final[str] = f"""\
You turn a spoken, dictated instruction into the message a person would type \
to an AI coding agent. The user will paste what you write into that agent \
themselves.

{GOAL_NOT_IMPLEMENTATION_RULE}

The transcript is ALL you get, and your message is ALL the agent gets. You see \
no repository, no files, no earlier conversation. Everything must come from \
the transcript: a file, symbol, error message, number or name the user said \
goes in exactly as spoken; one they did not say does not exist. A pointer \
you cannot resolve ("that one", "the second option") is left out, not \
forwarded.

SEVERAL TASKS AT ONCE. People dictate two, three, five small tasks in one \
breath. Every one of them survives, in the order spoken, each as its own \
sentence or short paragraph. Never merge two tasks into one, never drop the \
small one because the big one seemed to be the point, never summarise a list \
of tasks into "and a few other things". Count them before you write and \
count them after.

PLAIN TEXT, SAME LANGUAGE. No markdown: no headings, no "##", no bullet \
marks, no bold, no code fences, no labels like "Task:" or "Context:". Write \
in the language the transcript is in; if it mixes languages, keep the mix. \
Names, identifiers, paths and quoted strings stay exactly as spoken.

SOUND LIKE A PERSON TALKING, not like a document:
- Short, direct sentences. Say the thing, then stop.
- Everyday words. No "utilize", "leverage", "ensure", "robust", "seamless", \
"comprehensive", "delve" or their equivalents in the transcript's language.
- No sets of three for rhythm, no "not only ... but also", no dash-heavy \
asides, no closing summary sentence.
- Keep the speaker's own phrasing wherever it is already clear; you are \
cleaning it up, not rewriting their voice.

Keep what the user said: every constraint, file, symbol, number and intent. \
Drop filler sounds, false starts and self-corrections (keep the corrected \
version), and any clause addressing the assistant or the agent by name. \
State what "done" looks like only when the user said it. Output ONLY the \
message: no preamble, no quotes, no closing remark.

{FORBIDDEN_SUBJECTS_RULE}\
"""

# What a plain-text answer must not contain. Headings and fences are the v1
# failure; bold and bullet marks are the shape a model slides back into when
# it is told "structure" without "prose".
_MARKDOWN_RE: Final[re.Pattern[str]] = re.compile(r"(?m)^\s*(#{1,6}\s|```|[-*]\s|\d+[.)]\s)|\*\*")


def prompt_mode_enabled(cfg: Any) -> bool:
    """``[dictation].prompt_mode`` — off on any config that has not heard of it."""
    return bool(getattr(cfg, "prompt_mode", False))


def build_prompt_mode_prompt(protected_terms: Sequence[str] = ()) -> str:
    """The system prompt for one Prompt Mode call.

    ``protected_terms`` are the spellings the writer must carry across
    untouched — the STT dictionary, the wake word, the user's own name. The
    block is the polish pass's, not a copy: what "spell this exactly" looks
    like to a model is one decision.
    """
    return f"{_SYSTEM_PROMPT}\n\n{build_protected_block(protected_terms)}"


def prompt_guard_reason(raw: str, prompt: str, *, protected: Sequence[str] = ()) -> str:
    """Why *prompt* must not be delivered, or ``""`` when it may.

    Structural, never semantic. The drift guards of the polish pass measure
    how far the words moved, and here they are SUPPOSED to move; what can
    still go wrong is the shape of the answer:

    * ``empty`` — nothing came back worth pasting.
    * ``markdown`` — a heading, a fence, bold or a list mark. The answer is
      pasted into a chat box as a message; markdown there is the v1 defect.
    * ``truncated`` — stopped mid-sentence. Reads as complete, is not.
    * ``lost_protected_term`` — a spelling the user protected was in the
      transcript and is not in the prompt. The dictionary exists because the
      recognizer gets these wrong; a writer that drops one has undone that.
    """
    body = (prompt or "").strip()
    if not body:
        return "empty"
    if _MARKDOWN_RE.search(body):
        return "markdown"
    if looks_truncated(body):
        return "truncated"
    source = (raw or "").casefold()
    target = body.casefold()
    for term in protected or ():
        needle = str(term or "").strip().casefold()
        if needle and needle in source and needle not in target:
            return "lost_protected_term"
    return ""


def timeout_budget_s(cfg: Any, override_s: float | None = None) -> float:
    """The wall-clock ceiling for one call, in seconds."""
    if override_s is not None:
        return max(0.05, float(override_s))
    try:
        ms = int(getattr(cfg, "prompt_mode_timeout_ms", _DEFAULT_TIMEOUT_MS))
    except (TypeError, ValueError):
        # A hand-edited jarvis.toml that never reached the validator (a plain
        # object in tests, an older config class): the shipped default is the
        # right answer and nobody needs a log line about a missing knob.
        ms = _DEFAULT_TIMEOUT_MS
    return max(_MIN_TIMEOUT_MS, min(_MAX_TIMEOUT_MS, ms)) / 1000.0


async def compose_prompt(
    raw: str,
    *,
    cfg: Any,
    protected_terms: Sequence[str] = (),
    timeout_s: float | None = None,
) -> PolishOutcome:
    """Turn *raw* into an agent prompt. Never raises, never loses text.

    Returns a :class:`~jarvis.dictation.polish.PolishOutcome` so the delivery
    path, the history row and the settings screen read Prompt Mode through the
    same fields they already read the polish pass through. ``text`` is the
    prompt on :data:`STATUS_PROMPTED` and the untouched *raw* on every other
    status — the caller then falls through to whatever it would have done
    without this feature, which is the polish pass.

    The model is the polish pass's own chain (``[dictation].polish_provider``
    and its ``auto`` order), asked for the family's stronger fast model — the
    one the translate pass uses — because turning speech into a brief asks
    more of a model than punctuating it, and that model still answers inside
    the ceiling.
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
        if not prompt_mode_enabled(cfg):
            return _result("off")
        if not source.strip():
            return _result("skipped_short", reason="empty_input")

        from jarvis.dictation import polish as polish_pass

        breaker = polish_pass._breaker()
        if await breaker.is_open():
            return _result("unavailable", reason="circuit_open")

        budget_s = timeout_budget_s(cfg, timeout_s)
        attempt = polish_pass._Attempt()
        system = build_prompt_mode_prompt(protected_terms)
        user = build_polish_user_message(source)
        try:
            answer = await asyncio.wait_for(
                _call_chain(
                    cfg,
                    system=system,
                    user=user,
                    attempt=attempt,
                    deadline=time.monotonic() + budget_s,
                ),
                budget_s,
            )
        except TimeoutError:
            await breaker.record_failure()
            log.info(
                "dictation prompt mode exceeded its %d ms ceiling on %r; delivering "
                "the transcript to the ordinary passes.",
                int(budget_s * 1000),
                attempt.provider,
            )
            return _result(
                "timeout", provider=attempt.provider, model=attempt.model, reason="deadline"
            )

        if answer is None:
            if attempt.error == "no_credential":
                log.debug(
                    "dictation prompt mode found no usable provider family; "
                    "delivering the transcript to the ordinary passes."
                )
                return _result("unavailable", reason="no_credential")
            await breaker.record_failure()
            status = "local_only" if attempt.on_device_only else "provider_error"
            return _result(
                status,
                provider=attempt.provider,
                model=attempt.model,
                reason=attempt.error or "no_provider",
            )

        await breaker.record_success()

        reason = prompt_guard_reason(source, answer, protected=protected_terms)
        if reason:
            log.info(
                "dictation prompt mode answer from %r rejected (%s); delivering the "
                "transcript to the ordinary passes.",
                attempt.provider,
                reason,
            )
            return _result(
                "rejected_drift", provider=attempt.provider, model=attempt.model, reason=reason
            )

        return _result(
            STATUS_PROMPTED,
            provider=attempt.provider,
            model=attempt.model,
            text=answer.strip(),
        )
    except Exception:
        # ``Exception``, not ``BaseException``, for the reason the polish pass
        # gives: a cancellation or an interpreter exit belongs to whoever
        # raised it, and swallowing one leaves a task that refuses to die.
        log.warning(
            "dictation prompt mode failed unexpectedly; delivering the transcript "
            "to the ordinary passes.",
            exc_info=True,
        )
        return _result("provider_error", reason="unexpected")


async def _call_chain(
    cfg: Any,
    *,
    system: str,
    user: str,
    attempt: Any,
    deadline: float,
) -> str | None:
    """One walk of the polish family chain. Split out so tests can stand in."""
    from jarvis.dictation.polish import _resolve_and_run

    return await _resolve_and_run(
        cfg,
        system=system,
        user=user,
        attempt=attempt,
        deadline=deadline,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        temperature=_TEMPERATURE,
        # The family's stronger fast model, exactly as the translate pass asks
        # for it: a brief is a rewrite, not a repunctuation.
        translating=True,
    )


__all__ = [
    "PROMPT_MODE_PROMPT_VERSION",
    "STATUS_PROMPTED",
    "build_prompt_mode_prompt",
    "compose_prompt",
    "prompt_guard_reason",
    "prompt_mode_enabled",
    "timeout_budget_s",
]
