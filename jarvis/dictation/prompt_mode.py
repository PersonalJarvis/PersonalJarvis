"""Prompt Mode — a dictation comes out as the prompt a coding agent should get.

What it is
----------
The user holds the dictation key, describes a task in their own words — half
sentences, false starts, whichever language they think in — and what lands in
the field is a finished, English prompt for an AI coding agent: the goal, the
context that was stated, the constraints, and nothing invented. It is the
Agentic IDE's prompt composer turned into a dictation target: the same writing
rules (:mod:`jarvis.agentic_ide.prompt_blueprint`), the same writer order
(:mod:`jarvis.agentic_ide.writer` — the Tool Model the user pinned, then the
API-billed quality tier, then a connected coding subscription), aimed at a
transcript instead of a pane.

What it is NOT
--------------
It is not the polish pass with a longer prompt. The polish pass
(:mod:`jarvis.dictation.polish`) is a *formatter*: a fast small model, a
1.2 s ceiling, and drift guards that reject any answer whose words moved —
which is exactly right for "punctuate what I said" and exactly wrong for
"rewrite what I said into a brief". So this module keeps the polish pass's
*shape* (never raises, never loses text, one status per attempt, fail-open to
the user's own words) and none of its *judgement*: a thinking-grade writer, a
budget measured in seconds, and structural checks (is it a brief, did it
finish, did it keep the protected spellings) instead of word-count bands.

Why the transcript never touches the system prompt
--------------------------------------------------
Same rule as the polish pass: a dictation is untrusted material and people
dictate sentences shaped like instructions. The transcript travels as the
fenced user message :func:`jarvis.dictation.polish_prompt.build_polish_user_message`
builds — one mechanism, one delimiter, one defusing routine.

Ships OFF. Unlike the formatter, this changes WHAT the text says on purpose,
and it sends the words to the writer the Agentic IDE uses, which is a cloud
model on most installs. A feature like that is chosen, never inherited from a
default.

Pure orchestration — the imports that cost anything are inside the function
(AP-26), so ``import jarvis.dictation.prompt_mode`` is free on the boot path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any, Final

from jarvis.agentic_ide.prompt_blueprint import (
    FORBIDDEN_SUBJECTS_RULE,
    GOAL_NOT_IMPLEMENTATION_RULE,
    ends_on_reference,
    looks_like_brief,
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
PROMPT_MODE_PROMPT_VERSION: Final[int] = 1

#: The status a successful Prompt Mode delivery reports on the history row.
#: Lives in ``POLISH_STATUSES`` (the shared vocabulary) — restated here as a
#: named constant so call sites compare against a name, not a string literal.
STATUS_PROMPTED: Final[str] = "prompted"

# The ceiling and its bounds. A thinking-grade writer answers in 3-15 s on a
# warm API and pays a 10-20 s cold start on a subscription CLI; the default
# covers the API case with room and lets the CLI case through on a good day.
_DEFAULT_TIMEOUT_MS: Final[int] = 20_000
_MIN_TIMEOUT_MS: Final[int] = 2_000
_MAX_TIMEOUT_MS: Final[int] = 120_000

# One call, not a stream the user watches: the answer is pasted whole.
_MAX_OUTPUT_TOKENS: Final[int] = 4096
_TEMPERATURE: Final[float] = 0.2

_SKELETON: Final[str] = """\
## Task
<what must be achieved, in the imperative - one to three sentences>

## Context
- <a fact the user stated that the agent needs: the symptom, the file or \
feature they named, what they already tried>

## Done when
- <an observable outcome the user actually stated>\
"""

# The composer's rules, minus everything that presumes a workspace: no tree to
# pick @files from, no pane name to strip, no earlier conversation to resolve
# ordinals against. What remains is the writing doctrine itself, imported from
# the blueprint so the two surfaces cannot drift apart.
_SYSTEM_PROMPT: Final[str] = f"""\
You turn a spoken, dictated instruction into a prompt for an AI coding agent \
or assistant. The user will paste what you write into that agent themselves, \
so write the prompt you would want to receive.

{GOAL_NOT_IMPLEMENTATION_RULE}

The prompt is ALL the agent gets, and the transcript is ALL you get. You see no \
repository, no files, no earlier conversation. Everything in the prompt must \
come from the transcript: a file, symbol, error message, number or name the \
user said goes in VERBATIM; one they did not say does not exist. When the \
user pointed at something you cannot see ("that one", "the second option"), \
keep what IS known and leave the pointer out rather than forwarding it.

Markdown, exactly this skeleton:

{_SKELETON}

- Output ONLY the prompt: no preamble, no code fence, no quotes, no closing \
remark.
- `## Task` is mandatory; every other section is OPTIONAL. Omit any you \
cannot ground in the transcript rather than padding it.
- INVENTING is forbidden: a requirement, constraint, boundary, file or \
success criterion the user did not state. `## Done when` holds only what the \
user asked for. Omit it otherwise.
- Never end the prompt on a file path or a `/command` - finish on a sentence.
- ENGLISH, whatever language the user spoke. Names, identifiers and quoted \
strings stay exactly as spoken.
- Preserve every constraint, file, symbol, number and intent expressed; drop \
filler sounds, false starts, self-corrections (keep the corrected version) \
and any clause addressing the agent or the assistant by name.
- Length follows the instruction: a one-sentence request is a short prompt, \
a long dictation with many details is a longer one. Never pad, never \
summarise details away.

{FORBIDDEN_SUBJECTS_RULE}\
"""


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

    * ``not_a_prompt`` — no markdown heading at all. A CLI writer answers on
      stdout whether it understood or not, and a one-line tool error typed
      into a coding agent as its task is the worst outcome this feature has.
    * ``truncated`` — stopped mid-sentence. Reads as complete, is not.
    * ``lost_protected_term`` — a spelling the user protected was in the
      transcript and is not in the prompt. The dictionary exists because the
      recognizer gets these wrong; a writer that drops one has undone that.
    """
    if not looks_like_brief(prompt):
        return "not_a_prompt"
    if looks_truncated(prompt):
        return "truncated"
    source = (raw or "").casefold()
    target = (prompt or "").casefold()
    for term in protected or ():
        needle = str(term or "").strip().casefold()
        if needle and needle in source and needle not in target:
            return "lost_protected_term"
    return ""


def _finish_cleanly(prompt: str) -> str:
    """Never hand over a prompt that ends on an ``@path`` or ``/command``.

    The destination is a coding agent, and in its terminal a trailing
    reference holds the completion popup open so the Enter that follows picks
    a suggestion instead of submitting (see ``prompt_blueprint``). One plain
    closing sentence, the same one the deterministic composer uses.
    """
    body = prompt.rstrip()
    if ends_on_reference(body):
        return body + "\n\nStart there; search further if it is not enough."
    return body


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
    writer: Any | None = None,
) -> PolishOutcome:
    """Turn *raw* into an agent prompt. Never raises, never loses text.

    Returns a :class:`~jarvis.dictation.polish.PolishOutcome` so the delivery
    path, the history row and the settings screen read Prompt Mode through the
    same fields they already read the polish pass through. ``text`` is the
    prompt on :data:`STATUS_PROMPTED` and the untouched *raw* on every other
    status — the caller then falls through to whatever it would have done
    without this feature, which is the polish pass.

    ``writer`` is a Brain to use instead of resolving one; it exists for tests
    and for the settings dry run. With ``None`` the writer is the Agentic IDE's
    (:func:`jarvis.agentic_ide.writer.resolve_writer`) — the "who writes
    briefs" choice under API Keys → Agents governs this feature too, on
    purpose: the user answered "which model turns my words into a prompt"
    once, and a second picker for the same question would be the AP-4 drift
    shape with a settings screen on top.
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

        budget_s = timeout_budget_s(cfg, timeout_s)
        brain = writer
        source_label = "caller"
        if brain is None:
            brain, source_label = await asyncio.to_thread(_resolve_writer, budget_s)
        if brain is None:
            # The AP-23 path: no Tool Model, no API tier, no signed-in CLI.
            # Logged once at info because the user switched this on and is
            # about to receive their raw words instead — that is worth a line.
            log.info(
                "dictation prompt mode found no writer on this host; delivering "
                "the transcript unchanged."
            )
            return _result("unavailable", reason="no_writer")

        provider = _brain_name(brain)
        model = _brain_model(brain)
        system = build_prompt_mode_prompt(protected_terms)
        user = build_polish_user_message(source)
        try:
            prompt = await asyncio.wait_for(_write(brain, system, user), budget_s)
        except TimeoutError:
            log.info(
                "dictation prompt mode exceeded its %d ms ceiling on %s; delivering "
                "the transcript unchanged.",
                int(budget_s * 1000),
                source_label,
            )
            return _result("timeout", provider=provider, model=model, reason="deadline")
        except Exception as exc:  # noqa: BLE001 — one dead writer costs the prompt, not the words
            log.warning(
                "dictation prompt mode writer %s failed (%s: %s); delivering the "
                "transcript unchanged.",
                source_label,
                type(exc).__name__,
                exc,
            )
            return _result(
                "provider_error",
                provider=provider,
                model=model,
                reason=type(exc).__name__,
            )

        reason = prompt_guard_reason(source, prompt, protected=protected_terms)
        if reason:
            log.info(
                "dictation prompt mode answer from %s rejected (%s); delivering the "
                "transcript unchanged.",
                source_label,
                reason,
            )
            return _result("rejected_drift", provider=provider, model=model, reason=reason)

        return _result(
            STATUS_PROMPTED,
            provider=provider,
            model=model,
            text=_finish_cleanly(prompt),
        )
    except Exception:
        # ``Exception``, not ``BaseException``, for the reason the polish pass
        # gives: a cancellation or an interpreter exit belongs to whoever
        # raised it, and swallowing one leaves a task that refuses to die.
        log.warning(
            "dictation prompt mode failed unexpectedly; delivering the transcript unchanged.",
            exc_info=True,
        )
        return _result("provider_error", reason="unexpected")


def _resolve_writer(budget_s: float) -> tuple[Any | None, str]:
    """The Agentic IDE's writer, resolved off the loop (it probes CLI sign-ins)."""
    from jarvis.agentic_ide.writer import resolve_writer

    return resolve_writer(cli_timeout_s=budget_s)


async def _write(brain: Any, system: str, user: str) -> str:
    from jarvis.core.protocols import BrainMessage, BrainRequest

    request = BrainRequest(
        messages=(BrainMessage(role="user", content=user),),
        system=system,
        temperature=_TEMPERATURE,
        max_tokens=_MAX_OUTPUT_TOKENS,
        stream=True,
        # Unlike the pane composer, which runs on a voice turn's critical path
        # and switches thinking OFF to land in 1-5 s, this call sits between a
        # key release and a paste the user has already decided to wait for —
        # they chose "turn my words into a proper prompt", not "punctuate
        # them". A graded effort is what buys a brief that resolves "make it
        # faster" into the goal behind it. Providers without a reasoning knob
        # ignore the hint.
        reasoning_effort="medium",
    )
    chunks: list[str] = []
    async for delta in brain.complete(request):
        if delta.content:
            chunks.append(delta.content)
    return "".join(chunks).strip()


def _brain_name(brain: Any) -> str:
    return str(getattr(brain, "name", "") or "")


def _brain_model(brain: Any) -> str:
    return str(getattr(brain, "model", "") or getattr(brain, "model_name", "") or "")


__all__ = [
    "PROMPT_MODE_PROMPT_VERSION",
    "STATUS_PROMPTED",
    "build_prompt_mode_prompt",
    "compose_prompt",
    "prompt_guard_reason",
    "prompt_mode_enabled",
    "timeout_budget_s",
]
