"""Prompt Mode — a dictation comes out as the prompt a coding agent should get.

What it is
----------
The user holds the dictation key, describes a task in their own words — half
sentences, false starts, several small tasks in one breath — and what lands
in the field is a finished prompt for an AI coding agent: the situation they
described, the goal, the constraints that were stated, and nothing invented.
Plain text, in the language they spoke, written the way a person would put
it to a colleague, closing with a line of thanks. It is the Agentic IDE's
prompt doctrine (:mod:`jarvis.agentic_ide.prompt_blueprint`) aimed at a
transcript instead of a pane.

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
empty, not markdown, not cut off, protected spellings still present — and
then finishes it deterministically: typographic debris a fast model leaves
behind is normalised, and the closing line of thanks is guaranteed in code,
never left to the model alone.

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
#:
#: v3: the shape of a professional request (situation, goal, limits, thanks)
#: as paragraphs without labels, "fix what exists" instead of "implement",
#: one statement per fact (no restating "make sure" sentences), a courteous
#: register with the transcript-language word bans, plain typography, a
#: closing line of thanks, and two worked examples. Measured live 2026-08-27
#: on the fast chain: v2 dropped the observed symptom ("shows done while it
#: is still working"), turned a broken existing indicator into "implement an
#: indicator", padded with a "Stelle sicher" restatement of the sentence
#: before, and wrote U+2011 non-breaking hyphens into identifiers.
PROMPT_MODE_PROMPT_VERSION: Final[int] = 3

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

# The closing line, per language, for the case the model left it out. The
# rule in the prompt asks for it; this table makes it a guarantee (the
# maintainer's ask, 2026-08-27: every prompt ends with thanks). The code is
# the resolved dictation language; anything not listed falls back to English,
# the language every coding agent reads.
_THANKS_BY_LANGUAGE: Final[dict[str, str]] = {
    "en": "Thanks!",
    "de": "Danke!",  # i18n-allow: closed product-output pool
    "es": "¡Gracias!",
    "fr": "Merci !",
    "it": "Grazie!",
    "pt": "Obrigado!",
    "nl": "Bedankt!",
}
_DEFAULT_THANKS: Final[str] = _THANKS_BY_LANGUAGE["en"]

# What a closing line of thanks looks like, across the languages above and
# the ones a recognizer commonly reports. Anchored to the LAST line of the
# prompt: "thanks" in the middle of the text is the user's own wording and
# proves nothing about the ending. Matches a short line only — a paragraph
# that merely contains the word is a paragraph, not a closing.
_THANKS_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\W{0,3}(?:\w+\s+){0,6}?"
    r"(?:thank(?:s|\s+you)?|dank(?:e|esch[öo]n)?|gracias|merci|grazie|obrigad[oa]|bedankt|"
    r"tak|tack|takk|kiitos|dzi[eę]kuj[eę]|d[ěe]kuji|спасибо|ありがとう|谢谢)"
    r"[\w\s,!.]{0,40}$",
    re.IGNORECASE,
)

# The composer's two shared rules plus what a transcript-only, latency-bound
# pass needs on top. No skeleton: the answer is prose, and a model handed a
# skeleton reproduces it. The four parts of a good request are described as
# paragraphs, not numbered, for the same reason.
_SYSTEM_PROMPT: Final[str] = f"""\
You turn a spoken, dictated instruction into the message a person would type \
to an AI coding agent. The user will paste what you write into that agent \
themselves, so it has to read like a well-written request from a colleague: \
complete, calm, courteous, and no longer than it needs to be.

{GOAL_NOT_IMPLEMENTATION_RULE}

The transcript is ALL you get, and your message is ALL the agent gets. You see \
no repository, no files, no earlier conversation. Everything must come from \
the transcript: a file, symbol, error message, number or name the user said \
goes in exactly as spoken; one they did not say does not exist. A pointer \
you cannot resolve ("that one", "the second option") is left out, not \
forwarded.

WHAT A GOOD REQUEST CONTAINS, as plain paragraphs in this order, with no \
labels in front of them. First the situation, when the user described one: \
what they see, what they expected instead, where it shows up. This is the \
most valuable part of the transcript - keep every concrete detail of it \
(which screen, which state, "it says done while it is still working"), \
because the agent starts from it. Then what should be true afterwards - the \
goal, in one or two sentences. Then every limit, preference or exclusion the \
user stated ("only", "never", "not the live app", "keep the wording"). Last, \
one short line of thanks on its own line, in the transcript's language - \
"Thanks!" or its equivalent - always, and nothing after it. A part the \
transcript gives you nothing for is skipped, never filled in.

FIX WHAT EXISTS. When the user says a thing exists and is wrong ("the \
indicators don't work", "the title is missing on some chats"), the request is \
to repair that thing - never turn it into "implement" or "create" something \
new. A second one built next to the broken one is the outcome to avoid.

SEVERAL TASKS AT ONCE. People dictate two, three, five small tasks in one \
breath. Every one of them survives, in the order spoken, each as its own \
sentence or short paragraph. Never merge two tasks into one, never drop the \
small one because the big one seemed to be the point, never summarise a list \
of tasks into "and a few other things". Count them before you write and \
count them after.

SAY EACH THING ONCE. A requirement stated twice in different words is \
padding, not emphasis: no closing sentence that restates the goal, no "make \
sure that" clause that repeats the sentence before it, no summary. A sentence \
that adds no fact the message does not already have is left out.

PLAIN TEXT, SAME LANGUAGE. No markdown: no headings, no "##", no bullet \
marks, no bold, no code fences, no labels like "Task:" or "Context:". Write \
in the language the transcript is in; if it mixes languages, keep the mix. \
Names, identifiers, paths and quoted strings stay exactly as spoken. Use the \
ordinary hyphen-minus and ordinary spaces: no typographic hyphens, no \
non-breaking spaces, no two spaces at the end of a line.

SOUND LIKE A PERSON WRITING TO A COLLEAGUE, not like a ticket and not like a \
document:
- Short, direct sentences. Say the thing, then stop.
- Courteous, not commanding: "please" where a person would say it, and the \
user's own "I would like" where they said it. Never a bare string of orders.
- Address the agent the way the user did. In a language that distinguishes \
a familiar from a formal "you", keep the user's form and never switch to the \
formal one on your own; a transcript that shows neither gets the familiar one.
- Everyday words. No "utilize", "leverage", "ensure", "make sure", "robust", \
"seamless", "comprehensive", "delve" - and not their equivalents in the \
transcript's language either.
- No sets of three for rhythm, no "not only ... but also", no dash-heavy \
asides.
- Keep the speaker's own phrasing wherever it is already clear; you are \
cleaning it up, not rewriting their voice.

Keep what the user said: every constraint, file, symbol, number and intent. \
Drop filler sounds, false starts and self-corrections (keep the corrected \
version), and any clause addressing the assistant or the agent by name. \
State what "done" looks like only when the user said it. Output ONLY the \
message: no preamble, no quotes, no comment of your own.

{FORBIDDEN_SUBJECTS_RULE}

Two examples of the shape. Each shows a transcript and the message written \
for it.

Transcript: "hallo es werden mir nicht mehr diese indikatoren funktionieren \
nicht korrekt ich möchte und vor allem immer live nicht korrekt ich möchte \
dass diese indikatoren richtig funktionieren und die indikatoren dass man ob \
die session auch arbeitet oder nicht weil es ist zum beispiel so dass es so \
angezeigt wird zum beispiel wenn die session arbeitet dass sie fertig ist \
oder andersrum"
Message:
Die Indikatoren, die anzeigen, ob eine Session gerade arbeitet oder fertig \
ist, stimmen nicht mehr: Eine arbeitende Session wird als fertig angezeigt \
und umgekehrt, und sie aktualisieren sich nicht live.

Bitte bring die Indikatoren wieder in Ordnung, sodass sie den echten Zustand \
der Session zeigen und sich live aktualisieren.

Danke!

Transcript: "okay so um the login page is broken again when you type a wrong \
password it just shows a blank screen instead of the error message i think \
it's in the auth handler file can you have a look and fix it so the message \
shows up like it used to and also rename the save button to submit"
Message:
The login page is broken again: when you type a wrong password it shows a \
blank screen instead of the error message. I think it's in the auth handler \
file.

Please have a look and fix it so the error message shows up like it used to.

Also, please rename the save button to submit.

Thanks!\
"""

# What a plain-text answer must not contain. Headings and fences are the v1
# failure; bold and bullet marks are the shape a model slides back into when
# it is told "structure" without "prose".
_MARKDOWN_RE: Final[re.Pattern[str]] = re.compile(r"(?m)^\s*(#{1,6}\s|```|[-*]\s|\d+[.)]\s)|\*\*")

# Typographic debris a fast model writes into identifiers and paths. Measured
# live 2026-08-27: gpt-oss on the fast chain joined "Jarvis‑Bar" and
# "Self‑Hosted" with U+2011 (non-breaking hyphen), which a grep for "Self-Hosted"
# in the receiving agent never matches. The whole hyphen block maps to the
# hyphen-minus when it sits inside a word; a spaced dash stays a spaced dash
# so an aside the model wrote as " – " is not turned into " - " by force.
_TIGHT_HYPHEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<=\w)[\u2010\u2011\u2012\u2013\u2014](?=\w)"
)
_LOOSE_HYPHEN_RE: Final[re.Pattern[str]] = re.compile(r"[\u2010\u2011]")
_SPECIAL_SPACE_RE: Final[re.Pattern[str]] = re.compile(r"[\u00a0\u202f\u2007\u2009\u200b]")
_TRAILING_WS_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANK_RUN_RE: Final[re.Pattern[str]] = re.compile(r"\n{3,}")

# The words that hint at a language when the resolver left it open ("auto",
# an empty tag). Only the languages the thanks table knows; a single hit is
# enough because the alternative is a wrong-language closing line, and the
# function words below are near-unique to their language.
_LANGUAGE_HINTS: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ("de", frozenset({"nicht", "und", "dass", "ich", "bitte", "wird", "auch", "soll"})),
    ("es", frozenset({"que", "para", "los", "las", "pero", "también", "esto", "hacer"})),
    ("fr", frozenset({"pas", "les", "pour", "avec", "dans", "cette", "faire"})),
    ("it", frozenset({"che", "anche", "questo", "fare", "sono", "perché"})),
    ("pt", frozenset({"não", "também", "isso", "fazer", "está", "para"})),
    ("nl", frozenset({"niet", "het", "ook", "moet", "deze", "maken"})),
)
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[^\W\d_]+", re.UNICODE)


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


def normalize_prompt_text(text: str) -> str:
    """Undo the typographic debris a fast model leaves in a plain-text prompt.

    Deterministic and lossless in meaning: a non-breaking hyphen inside a word
    becomes the hyphen-minus the user's keyboard produces, special spaces
    become spaces, trailing spaces on a line go (two of them are a markdown
    line break, which the answer must not carry), and runs of blank lines
    collapse to one paragraph break. Runs BEFORE the guards, so a protected
    spelling the model wrote with a U+2011 still counts as present.
    """
    body = str(text or "")
    body = _TIGHT_HYPHEN_RE.sub("-", body)
    body = _LOOSE_HYPHEN_RE.sub("-", body)
    body = _SPECIAL_SPACE_RE.sub(" ", body)
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = _TRAILING_WS_RE.sub("", body)
    body = _BLANK_RUN_RE.sub("\n\n", body)
    return body.strip()


def ends_with_thanks(text: str) -> bool:
    """Whether the LAST line of *text* is a short line of thanks."""
    lines = [line.strip() for line in str(text or "").strip().splitlines() if line.strip()]
    if not lines:
        return False
    return bool(_THANKS_LINE_RE.match(lines[-1]))


def guess_language(text: str, hint: str = "") -> str:
    """The language code the closing line is written in.

    The resolved dictation language wins when it names one; ``auto`` or an
    empty tag falls back to a function-word sniff over the text, and English
    when even that says nothing — the language every coding agent reads.
    """
    code = str(hint or "").strip().lower().split("-")[0]
    if code and code != "auto":
        return code
    words = {match.group(0).casefold() for match in _WORD_RE.finditer(str(text or ""))}
    if not words:
        return "en"
    best = ("en", 0)
    for language, markers in _LANGUAGE_HINTS:
        hits = len(words & markers)
        if hits > best[1]:
            best = (language, hits)
    return best[0]


def closing_thanks(language: str) -> str:
    """The thanks line for *language*, English when the table has no entry."""
    return _THANKS_BY_LANGUAGE.get(str(language or "").strip().lower(), _DEFAULT_THANKS)


def ensure_closing_thanks(text: str, *, language: str = "") -> str:
    """*text* with exactly one closing line of thanks.

    The prompt asks the model for it; this is the guarantee. A model that
    already closed with thanks is left alone (never two), one that did not
    gets the line for the resolved language appended as its own paragraph.
    Runs AFTER the truncation guard on purpose: appending a full stop to a
    prompt that stopped mid-sentence would hide exactly the damage that guard
    exists to catch.
    """
    body = str(text or "").strip()
    if not body or ends_with_thanks(body):
        return body
    return f"{body}\n\n{closing_thanks(guess_language(body, language))}"


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
    language: str = "",
) -> PolishOutcome:
    """Turn *raw* into an agent prompt. Never raises, never loses text.

    Returns a :class:`~jarvis.dictation.polish.PolishOutcome` so the delivery
    path, the history row and the settings screen read Prompt Mode through the
    same fields they already read the polish pass through. ``text`` is the
    prompt on :data:`STATUS_PROMPTED` and the untouched *raw* on every other
    status — the caller then falls through to whatever it would have done
    without this feature, which is the polish pass.

    ``language`` is the resolved dictation language (the same code the polish
    pass receives); it decides the language of the closing thanks when the
    model left that line out. Empty or ``auto`` means "read it off the text".

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

        prompt = normalize_prompt_text(answer)
        reason = prompt_guard_reason(source, prompt, protected=protected_terms)
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
            text=ensure_closing_thanks(prompt, language=language),
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
    "closing_thanks",
    "compose_prompt",
    "ends_with_thanks",
    "ensure_closing_thanks",
    "guess_language",
    "normalize_prompt_text",
    "prompt_guard_reason",
    "prompt_mode_enabled",
    "timeout_budget_s",
]
