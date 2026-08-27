"""Prompt Mode — a dictation comes out as the prompt the user asked for.

What it is
----------
The user holds the dictation key and says what they want, in their own words —
half sentences, false starts, several small tasks in one breath. What lands in
the field is the finished prompt: what they described, worked up into
something an AI model can act on, in the language they spoke.

The instruction, and why it is short
------------------------------------
This module is one instruction and one worked example. That is a deliberate
correction of v2-v4, which grew into eleven thousand characters of rules — no
markdown, no headings, plain paragraphs, keep the register, six of these,
seven of those — and each rule narrowed what the writer was allowed to
produce. The maintainer's own reference run is the argument (2026-08-27): he
said "kannst du mir bitte ein Prompt schreiben" to Gemini, with no rules at
all, and got back a structured, professional prompt with a role, a workflow
and quality criteria. That is the output this feature is for, and every rule
that forbade markdown or headings was forbidding exactly it.

So: say what the job is, show one run of it, and let the model write. The
example carries what a page of rules used to, because a shown run outweighs a
written rule — measured on this very prompt, where a rule about language lost
to an example in the other language.

What is guarded and what is not
-------------------------------
Not guarded: shape. A prompt may be markdown, may carry headings, lists,
sections and a role line — that is what a good prompt looks like, and v3's
``markdown`` rejection was this feature refusing its own purpose.

Guarded: fidelity and damage. The message must not be empty, must not stop
mid-sentence, must carry every literal the user spoke and every protected
spelling, and must not collapse to a fraction of what was said. All
deterministic, all fail-open — a rejection delivers the transcript to the
ordinary passes, and the raw words are never lost.

Then two deterministic finishes: typographic debris a fast model leaves in
identifiers is normalised, and a courtesy sign-off is cut, because what the
user pastes into a model should end on its last piece of substance.

Where it runs
-------------
On the polish pass's own chain (:mod:`jarvis.dictation.polish_client`), asking
for the family's stronger fast model — the one the translate pass uses
(``gemini-3.7-flash`` on Google, ``gpt-oss-120b`` on Groq and Cerebras,
``gpt-5.4-mini`` on OpenAI). One call, no scratchpad, no second pass: writing
a prompt is one job, and a model that is good at it does it in one go.

Ships OFF. It changes WHAT the text says, on purpose, and sends the words to a
cloud model on most installs (the polish pass's on-device rule still applies:
a local recognizer keeps the chain local).

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

from jarvis.agentic_ide.prompt_blueprint import looks_truncated
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
#: v2-v4 are one arc and one mistake. v2 replaced v1's markdown skeleton with
#: plain text in the spoken language; v3 added the shape of a courteous
#: request and a closing line of thanks; v4 added a written analysis, a draft
#: and a read-back, to make the writer spend the 5-7 s the maintainer had
#: asked for. Each revision answered a real defect and each one added rules,
#: until the instruction was eleven thousand characters that told a capable
#: model mostly what it was NOT allowed to write.
#:
#: v5 throws that away. The maintainer's reference run settled it
#: (2026-08-27): a dictation handed to Gemini with no instruction beyond
#: "write me a prompt" came back as a structured professional prompt — role,
#: workflow, quality criteria, markdown — which is precisely the output v3's
#: ``markdown`` guard rejected and v3's rules forbade. So the instruction is
#: one paragraph and one worked example (that same run), the shape guards are
#: gone, and the analysis/draft/read-back ceremony is gone with them: it made
#: the pass slower and dearer to enforce a narrowness nobody wanted.
#:
#: What survives from v4 is what was never about shape: the fidelity guards
#: (a literal the user spoke must be in the prompt, the prompt must not
#: collapse to a fraction of the transcript) and the sign-off trim.
PROMPT_MODE_PROMPT_VERSION: Final[int] = 5

#: The status a successful Prompt Mode delivery reports on the history row.
#: Lives in ``POLISH_STATUSES`` (the shared vocabulary) — restated here as a
#: named constant so call sites compare against a name, not a string literal.
STATUS_PROMPTED: Final[str] = "prompted"

# One call, one prompt written. The bound is a safety net, not a target: the
# fast chain answers in 2-6 s depending on the provider's hardware, and 12 s
# leaves room for a long dictation on a slower family without ever cutting an
# answer off mid-prompt. 4 s is the floor (below it the pass only ever times
# out, which is worse than being off) and 20 s the ceiling before this stops
# being dictation.
_DEFAULT_TIMEOUT_MS: Final[int] = 12_000
_MIN_TIMEOUT_MS: Final[int] = 4_000
_MAX_TIMEOUT_MS: Final[int] = 20_000

# A written-out prompt with a role, sections and format rules is long — the
# reference run is about 700 tokens — and a cut-off one is rejected as
# truncated and costs the whole pass. Temperature 0: the job is well defined.
_MAX_OUTPUT_TOKENS: Final[int] = 3_000
_TEMPERATURE: Final[float] = 0.0

# The one piece of structure asked of the model, and the only reason for it:
# a model handed this job likes to introduce it ("Hier ist ein System-Prompt
# für deinen Auto-Prompting-Modus" — the reference run's own first line). The
# tag is a fence around the deliverable, not a scratchpad and not a schema.
_PROMPT_OPEN_RE: Final[re.Pattern[str]] = re.compile(r"<\s*prompt\s*>", re.IGNORECASE)
_PROMPT_CLOSE_RE: Final[re.Pattern[str]] = re.compile(r"<\s*/\s*prompt\s*>", re.IGNORECASE)
_OUR_TAGS_RE: Final[re.Pattern[str]] = re.compile(r"<\s*/?\s*prompt\s*>", re.IGNORECASE)

# A courtesy sign-off, in the two shapes measured live on 2026-08-27: a line
# of its own ("...\n\nDanke!") and a sentence tacked onto the last paragraph
# ("...lasse ihn unverändert. Danke dir."). Same vocabulary, two anchors — the
# second needs a sentence boundary in front of it, which also keeps a message
# that IS a thank-you from being erased. The reader is a model; a courtesy
# line is noise it has to step over.
_SIGN_OFF_WORDS: Final[str] = (
    r"(?:thanks?|thank\s+you|dank(?:e|esch[öo]n)?|gracias|merci|grazie|obrigad[oa]|"
    r"bedankt|tak|tack|takk|kiitos|dzi[eę]kuj[eę]|d[ěe]kuji|спасибо|ありがとう|谢谢|"
    r"regards|cheers|gr[üu][ßs]e)"
)
_SIGN_OFF_LINE_RE: Final[re.Pattern[str]] = re.compile(
    rf"^\W{{0,3}}(?:\w+\s+){{0,4}}?{_SIGN_OFF_WORDS}[\w\s,!.…]{{0,40}}$",
    re.IGNORECASE,
)
_SIGN_OFF_SENTENCE_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?<=[.!?…])[ \t]+(?:\w+\s+){{0,3}}?{_SIGN_OFF_WORDS}[\w\s,]{{0,30}}[.!…]*\s*$",
    re.IGNORECASE,
)

# The instruction. Short on purpose — see the module docstring and the version
# note above. The example is the maintainer's own reference run: his dictated
# transcript, and the prompt Gemini wrote for it when asked for nothing but a
# prompt. It is the specification; everything above it is context for reading
# it.
_SYSTEM_PROMPT: Final[str] = """\
A user is dictating to Jarvis Voice. They want every transcript of theirs \
turned automatically into a finished, professional prompt for an AI model - \
that is the whole feature, and you are the part of it that writes.

Below is one transcript. Write the prompt it is asking for.

Take what they said as the brief: their goal, the context and constraints \
they stated, every separate task they mentioned. Everything comes from the \
transcript - a file, name, number, quoted string or error message they said \
goes in exactly as spoken, one they did not say does not exist, and a gap \
they left is left open rather than guessed at. Drop the filler sounds, the \
false starts and the self-corrections; keep the corrected version.

WRITE IT IN THE LANGUAGE THE USER SPOKE. These instructions are in English \
and that says nothing about your answer: a German transcript gets a German \
prompt, a Spanish one a Spanish prompt, and that includes the headings and \
the section names, not just the sentences between them. A transcript that \
mixes languages keeps the mix.

Structure it the way a good prompt is structured and use whatever serves that \
- a role and system context, the task and the outcome it has to reach, the \
context and guardrails, the output format, headings, lists, markdown, \
sections. Be concrete rather than adjectival: "a markdown table with the \
columns X, Y, Z", never "make it good".

Put the prompt, and nothing else, between <prompt> and </prompt>. No \
introduction of your own in front of it, no comment after it, and no closing \
line of thanks - it ends on its last piece of substance.

Here is one run of this job, from a dictated transcript to the prompt written \
for it.

Transcript: "Hallo, kannst du mir bitte ein Prompt schreiben? Und zwar möchte \
ich, dass du es hier verbesserst. Ich möchte, dass du bitte für mich einen \
Deep Dive machst und mir dabei hilfst, den Modus, welchen wir eingebaut haben \
in Jarvis Voice, diesen Auto-Prompt-Modus zu verbessern, dass die Prompts \
besser geschrieben werden, in einem professionelleren Stil. Ich möchte, dass. \
Sich das Modell, und zwar das Tool-Modell, welches damit verbunden wird, mehr \
darüber nachdenkt und den Prompt nicht zu schnell schreibt. Mit einer \
Nachdenkzeit von 5 bis 7 Sekunden, dass wirklich ein guter Prompt geschrieben \
wird, aber auch nicht zu lange nachgedacht wird. Es muss halt eine gute \
Mischung darin entstehen. Diese Prompt sollen in einem guten und \
professionellen Modell professionellen Stil geschrieben sein."
Answer:
<prompt>
Du bist der spezialisierte Prompt-Engineering-Core für Jarvis Voice. Deine \
Aufgabe ist es, rohe, unpräzise oder gesprochene Nutzeranweisungen in \
hochgradig strukturierte, professionelle und präzise Prompts zu \
transformieren.

Bevor du die finale Antwort ausgibst, MUSST du einen strukturierten \
Denkprozess (Reasoning Step) durchlaufen, um den Prompt systematisch zu \
optimieren.

### ARBEITSABLAUF (2-Phasen-Prozess)

Führe bei jeder Anfrage ausnahmslos folgende zwei Schritte aus:

#### PHASE 1: <thinking> (Tiefenanalyse & Konstruktion)
Nutze diese Phase, um den Kontext systematisch zu durchdenken:
1. **Intenterkennung:** Was ist das eigentliche Kernziel des Nutzers hinter \
der gesprochenen Eingabe?
2. **Kontext- & Rollenbestimmung:** Welche Rolle/Expertise passt am besten zu \
diesem Ziel?
3. **Strukturelle Mängel & Lücken:** Welche Parameter fehlen (Format, \
Tonalität, Einschränkungen, Zielgruppe)?
4. **Optimierungsstrategie:** Wie muss der Prompt formuliert werden, um \
Fehlinterpretationen zu minimieren und maximale Detailtiefe zu sichern?

#### PHASE 2: <optimized_prompt> (Finale Ausgabe)
Gib ausschließlich den fertigen, sofort einsatzbereiten Prompt aus. Dieser \
muss folgenden Aufbau haben:
- **Rolle / Systemkontext:** Definition der Persona und des Wissensbereichs.
- **Aufgabe & Zielsetzung:** Glasklare Beschreibung des Outputs.
- **Kontext & Richtlinien:** Wichtige Rahmenbedingungen, Einschränkungen und \
Do's/Don'ts.
- **Formatierungs- & Strukturvorgaben:** Exakte Vorgabe von \
Markdown-Elementen, Tabellen oder Codeblöcken.
- **Input-Variable / Platzhalter:** Wo die tatsächlichen Nutzerdaten \
eingefügt werden.

### QUALITÄTSKRITERIEN FÜR DEN FINALEN PROMPT

* **Präzision statt Floskeln:** Keine vagen Adjektive ("mache es gut"), \
sondern konkrete Handlungsanweisungen ("nutze eine Markdown-Tabelle mit \
Spalten X, Y, Z").
* **Professioneller Tonfall:** Formuliere den Prompt in einem klaren, \
autoritativen und direktiven Ton.
* **Kein Meta-Gerede:** Schreibe in Phase 2 keine Einleitung wie "Hier ist \
dein Prompt" - starte direkt mit dem Prompt-Inhalt.
</prompt>\
"""

# Typographic debris a fast model writes into identifiers and paths. Measured
# live 2026-08-27: gpt-oss on the fast chain joined "Jarvis‑Bar" and
# "Self‑Hosted" with U+2011 (non-breaking hyphen), which a grep for
# "Self-Hosted" in the receiving model never matches. The whole hyphen block
# maps to the hyphen-minus when it sits inside a word; a spaced dash stays a
# spaced dash so an aside written as " – " is not turned into " - " by force.
_TIGHT_HYPHEN_RE: Final[re.Pattern[str]] = re.compile(r"(?<=\w)[‐‑‒–—](?=\w)")
_LOOSE_HYPHEN_RE: Final[re.Pattern[str]] = re.compile(r"[‐‑]")
_SPECIAL_SPACE_RE: Final[re.Pattern[str]] = re.compile(r"[    ​]")
_TRAILING_WS_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANK_RUN_RE: Final[re.Pattern[str]] = re.compile(r"\n{3,}")

# --- Fidelity guards -------------------------------------------------------
#
# The polish pass measures how far the words moved. Here they are supposed to
# move — a prompt is not a tidied transcript — so what is measured instead is
# what got LOST. Two things, both deterministic, both cheap, both fail-open:
#
#   * a literal the user spoke that the prompt does not carry, and
#   * a prompt so much shorter than the transcript that context was thinned.
#
# Neither has an opinion about shape or style. That is the model's judgement,
# and the instruction above is where it is made.

# What counts as a literal worth carrying: an identifier or path
# (``auth_handler``, ``src/app.ts``, ``JarvisBar``), a version or number of
# two digits or more, and anything the user put in quotes. Deliberately NOT
# ordinary words: the prompt is a rewrite, and a guard over every noun would
# reject every honest one.
_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"""
    \b(?:
        [A-Za-z0-9_]+(?:[./\\-][A-Za-z0-9_]+)+        # a.b, src/app.ts, foo-bar
      | [A-Za-z]+_[A-Za-z0-9_]+                       # snake_case
      | [a-z]+[A-Z][A-Za-z0-9]*                       # camelCase
      | [A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*               # PascalCase
    )\b
    """,
    re.VERBOSE,
)
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"\b\d{2,}(?:[.,]\d+)*\b")
# Double quotes only. A lone ``'`` is an apostrophe far more often than a
# quotation mark in a transcript ("it's", "don't"), and treating one as an
# opening quote turns the rest of the sentence into a "literal" the prompt is
# then required to reproduce word for word.
_QUOTED_RE: Final[re.Pattern[str]] = re.compile("[\"“„«]([^\"“”„«»\n]{2,60})[\"”»]")

#: How many of the transcript's literals may go missing before the prompt is
#: rejected. Not zero: a recognizer writes the same spoken name two ways in
#: one breath ("JarvisBar", "Jarvis Bar"), and rejecting on one of those costs
#: a good prompt. One is a slip; a quarter of them is a writer that stopped
#: reading.
_MAX_LOST_LITERALS: Final[int] = 1
_LOST_LITERAL_SHARE: Final[float] = 0.25

#: How far the prompt may shrink against the transcript before it counts as
#: thinned, and the shortest transcript this is measured on at all. A written
#: prompt is normally LONGER than what was dictated, so this only ever fires
#: on a writer that summarised instead of writing. Short transcripts are
#: exempt: "fix the typo on the login page" is a complete brief at eight
#: words and its prompt may be shorter still.
_MIN_LENGTH_SHARE: Final[float] = 0.40
_MIN_MEASURED_WORDS: Final[int] = 30

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


def extract_prompt_block(answer: str) -> str:
    """The prompt out of the answer, tolerant of a ragged one.

    The LAST opening tag wins and the closing one is optional: a model that
    names the tag while introducing itself would otherwise hand over its
    introduction, and one that ran out of budget still wrote a usable prompt
    up to where it stopped — the truncation guard is what decides whether it
    is usable. An answer with no tag at all is taken whole, on the assumption
    that the model simply wrote the prompt directly, which is a fine thing to
    have done.

    Whatever comes out is stripped of any of our own tags echoed inside it.
    """
    body = str(answer or "").strip()
    if not body:
        return ""
    opens = list(_PROMPT_OPEN_RE.finditer(body))
    if not opens:
        return _OUR_TAGS_RE.sub("", body).strip()
    rest = body[opens[-1].end() :]
    close = _PROMPT_CLOSE_RE.search(rest)
    return _OUR_TAGS_RE.sub("", rest[: close.start()] if close else rest).strip()


def normalize_prompt_text(text: str) -> str:
    """Undo the typographic debris a fast model leaves behind.

    Deterministic and lossless in meaning: a non-breaking hyphen inside a word
    becomes the hyphen-minus the user's keyboard produces, special spaces
    become spaces, trailing spaces on a line go, and runs of blank lines
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


def ends_with_sign_off(text: str) -> bool:
    """Whether *text* closes on a courtesy sign-off, as a line or a sentence."""
    body = str(text or "").strip()
    if not body:
        return False
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if lines and _SIGN_OFF_LINE_RE.match(lines[-1]):
        return True
    return bool(_SIGN_OFF_SENTENCE_RE.search(body))


def strip_closing_sign_off(text: str) -> str:
    """*text* with a trailing courtesy line or sentence removed.

    The instruction asks for a prompt that ends on its substance; this is the
    guarantee. Loops, because a model that writes "Danke!" under "Viele Grüße"
    has written two, and because cutting the last line can expose another
    underneath it. Never strips the whole thing: a line of thanks that is all
    there is stays, and a sentence is only cut when a finished sentence stands
    in front of it.

    Runs AFTER the truncation guard, for the reason that guard exists — a
    prompt that stopped mid-sentence must be caught as damage, not tidied.
    """
    body = str(text or "").strip()
    while True:
        trimmed = _SIGN_OFF_SENTENCE_RE.sub("", body).strip()
        if trimmed != body and trimmed:
            body = trimmed
            continue
        lines = body.splitlines()
        kept = [line.strip() for line in lines if line.strip()]
        if len(kept) < 2 or not _SIGN_OFF_LINE_RE.match(kept[-1]):
            return body
        cut = len(lines)
        while cut > 0 and not lines[cut - 1].strip():
            cut -= 1
        body = "\n".join(lines[: cut - 1]).strip()


def transcript_literals(text: str) -> list[str]:
    """The literals in *text* a faithful prompt has to carry.

    Identifiers and paths, numbers of two digits or more, and quoted spans.
    Case is preserved in what comes back (the caller folds it) so a log line
    can name the thing that went missing the way the user said it.
    """
    body = str(text or "")
    found: dict[str, str] = {}
    for pattern in (_IDENTIFIER_RE, _NUMBER_RE):
        for match in pattern.finditer(body):
            token = match.group(0)
            found.setdefault(token.casefold(), token)
    for match in _QUOTED_RE.finditer(body):
        token = match.group(1).strip()
        if token:
            found.setdefault(token.casefold(), token)
    return list(found.values())


def lost_literals(raw: str, prompt: str) -> list[str]:
    """Which of the transcript's literals the prompt does not carry."""
    target = str(prompt or "").casefold()
    return [token for token in transcript_literals(raw) if token.casefold() not in target]


def looks_thinned(raw: str, prompt: str) -> bool:
    """True when the prompt is too short to still hold what was dictated.

    A word-count proxy, and only over transcripts long enough for the ratio to
    mean anything. A written prompt is normally longer than the dictation, so
    this fires on a writer that summarised rather than wrote — without having
    any opinion about wording.
    """
    spoken = len(_WORD_RE.findall(str(raw or "")))
    if spoken < _MIN_MEASURED_WORDS:
        return False
    written = len(_WORD_RE.findall(str(prompt or "")))
    return written < spoken * _MIN_LENGTH_SHARE


def prompt_guard_reason(raw: str, prompt: str, *, protected: Sequence[str] = ()) -> str:
    """Why *prompt* must not be delivered, or ``""`` when it may.

    Damage and fidelity, never shape. A prompt is allowed to be markdown, to
    carry headings, sections and a role line — v3 rejected exactly that as
    ``markdown``, which was this feature refusing its own purpose. What is
    still worth catching:

    * ``empty`` — nothing came back worth pasting.
    * ``truncated`` — stopped mid-sentence. Reads as complete, is not.
    * ``lost_protected_term`` — a spelling the user protected was in the
      transcript and is not in the prompt. The dictionary exists because the
      recognizer gets these wrong; a writer that drops one has undone that.
    * ``dropped_detail`` — file names, identifiers, numbers or quoted strings
      the user spoke are missing. The model is sent looking for something the
      user had already named.
    * ``dropped_context`` — under 40 % of the spoken word count. A written
      prompt is normally longer than the dictation; this short means the
      writer summarised instead.
    """
    body = (prompt or "").strip()
    if not body:
        return "empty"
    if looks_truncated(body):
        return "truncated"
    source = (raw or "").casefold()
    target = body.casefold()
    for term in protected or ():
        needle = str(term or "").strip().casefold()
        if needle and needle in source and needle not in target:
            return "lost_protected_term"
    literals = transcript_literals(raw)
    if literals:
        missing = lost_literals(raw, body)
        allowed = max(_MAX_LOST_LITERALS, int(len(literals) * _LOST_LITERAL_SHARE))
        if len(missing) > allowed:
            return "dropped_detail"
    if looks_thinned(raw, body):
        return "dropped_context"
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
    """Turn *raw* into the prompt it is asking for. Never raises, never loses text.

    Returns a :class:`~jarvis.dictation.polish.PolishOutcome` so the delivery
    path, the history row and the settings screen read Prompt Mode through the
    same fields they already read the polish pass through. ``text`` is the
    prompt on :data:`STATUS_PROMPTED` and the untouched *raw* on every other
    status — the caller then falls through to whatever it would have done
    without this feature, which is the polish pass.

    ``language`` is the resolved dictation language. The prompt is written in
    the transcript's own language by the instruction, so nothing here depends
    on it; the parameter stays because every caller passes it and because the
    language belongs in the log line when a delivery has to be explained.

    The model is the polish pass's own chain (``[dictation].polish_provider``
    and its ``auto`` order), asked for the family's stronger fast model — the
    one the translate pass uses — because writing a prompt asks more of a
    model than punctuating a sentence.
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

        prompt = normalize_prompt_text(extract_prompt_block(answer))
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

        log.debug(
            "dictation prompt mode wrote a %d-word prompt on %r (language %r).",
            len(_WORD_RE.findall(prompt)),
            attempt.provider,
            language or "auto",
        )
        return _result(
            STATUS_PROMPTED,
            provider=attempt.provider,
            model=attempt.model,
            text=strip_closing_sign_off(prompt),
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
        # for it: writing a prompt is a rewrite, not a repunctuation.
        translating=True,
    )


__all__ = [
    "PROMPT_MODE_PROMPT_VERSION",
    "STATUS_PROMPTED",
    "build_prompt_mode_prompt",
    "compose_prompt",
    "ends_with_sign_off",
    "extract_prompt_block",
    "looks_thinned",
    "lost_literals",
    "normalize_prompt_text",
    "prompt_guard_reason",
    "prompt_mode_enabled",
    "strip_closing_sign_off",
    "timeout_budget_s",
    "transcript_literals",
]
