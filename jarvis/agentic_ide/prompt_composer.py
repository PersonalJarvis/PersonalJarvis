"""Turn a spoken instruction into a prompt a coding agent can actually work on.

This is the part that makes the Agentic IDE worth having. Speaking at an agent
and speaking at a *transcript relay* are very different products: dictated
words arrive as one unpunctuated run of filler, pronouns and self-corrections
("kannst du mal schnell, also ähm, das Wake-Ding angucken ob da was kaputt
ist"), and pasting that into Claude Code produces a confused agent that starts
by asking what you meant. What the user asked for is that *Jarvis* does the
prompt engineering: it knows the repo, so it turns that sentence into a briefed
task with the relevant files already attached.

The prompt's SHAPE lives in ``prompt_blueprint`` and the guardrails it carries
are chosen by ``task_kind``; this module is the orchestration around them:
which files to offer, what to read of them, which model writes it, and what
happens when any of that is unavailable.

What the composed prompt contains, and why each part earns its place:

* **A briefed task in markdown**, not a rephrased sentence. Opus 5 and Fable 5
  both work best from a complete specification given up front, so the prompt
  states the task, why it matters, the files, the scope bound, and what done
  looks like — dropping any section it cannot ground rather than padding it.
* **File references in the agent's own syntax** (``@path``), inside the Key
  files list with a reason each. Both supported CLIs read ``@`` as "pull this
  file into context", which removes the agent's entire opening round of blind
  searching. Paths come from the workspace file index — never invented, and
  every one is verified to exist before it ships.
* **Real symbol names**, because the writer reads a bounded outline of those
  files first (``code_skeleton``). Without it the prompt can only say "the
  ranking logic" where it could say ``_fuse_ranked()``.
* **The repository's own house rules**, so ``## Done when`` cites the project's
  actual test command instead of inventing a plausible one.

Two-layer construction, because the product must work for a downloader with no
API key at all (§3):

1. **Composed** — a bounded call to the writer ``jarvis.agentic_ide.writer``
   chose. Deliberately NOT the full frontier chain: that chain ends in small,
   fast models so a core path never dies, and a prompt written by one of those
   looks fine while being materially worse. Here the output IS the product, so
   nothing below the quality tier writes a brief.
   A writer that accepts the job and dies inside it (a depleted key answering
   429, a CLI printing its own flag error, a hang) hands the brief to the next
   rung instead of ending the composition — one dead credential must not cost
   the feature while another provider is signed in and idle (AP-22). Every
   attempt shares one budget, and the user is told which writer took over.
2. **Deterministic fallback** — the same markdown skeleton filled by regex,
   used when no provider qualifies, or when every writer in the chain fails,
   times out, or comes back empty. It is *always* better than the raw
   transcript, so the feature never depends on a model being available.

The composer is honest about which layer produced the result (``composed_by``),
because the readback the user hears should not claim more than happened.

**On latency (deliberate trade, maintainer decision 2026-07-25).** An earlier
change routed this through the router-tier model the voice turn already held,
because a rewrite took 1-3 s there against 7-8 s on the deep tier. That
optimised the wrong axis: the maintainer's instruction was that these prompts
must be accurate about the codebase and "must not be dumb", and chose the
slower, better prompt explicitly. Reading file outlines costs more still. So
the composer takes its time and the bound below is generous — what must never
happen is a silent demotion to a weaker model, because nobody inspects a prompt
that looks fine.

**What was made faster, and what deliberately was not.** The model's own
writing is the wait, and shortening it means a shorter brief — the one thing
this module exists not to do. What WAS serial and did not need to be is the
preparation around it: resolving the writer (a config load plus a subscription
probe per candidate CLI) ran on the event loop and finished before the first
file was opened, and the file outlines were read before the house rules were.
All of that now overlaps and none of it runs on the loop, so the model call
starts as soon as the slowest single piece of preparation is done instead of
after their sum.

Measured again live 2026-08-12 (09:54, T1+T3): the writer call was 95%+ of a
72 s delivery, and two things inside it were pure overhead. First, every
subscription-CLI turn paid to start MCP servers, skills, and session
persistence it can never use — the CLI brain now switches those off for
structured turns (see ``claude_cli``), which cut the same real payload from
27.5 s to 16.4 s with the brief unchanged. Second, a slow writer was waited
out IN FULL before anyone else was allowed to start; attempts now overlap
after ``HEDGE_AFTER_S`` and the first valid brief wins, which bounds the tail
without demoting the model.

**On the silence.** The rest is perceived latency: for 10-27 s nothing at all
was said, so a working composer and a wedged one looked identical. The three
beats below (``start`` → ``thinking``/``drafting`` → ``ready``, and ``sent``
once the pane took it) are printed as they happen, and each one names the pane
it belongs to — with a fleet composing at once, an unattributed line is worse
than none. They are terminal lines, so they follow the English-artifact rule
like every other log line; the SPOKEN readback the user hears is a separate
surface and stays in the turn's resolved output language.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from .file_index import cached_index
from .session import MAX_PROMPT_CHARS, sanitize_prompt
from .task_kind import (
    KIND_IMPLEMENT,
    KIND_INVESTIGATE,
    KIND_NEUTRAL,
    KIND_QUESTION,
    KIND_REVIEW,
)

# The composer is not on the voice hot path the way an ack is, but the user is
# waiting to hear "sent to Kai". On timeout the deterministic prompt ships, so
# this bound costs quality, never delivery.
#
# Measured on the live chain: a fast router-tier model answers in 1-3 s, the
# deep tier took 7-8 s for a plain rewrite, and a full brief that reads five
# file outlines and describes the code took 16-22 s. A bound that expires
# mid-composition does not produce a faster good prompt, it produces the regex
# one — which 8 s did routinely and 20 s still did for the longest brief. The
# ceiling is therefore well clear of the measured worst case.
#
# Raised 45 → 90 s when the writer gained a subscription-CLI path (2026-07-26).
# Those brains pay a cold process start before they think at all: measured
# 10-12 s for a trivial prompt and 26.6 s for a real structured brief on the
# fastest model. At 45 s a slower model or a loaded machine would have expired
# routinely, and every expiry buys the regex prompt — never a faster good one.
COMPOSE_TIMEOUT_S = 90.0

# How many files may be attached. Enough to point the agent at a feature's
# surface; few enough that the agent's context is not flooded with guesses.
MAX_FILE_REFERENCES = 5

# The least time a second writer needs to be worth starting. A subscription CLI
# pays a 10-12 s cold process start before it thinks at all, so handing one a
# shorter window buys the deterministic prompt AND the wait.
_MIN_ATTEMPT_S = 20.0

# How long the chosen writer may stay silent before the next rung starts
# WRITING ALONGSIDE it — not instead of it: the first valid brief wins and the
# other attempt is cancelled. The serial shape this replaces is where the
# minute-plus deliveries came from (live 2026-08-12 09:54: a healthy but slow
# subscription call ran 71.4 s while the user waited; a hung one could burn 70 s
# before the rescue was even ALLOWED to start). Hedging bounds that tail at
# roughly this constant plus the second writer's own time.
#
# Above the healthy p90 on purpose: a brief on the lean CLI invocation measures
# 16-25 s end to end, so a hedge at 30 s fires only on genuinely slow calls and
# the double spend stays rare. The hedge crosses to the SAME rungs the rescue
# already uses on failure — quality-tier by construction, never a demotion.
HEDGE_AFTER_S = 30.0

# When the writer is a direct API call (the ``api`` and ``tool_model`` rungs),
# insurance starts earlier. Those paths have no process to cold-start: measured
# live 2026-08-12, a healthy API brief lands in 13-21 s, so the CLI-sized 30 s
# above would let an already-fast path sit through half a minute of silence
# before a second writer was allowed to begin. Still above the API path's
# healthy band, so the double spend stays as rare as it is for the CLI.
HEDGE_AFTER_API_S = 20.0

# Speech artefacts the deterministic layer removes. Matching *input vocabulary*
# in the supported locales — these are the words people actually say while
# thinking, not prose.
#
# ADMISSION RULE, inherited verbatim from ``jarvis.dictation.cleanup``: an
# entry qualifies only if it carries NO meaning in ANY locale this project
# serves. This regex runs language-BLIND — one pattern over German, English and
# Spanish alike — so an entry that is a content word in a language other than
# the speaker's is not a risk, it is a certainty. De-mined 2026-07-30 after the
# same defect surfaced in the voice lane's filler table:
#
#   "um"                German preposition. "Kümmere dich um den Bug" became
#                       "Kümmere dich den Bug".  # i18n-allow: transcript under test
#   "like"              English verb and preposition. "Make it like the other
#                       panel" became "Make it the other panel" — the same
#                       words, the opposite instruction.
#   "also"              English adverb ("also add the tests" → "add the tests"),
#                       and a German discourse marker its own docs exclude.
#   "kind of"/"sort of" "what kind of test" became "what test".
#   "halt"/"eben"       German content words ("halt das Layout stabil");
#   "ne"                colloquial German "eine", French negation particle;
#   "quasi"/"irgendwie" German adverbs that qualify the task;
#   "este"/"pues"/      Spanish demonstrative ("este archivo" = this file) and
#   "bueno"             two words the cleanup module names as excluded.
#
# What is left is either a pure hesitation sound or a multi-word discourse
# phrase that no locale reads as instruction. Anything added later must clear
# the same bar in all three locales, not only in the one it came from.
_FILLER_RE = re.compile(
    r"\b(?:"
    r"ähm|ähmm|äh|ehm|hmm|einfach\s+mal|gell|weißt\s+du|"
    r"verstehst\s+du|sozusagen|jetzt\s+mal|mal\s+eben|"
    r"uh|erm|you\s+know|i\s+mean|o\s+sea"
    r")\b[\s,]*",
    re.IGNORECASE,
)

_POLITENESS_PREFIX_RE = re.compile(
    r"^(?:"
    r"(?:kannst|k[oö]nntest|w[uü]rdest|willst|magst|k[oö]nnen)\s+(?:du|sie)\b\s*"
    r"|(?:could|can|would|will)\s+you\b\s*"
    r"|(?:puedes|podr[ií]as)\b\s*"
    r"|(?:bitte|please|por\s+favor|mal|kurz|schnell|eben|just|quickly)\b[\s,]*"
    r")+",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ComposedPrompt:
    """The prompt that will be typed, plus how it came to be."""

    text: str
    """What gets sent to the agent."""

    files: list[str] = field(default_factory=list)
    """Repo-relative paths referenced with ``@`` in ``text``."""

    composed_by: str = "fallback"
    """``llm`` when a provider wrote it, ``fallback`` when the regex layer did,
    ``raw`` when composition was switched off by the caller."""

    note: str = ""
    """Why the fallback was used, when it was. Empty on the happy path."""


# --------------------------------------------------------------- progress
# The stages of one composition, in the order they happen. Named constants
# because a sink may want to filter or style them, and a caller matching on a
# literal string is how a stage gets renamed and silently stops arriving.
STAGE_START = "start"
STAGE_THINKING = "thinking"
STAGE_DRAFTING = "drafting"
STAGE_RETRY = "retry"
STAGE_HEDGE = "hedge"
STAGE_READY = "ready"
STAGE_FALLBACK = "fallback"
STAGE_SENT = "sent"


@dataclass(frozen=True, slots=True)
class ComposeNotice:
    """One progress line about the prompt being written for one terminal."""

    stage: str
    """One of the ``STAGE_*`` constants above."""

    message: str
    """The finished English line, ready to print. Always names the terminal."""

    terminal: str = ""
    kind: str = ""
    """The detected task kind, so a sink can style a review differently from a
    build. Empty for notices that happen outside a composition (``sent``)."""


ProgressSink = Callable[[ComposeNotice], None]
"""Where notices go. Sync on purpose: a sink is called from inside a voice turn
and must not be able to await anything, let alone the pane it describes."""


def print_notice(notice: ComposeNotice) -> None:
    """Put one notice where the user is actually looking. Never raises.

    Standard output when there is one — that is the terminal a headless install
    and a ``--dev`` run are both watched from. Under the tray build there is no
    console at all (``pythonw.exe`` gives the process no stdout), so the line
    goes to the log file instead of being lost. Exactly one copy either way:
    the same sentence twice reads as a bug in the thing reporting progress.
    """
    logger.debug("Agentic IDE composer [{}] {}", notice.stage, notice.message)
    stream = getattr(sys, "stdout", None)
    if stream is None:
        logger.info("Agentic IDE composer: {}", notice.message)
        return
    try:
        stream.write(f"Jarvis: {notice.message}\n")
        stream.flush()
    except (OSError, ValueError, AttributeError, UnicodeError):
        # A closed, detached or non-decodable stream is not a failure of the
        # composition — the notice just goes somewhere that survives.
        logger.info("Agentic IDE composer: {}", notice.message)


def _emit(sink: ProgressSink | None, notice: ComposeNotice) -> None:
    """Deliver one notice. A broken sink costs the line, never the prompt."""
    try:
        (sink or print_notice)(notice)
    except Exception:  # noqa: BLE001 - progress is decoration, the brief is not
        logger.debug("Agentic IDE composer: a progress sink raised", exc_info=True)


# How much of the instruction a notice quotes back. Long enough to recognise
# the request, short enough that the line stays one line in a narrow terminal.
_MAX_SUBJECT_CHARS = 72


def _subject(instruction: str) -> str:
    """A short stand-in for what the user asked for, in their own words.

    Quoted rather than paraphrased: paraphrasing costs a model call, and this
    line has to appear BEFORE any of the work starts to be worth printing.
    """
    words = " ".join((instruction or "").split())
    if len(words) <= _MAX_SUBJECT_CHARS:
        return words
    return words[:_MAX_SUBJECT_CHARS].rsplit(" ", 1)[0].rstrip(",;:-") + "..."


def _variant(options: tuple[str, ...], seed: str) -> str:
    """One of ``options``, chosen by the instruction rather than at random.

    Two properties at once. Varied, because the same sentence on every turn
    stops being read after the second time and the user is then back to
    watching silence. Deterministic, because a test cannot pin a line that
    changes per run — and ``hash()`` is salted per process, so it is unusable
    here even though it looks like the obvious choice.
    """
    if not options:
        return ""
    return options[sum(map(ord, seed or "")) % len(options)]


_START_PHRASES: dict[str, tuple[str, ...]] = {
    KIND_IMPLEMENT: (
        'All right - writing {name} the build brief for "{subject}".',
        'On it: {name} gets the full spec for "{subject}".',
        'Right, briefing {name} to build "{subject}".',
    ),
    KIND_REVIEW: (
        'All right - writing {name} a review brief for "{subject}".',
        'On it: {name} gets asked to review "{subject}".',
        'Right, briefing {name} to go through "{subject}".',
    ),
    KIND_INVESTIGATE: (
        'All right - briefing {name} to dig into "{subject}".',
        'On it: {name} gets the investigation brief for "{subject}".',
        'Right, pointing {name} at "{subject}" to find the cause.',
    ),
    KIND_QUESTION: (
        'All right - writing {name} the question about "{subject}".',
        'On it: asking {name} about "{subject}".',
        'Right, putting {name}\'s question about "{subject}" together.',
    ),
    KIND_NEUTRAL: (
        'All right, writing the prompt for {name} - "{subject}".',
        'On it: putting {name}\'s prompt together - "{subject}".',
        'Right, {name}\'s prompt is being written - "{subject}".',
    ),
}

_THINKING_PHRASES = (
    "Thinking {name}'s brief through - {context}.",
    "Reading the code before {name} is briefed - {context}.",
    "Working out what {name} needs to know - {context}.",
)

_DRAFTING_PHRASES = (
    "{name}'s brief is coming in now.",
    "Writing {name}'s brief out.",
    "The brief for {name} is being written.",
)

_READY_PHRASES = (
    "{name} is briefed - {size} characters{files} in {secs}s. Sending it now.",
    "Brief written for {name}: {size} characters{files}, {secs}s. Handing it over.",
    "Done - {size} characters{files} for {name} in {secs}s. Sending.",
)


def _start_message(kind: str, instruction: str, terminal_name: str) -> str:
    """The opening line, matched to what the user actually asked for."""
    options = _START_PHRASES.get(kind) or _START_PHRASES[KIND_NEUTRAL]
    return _variant(options, instruction).format(name=terminal_name, subject=_subject(instruction))


def _thinking_message(
    instruction: str, terminal_name: str, *, outlines: int, writer_label: str
) -> str:
    """The line that stands while the model works — with what it was handed."""
    if outlines == 1:
        context = "one file outline to go on"
    elif outlines:
        context = f"{outlines} file outlines to go on"
    else:
        context = "no matching files, so your words alone"
    if writer_label:
        context = f"{context}, via {writer_label}"
    return _variant(_THINKING_PHRASES, instruction).format(name=terminal_name, context=context)


def _ready_message(
    instruction: str, terminal_name: str, *, size: int, files: int, seconds: float
) -> str:
    """What was written, before it is typed anywhere."""
    if files == 1:
        files_clause = ", one file attached"
    elif files:
        files_clause = f", {files} files attached"
    else:
        files_clause = ""
    return _variant(_READY_PHRASES, instruction).format(
        name=terminal_name, size=size, files=files_clause, secs=f"{seconds:.1f}"
    )


def _writer_label(brain: object) -> str:
    """The writing model's own name, when it has one worth printing."""
    name = str(getattr(brain, "name", "") or "").strip()
    return name[:40]


def _retry_message(terminal_name: str, *, reason: str, writer: str) -> str:
    """Said when one writer died and another is taking the brief over.

    Names both halves on purpose. A composition that silently takes twice as
    long looks wedged, and "which model wrote this brief" is a question the
    user is entitled to answer without reading a log file.
    """
    taking_over = f"{writer} is taking over" if writer else "another writer is taking over"
    return f"{terminal_name}'s writer dropped out ({reason}) - {taking_over}."


def _hedge_message(terminal_name: str, *, writer: str) -> str:
    """Said when a slow — not dead — writer gets a second one racing it.

    Deliberately NOT the retry wording: "dropped out" would be a lie about a
    writer that is still working, and the user should know two are now writing
    so whichever brief arrives is not a surprise.
    """
    second = writer or "another writer"
    return (
        f"{terminal_name}'s brief is taking long - {second} is drafting in "
        "parallel; the first good brief wins."
    )


def _brief_defect(composed: str) -> str:
    """Why ``composed`` is unusable as a brief, or ``""`` when it is fine.

    Three separate ways an answer arrives without a brief in it, all of which
    used to end the composition and now cost the WRITER rather than the user:

    * Nothing at all came back.
    * It is not a brief. A subscription CLI writes its own errors to stdout, so
      "the process answered" is not "a brief came back" — live 2026-07-26 one
      returned a one-line flag-validation error that shipped as the prompt.
    * It stops mid-sentence. Half a brief READS as a whole one, which is what
      makes it dangerous: the agent starts on an instruction whose second half
      was never written.
    """
    from . import prompt_blueprint as blueprint

    if not composed:
        return "composer returned nothing usable"
    if not blueprint.looks_like_brief(composed):
        return "composer output was not a brief"
    if blueprint.looks_truncated(composed):
        return "composer output was cut off"
    return ""


def announce_delivery(
    terminal_name: str,
    *,
    delivered: bool,
    submitted: bool | None = None,
    reason: str = "",
    sink: ProgressSink | None = None,
) -> None:
    """The closing line, once the pane has been handed the prompt — or has not.

    Lives here rather than at the send site so the whole sequence the user
    reads is worded in one place, and so it can never claim more than happened:
    "typed" and "started" are different states (a prompt ending on an ``@path``
    sits in the input box looking exactly like a running task), and the
    unconfirmed third state says so instead of picking the flattering reading.
    """
    if not delivered:
        message = f"{terminal_name} did not get it: {reason or 'it refused the prompt'}."
    elif submitted is True:
        message = f"Sent - {terminal_name} has the brief and is working on it."
    elif submitted is False:
        message = f"{terminal_name} is holding the brief in its input box - it never started."
    else:
        message = f"Sent to {terminal_name} - I could not confirm it started."
    _emit(sink, ComposeNotice(stage=STAGE_SENT, message=message, terminal=terminal_name))


def _clean_speech(text: str) -> str:
    """The instruction with speech artefacts and politeness scaffolding gone."""
    cleaned = _FILLER_RE.sub(" ", text or "")
    cleaned = _POLITENESS_PREFIX_RE.sub("", cleaned.strip())
    # Collapse the "und zwar dass er ..." construction into a plain imperative
    # opening; it is a spoken connector, never part of the task.
    cleaned = re.sub(
        r"^\s*(?:und\s+zwar\s+)?(?:dass|damit|that|que)\s+(?:er|sie|es|it|he|she|they)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return " ".join(cleaned.split())


def _existing(root: str, candidates: list[str]) -> list[str]:
    """Candidate paths that really exist under ``root``, in the given order.

    A hallucinated ``@path`` is worse than none: the agent opens it, fails, and
    spends a turn recovering. The index can also be stale (a file was renamed
    after the walk), so existence is re-checked here rather than assumed.
    """
    base = Path(root)
    out: list[str] = []
    for rel in candidates:
        # Reject anything trying to leave the workspace — a composed path is
        # model output on the happy path, and it only ever legitimately points
        # inside the folder the user opened.
        if rel.startswith(("/", "\\")) or ".." in rel.split("/") or ":" in rel[:3]:
            continue
        try:
            if (base / rel).is_file():
                out.append(rel)
        except OSError:
            continue
    return out


def _file_candidates(session, instruction: str, limit: int) -> list[str]:  # noqa: ANN001
    """Repo-relative files the instruction plausibly points at."""
    index = cached_index(session.folder)
    if index is None:
        # Still building (or never primed): ship without references rather than
        # make the user wait for a directory walk.
        return []
    return _existing(session.folder, index.suggest(instruction, limit=limit))


def _extract_referenced(text: str) -> list[str]:
    """``@path`` tokens in a composed prompt, in order of appearance."""
    seen: list[str] = []
    for match in re.finditer(r"@([\w./\\-]+)", text or ""):
        rel = match.group(1).replace("\\", "/")
        if rel not in seen:
            seen.append(rel)
    return seen


def _resolve_writer():  # noqa: ANN202 - (Brain | None, str), avoid an import cycle
    """The model that writes prompts and where it came from, or ``(None, "")``.

    Delegates to ``writer.resolve_writer`` so this decision lives in ONE place:
    the work splitter makes the same choice, and a user who moved briefs onto a
    subscription must not find the split still billing an API key.

    Neither rung of that order is the full frontier chain. That chain is built
    so a core path never dies and therefore ends in small, fast models; here the
    OUTPUT is the product, and a brief written by a mini model is worse in a way
    nobody notices — it reads perfectly well. Returning None lets the caller
    degrade to the deterministic prompt and say so.
    """
    from .writer import resolve_writer

    return resolve_writer(cli_timeout_s=COMPOSE_TIMEOUT_S)


def _rescue_writer(tried: Sequence[str]):  # noqa: ANN202 - (Brain | None, str)
    """The next writer to try after one accepted the job and failed inside it."""
    from .writer import resolve_rescue_writer

    return resolve_rescue_writer(cli_timeout_s=COMPOSE_TIMEOUT_S, exclude=tuple(tried))


# How much of the repository's agent instructions to carry. Enough for the
# headline conventions; far short of pasting a whole CLAUDE.md into every call.
_HOUSE_RULES_CHARS = 1200


def _house_rules(session) -> str:  # noqa: ANN001 - Session, avoid an import cycle
    """The repository's own conventions, so ``## Done when`` stays grounded.

    Without this the writer has to guess a test command, and a guessed one is
    exactly the kind of invented acceptance criterion the blueprint forbids.
    """
    from .code_skeleton import skeleton_for

    names = list(getattr(session.profile, "instruction_files", None) or [])
    for name in names:
        text = skeleton_for(session.folder, name, max_chars=_HOUSE_RULES_CHARS)
        if text:
            return f"From {name}:\n{text}"
    return ""


async def _llm_compose(
    *,
    brain,  # noqa: ANN001 - Brain, avoid an import cycle
    system_prompt: str,
    user_block: str,
    on_first_delta: Callable[[], None] | None = None,
) -> str:
    """One bounded call that writes the prompt.

    ``on_first_delta`` fires the moment the model's answer starts arriving. It
    is the only signal that separates "still waiting" from "it is writing", and
    the gap between them is the whole wait on a subscription CLI, which pays a
    10-12 s cold process start before it thinks at all.
    """
    from jarvis.core.protocols import BrainMessage, BrainRequest

    request = BrainRequest(
        messages=(BrainMessage(role="user", content=user_block),),
        system=system_prompt,
        # Deterministic rewriting, not creative writing: the prompt must carry
        # the user's intent, so temperature stays low.
        temperature=0.2,
        # Generous, because on a thinking model max_tokens covers the THINKING
        # as well as the answer. Measured: at 3000 with medium effort, a live
        # investigation brief was cut off mid-sentence ("...to find") and still
        # returned as a success. The brief itself never exceeds MAX_BODY_CHARS;
        # the headroom exists so the reasoning cannot eat the answer.
        max_tokens=8000,
        stream=True,
        # Turning a spoken sentence plus a set of file outlines into a briefed
        # task is judgement work, not transcription. The documentation is
        # explicit that thinking disabled performs worse than a modest effort at
        # comparable cost, and "none" is what made this a rephraser.
        reasoning_effort="medium",
    )
    chunks: list[str] = []
    async for delta in brain.complete(request):
        if delta.content:
            if not chunks and on_first_delta is not None:
                on_first_delta()
            chunks.append(delta.content)
    return "".join(chunks).strip()


async def _read_context(
    session,  # noqa: ANN001 - Session, avoid an import cycle
    candidates: list[str],
) -> tuple[dict[str, str], str]:
    """The file outlines and the repository's house rules, read at once.

    Both are disk IO on a path a voice turn is waiting on, and neither needs
    the other's result. They used to be awaited one after the other for no
    reason beyond the order they were written in.
    """
    from .code_skeleton import skeletons as read_skeletons

    outlines, house_rules = await asyncio.gather(
        asyncio.to_thread(read_skeletons, session.folder, candidates),
        asyncio.to_thread(_house_rules, session),
    )
    return outlines, house_rules


async def _compose_once(
    *,
    brain,  # noqa: ANN001 - Brain, avoid an import cycle
    session,  # noqa: ANN001 - Session, avoid an import cycle
    said: str,
    base_instruction: str,
    terminal_name: str,
    agent_display: str,
    candidates: list[str],
    kind: str,
    context,  # noqa: ANN001 - Awaitable[tuple[dict[str, str], str]]
    notify: Callable[[str, str], None],
    attachments: list,
    conversation: Sequence[tuple[str, str]],
) -> str:
    """Wait for the file material, then make the one writing call.

    ``context`` arrives as an awaitable rather than as finished data so the
    reading can already be running while the caller resolves the writer — and
    so the wait for it still sits inside ``COMPOSE_TIMEOUT_S``, which a value
    computed before the timeout was armed would not.
    """
    from . import prompt_blueprint as blueprint

    outlines, house_rules = await context

    notify(
        STAGE_THINKING,
        _thinking_message(
            base_instruction or said,
            terminal_name,
            outlines=len(outlines),
            writer_label=_writer_label(brain),
        ),
    )

    return await _llm_compose(
        brain=brain,
        system_prompt=blueprint.system_prompt(kind),
        user_block=blueprint.user_block(
            utterance=said,
            instruction=base_instruction,
            terminal_name=terminal_name,
            agent_display=agent_display,
            profile_lines=session.profile.summary_lines(),
            candidates=candidates,
            skeletons=outlines,
            house_rules=house_rules,
            attachments=attachments,
            conversation=conversation,
        ),
        on_first_delta=lambda: notify(
            STAGE_DRAFTING,
            _variant(_DRAFTING_PHRASES, base_instruction or said).format(name=terminal_name),
        ),
    )


def _strip_wrapper(text: str) -> str:
    """Remove a fenced block or surrounding quotes a model may have added."""
    stripped = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n?```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    if len(stripped) > 1 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
        stripped = stripped[1:-1].strip()
    return stripped


async def _resolved_writer(task: asyncio.Task) -> tuple[object | None, str]:
    """The writer the background probe found, or ``(None, "")``. Never raises.

    The probe loads the config and asks every subscription candidate whether it
    is signed in; a broken one there must cost the deterministic prompt, not
    the turn.
    """
    try:
        return await task
    except Exception:  # noqa: BLE001 - resolution never breaks a composition
        logger.info("Agentic IDE writer resolution failed", exc_info=True)
        return None, ""


def _discard(task: asyncio.Task) -> None:
    """Drop a preparation task whose result is no longer wanted, quietly."""
    task.cancel()
    task.add_done_callback(lambda done: done.cancelled() or done.exception())


async def compose(
    utterance: str,
    *,
    session,  # noqa: ANN001 - Session, avoid an import cycle
    terminal_name: str,
    agent_display: str = "the coding agent",
    instruction: str | None = None,
    use_llm: bool = True,
    max_files: int = MAX_FILE_REFERENCES,
    brain=None,  # noqa: ANN001 - Brain, avoid an import cycle
    on_progress: ProgressSink | None = None,
    attachments: Sequence | None = None,
    conversation: Sequence[tuple[str, str]] | None = None,
) -> ComposedPrompt:
    """Build the prompt for ``terminal_name`` out of what the user said.

    ``brain`` pins the writing model explicitly. Leave it None — the default
    resolves a quality-tier model and degrades openly when none is reachable.
    Passing a fast model here trades prompt accuracy for a few seconds, which
    is the trade the maintainer decided against on 2026-07-25.

    ``attachments`` are ``drop_analysis.DropAnalysis`` entries for files the
    user dropped alongside the instruction — a described screenshot, an
    extracted document. They reach BOTH layers: the writer folds them into the
    brief, and the deterministic prompt lists them, because the model that
    described the picture and the model that writes prose are separate
    providers and having one without the other is ordinary.

    ``conversation`` is the last few turns as ``(role, text)`` pairs. A spoken
    instruction points back into them constantly ("above all points two and
    three"), and the agent receives the brief alone — so without them the
    writer has nothing to resolve the reference against and forwards it
    verbatim into a prompt nobody can act on (live 2026-07-29). Only the model
    layer uses
    them: the deterministic prompt states what the user said and never claims
    to have understood more than the sentence.

    ``on_progress`` receives a ``ComposeNotice`` at each beat of the
    composition; left None they are printed (see ``print_notice``). A caller
    that wants only the deterministic layer passes ``use_llm=False`` and gets
    no notices at all — nothing is being written there, and a progress line
    about an instant operation is noise.

    Never raises: every failure path lands on the deterministic prompt, because
    "the agent got a rougher prompt" is a far better outcome than "your
    instruction vanished".
    """
    from . import prompt_blueprint as blueprint
    from .task_kind import classify

    said = (utterance or "").strip()
    base_instruction = _clean_speech(instruction or said) or (instruction or said).strip()
    attached = list(attachments or [])
    spoken_before = tuple(conversation or ())
    if not said:
        return ComposedPrompt(text="", composed_by="raw", note="empty instruction")

    subject = base_instruction or said
    kind = classify(base_instruction)

    def notify(stage: str, message: str) -> None:
        _emit(
            on_progress,
            ComposeNotice(stage=stage, message=message, terminal=terminal_name, kind=kind),
        )

    def _deterministic(
        composed_by: str, note: str = "", candidates: list[str] | None = None
    ) -> ComposedPrompt:
        chosen = (candidates or [])[:max_files]
        return ComposedPrompt(
            text=sanitize_prompt(
                blueprint.render_fallback(base_instruction, chosen, attached),
                keep_newlines=True,
            )[:MAX_PROMPT_CHARS],
            files=chosen,
            composed_by=composed_by,
            note=note,
        )

    if not use_llm:
        found = await asyncio.to_thread(_file_candidates, session, subject, max_files * 2)
        return _deterministic("raw", candidates=found)

    started = time.monotonic()
    notify(STAGE_START, _start_message(kind, subject, terminal_name))

    # The writer probe goes first and runs on its own: it is the one piece of
    # preparation that can take seconds (a config load plus a sign-in probe per
    # subscription CLI), it needs nothing from the disk work below, and running
    # it on the event loop stalled every other pane of a fan-out with it.
    writer_task: asyncio.Task | None = (
        asyncio.create_task(asyncio.to_thread(_resolve_writer)) if brain is None else None
    )

    try:
        candidates = await asyncio.to_thread(_file_candidates, session, subject, max_files * 2)
    except BaseException:
        # Cancelled — or broken — before the writer probe was consumed. The
        # probe task is OURS: abandoned here it would run on unobserved and
        # complain at teardown. Today's callers shield this whole call, but
        # that is their defence, not a property of this function.
        if writer_task is not None:
            _discard(writer_task)
        raise

    def degrade(note: str) -> ComposedPrompt:
        notify(STAGE_FALLBACK, f"{terminal_name} gets the plain brief instead: {note}.")
        return _deterministic("fallback", note, candidates=candidates)

    # Reading the outlines starts NOW, not once the writer is known — on a cold
    # subscription CLI the probe is the longer of the two.
    context_task = asyncio.create_task(_read_context(session, candidates))

    try:
        writer, writer_source = (
            (brain, "") if writer_task is None else await _resolved_writer(writer_task)
        )
    except BaseException:
        # Same property one step later: cancelling the wait on the writer
        # probe reaps the probe itself (we are awaiting it), but the context
        # read is a sibling task nobody else knows about.
        _discard(context_task)
        raise
    if writer is None:
        # An unhonourable PIN still degrades here rather than quietly landing on
        # a provider the user did not choose: plain and honest beats polished
        # and quietly worse. Surviving a chosen writer is a different question,
        # answered by the loop below.
        _discard(context_task)
        return degrade("no quality-tier provider reachable")

    # A writer that accepts the job and then dies inside it — a depleted key
    # answering 429, a CLI printing its own flag error to stdout, a provider
    # hanging — used to end the composition on the raw spoken sentence while a
    # second, signed-in provider sat idle. Measured on the dev box 2026-08-02:
    # every composition for days. That is the single-provider brick of AP-22,
    # so a failed attempt crosses to the next rung.
    #
    # Attempts run CONCURRENTLY, in two situations. On a FAILURE the next rung
    # starts at once, as before. On a STALL (``HEDGE_AFTER_S`` with no answer)
    # the next rung starts WHILE the first is still writing — the serial shape
    # this replaces made the user wait out a slow writer in full before anyone
    # else was allowed to begin, which is where the minute-plus deliveries came
    # from. The first valid brief wins and every other attempt is cancelled.
    #
    # All attempts share ONE budget. The user is waiting through the whole
    # sequence, and three full timeouts in a row is not a rescue — it is the
    # same fallback three times slower.
    deadline = started + COMPOSE_TIMEOUT_S
    tried: list[str] = [writer_source] if writer_source else []
    attempts: dict[asyncio.Task[str], str] = {}
    # A pinned brain is the caller deciding — no hedge, no rescue, as before.
    may_substitute = writer_task is not None
    hedged = not may_substitute
    composed = ""
    reason = ""

    def _spawn_attempt(brn, source: str) -> bool:  # noqa: ANN001 - Brain
        """Start one writer on the brief, unless the budget says otherwise."""
        remaining = deadline - time.monotonic()
        if remaining < _MIN_ATTEMPT_S:
            return False
        task = asyncio.create_task(
            asyncio.wait_for(
                _compose_once(
                    brain=brn,
                    session=session,
                    said=said,
                    base_instruction=base_instruction,
                    terminal_name=terminal_name,
                    agent_display=agent_display,
                    candidates=candidates,
                    kind=kind,
                    context=context_task,
                    notify=notify,
                    attachments=attached,
                    conversation=spoken_before,
                ),
                timeout=remaining,
            )
        )
        attempts[task] = source
        return True

    def _cancel_attempts() -> None:
        for leftover in attempts:
            _discard(leftover)
        attempts.clear()

    if not _spawn_attempt(writer, writer_source):
        _discard(context_task)
        return degrade(f"composer timed out after {COMPOSE_TIMEOUT_S:g}s")

    try:
        while attempts and not composed:
            now = time.monotonic()
            if now >= deadline:
                break
            wait_s = deadline - now
            if not hedged:
                # The threshold follows the PRIMARY writer's family: a
                # subscription CLI pays a cold process start a direct API call
                # never does, so "suspiciously slow" starts earlier there.
                # Read at loop time, not hoisted — tests pin these constants.
                hedge_after_s = (
                    HEDGE_AFTER_S
                    if writer_source.startswith("subscription")
                    else HEDGE_AFTER_API_S
                )
                wait_s = min(wait_s, max(started + hedge_after_s - now, 0.0))
            done, _pending = await asyncio.wait(
                set(attempts), timeout=wait_s, return_when=asyncio.FIRST_COMPLETED
            )

            if not done and not hedged:
                # Nobody failed — the writer is just slow. The next rung starts
                # alongside it rather than after it; at most once per brief.
                hedged = True
                if deadline - time.monotonic() >= _MIN_ATTEMPT_S:
                    nxt, nxt_source = await asyncio.to_thread(_rescue_writer, tried)
                    if nxt is not None and _spawn_attempt(nxt, nxt_source):
                        tried.append(nxt_source)
                        notify(
                            STAGE_HEDGE,
                            _hedge_message(terminal_name, writer=_writer_label(nxt)),
                        )
                continue

            for task in done:
                source = attempts.pop(task)
                try:
                    candidate = _strip_wrapper(task.result())
                except (TimeoutError, asyncio.CancelledError):
                    reason = f"composer timed out after {COMPOSE_TIMEOUT_S:g}s"
                except Exception as exc:  # noqa: BLE001 - any failure crosses over
                    logger.info(
                        "Agentic IDE prompt composer failed on {}: {}",
                        source or "the pinned writer",
                        exc,
                    )
                    reason = f"composer unavailable ({type(exc).__name__})"
                else:
                    defect = _brief_defect(candidate)
                    if not defect:
                        composed = candidate
                        break
                    reason = defect
                    logger.info(
                        "Agentic IDE prompt composer: {} from {} ({} chars)",
                        defect,
                        source or "the pinned writer",
                        len(candidate),
                    )

            if composed or attempts:
                # Won, or a sibling attempt is still writing and may still win.
                continue
            if not may_substitute:
                # The caller pinned this model explicitly. Substituting another
                # one is not ours to decide.
                break
            if deadline - time.monotonic() < _MIN_ATTEMPT_S:
                break
            nxt, nxt_source = await asyncio.to_thread(_rescue_writer, tried)
            if nxt is None or not _spawn_attempt(nxt, nxt_source):
                break
            tried.append(nxt_source)
            notify(
                STAGE_RETRY,
                _retry_message(terminal_name, reason=reason, writer=_writer_label(nxt)),
            )
    except BaseException:
        # The caller died mid-composition (voice hangup, dropped client), or
        # something unforeseen escaped the loop. Either way the attempt tasks
        # are OURS — without this they would keep spending providers on a
        # brief nobody can deliver.
        _cancel_attempts()
        _discard(context_task)
        raise

    _cancel_attempts()
    if not composed:
        _discard(context_task)
        return degrade(reason or f"composer timed out after {COMPOSE_TIMEOUT_S:g}s")

    # Keep only the references that survive an existence check — the model may
    # echo a candidate that was renamed, or invent one outright. A dead @path
    # costs the agent a turn; the bare path costs it nothing.
    written = _extract_referenced(composed)
    referenced = _existing(session.folder, written)
    for bad in (r for r in written if r not in referenced):
        composed = composed.replace(f"@{bad}", bad)

    if blueprint.ends_on_reference(composed):
        # A trailing @path or /command holds the completion popup open, and the
        # Enter that follows picks a suggestion instead of submitting.
        composed = f"{composed}\n\nStart there; search further if needed."

    final = sanitize_prompt(composed, keep_newlines=True)[:MAX_PROMPT_CHARS]
    if not final:
        return degrade("composer returned nothing usable")

    notify(
        STAGE_READY,
        _ready_message(
            subject,
            terminal_name,
            size=len(final),
            files=len(referenced),
            seconds=time.monotonic() - started,
        ),
    )
    return ComposedPrompt(text=final, files=referenced, composed_by="llm")


__all__ = [
    "COMPOSE_TIMEOUT_S",
    "HEDGE_AFTER_API_S",
    "HEDGE_AFTER_S",
    "MAX_FILE_REFERENCES",
    "STAGE_DRAFTING",
    "STAGE_FALLBACK",
    "STAGE_HEDGE",
    "STAGE_READY",
    "STAGE_RETRY",
    "STAGE_SENT",
    "STAGE_START",
    "STAGE_THINKING",
    "ComposeNotice",
    "ComposedPrompt",
    "ProgressSink",
    "announce_delivery",
    "compose",
    "print_notice",
]
