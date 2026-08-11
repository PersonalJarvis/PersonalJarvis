"""Recaps that say what a pane ACHIEVED, not what it last printed.

:mod:`.recap` derives a pane's header label from the terminal replay with string
rules alone. That floor is honest but thin, and the way it fails is worth
stating plainly, because it is the reason this module exists: the last readable
row of a coding CLI is a *fragment*. A pane that had just written a design
document reported "without interrupting Claude's current work" — a true quote of
the screen, and a complete non-answer to the only question a grid of eight
terminals raises.

What the user wants in that header is the thing a coding CLI writes when you ask
it for a recap: the goal, where the work stands, and what is outstanding. That is
a summary of a session, and no amount of line-picking produces one — it needs a
model to read the pane and write a sentence.

So this module puts a summarizer above the floor, and treats the floor as the
fallback rather than as the product:

* **Cached, never on the read path.** ``recap_for`` returns whatever is already
  known — a model recap when one has arrived, the deterministic one until then —
  and returns it immediately. The model call happens in a background task.
* **Refreshed only on a material change, then SETTLED.** A young pane is
  re-summarized as output arrives (at most once per :data:`MIN_REFRESH_S`), but
  once a summary has read a substantial transcript the title stays put: only a
  new instruction or a status change rewrites it (:data:`STABLE_AFTER_LINES`).
  A navigation label that re-words itself while the user glances away cannot be
  recognized again — and a grid that nobody has touched costs nothing at all.
* **Bounded.** At most :data:`MAX_CONCURRENT` summaries are in flight across the
  whole app, each with its own timeout, and a pane whose summaries keep failing
  goes quiet for a while instead of retrying every poll.
* **Optional by construction** (§3). No key, no reachable provider — those
  paths end at the deterministic recap, which is exactly what the pane showed
  before this module existed. A provider that fails at CALL time (a depleted
  key's 429, a 402) first crosses to the next provider FAMILY in the resolver
  chain (AP-22), then to a connected coding subscription (the CLI in the panes
  is a credential too), and only then falls back to the deterministic floor.
  Nothing here is load-bearing, and nothing here may raise into a state read.

Above the model sits one more layer, and it outranks both: **what the user
wrote themselves**. No summarizer can know that a pane is "the branch I'm about
to demo" or "leave this one alone", so the pencil in the pane header writes
straight into :func:`pin`, the written text wins, and the background summarizer
stops spending requests on that pane until it is cleared again.

Every recap also carries a ``reason`` — which layer wrote it and, when it is the
deterministic floor, *why* the model did not. Each of those codes was already a
silent early return in :func:`refresh_soon`; naming them is what turns "this
recap is thin and nobody knows why" into a sentence in the recap card.

The recap is written in the INTERFACE language (``[ui].language``), not the
voice/chat reply language: it is a label sitting among the workspace's other
labels, and a German sentence between English buttons is the inconsistency, not
the fix.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from loguru import logger

from . import recap

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

#: Shortest gap between two model summaries of the SAME pane. A recap answers
#: "what is this pane up to", a question whose answer does not change every
#: second — and the header keeps showing the previous sentence in between, so
#: the cost of a longer gap is a slightly older sentence, not a blank.
MIN_REFRESH_S = 75.0

#: How many new readable rows count as "something happened". Below this a pane
#: is repainting its spinner or printing a progress counter, and a fresh
#: summary would spend a model call to write the same sentence again.
MIN_NEW_LINES = 5

#: Once a summary was written from a transcript at least this many readable
#: rows deep, the pane's PURPOSE was visible and the title is settled: output
#: growth alone no longer triggers a rewrite, only a new instruction or a
#: status change does. This is what every conversation UI the user knows does —
#: a title names the session and then stays put, while "what is happening right
#: now" lives in the activity badge next to it. A label that re-words itself
#: every 75 seconds cannot be recognized, which is a navigation label's one
#: job. Summaries written EARLIER than this (a pane summarized at its first
#: eight rows knows the banner, not the work) may still improve with output
#: until one of them has seen this much.
STABLE_AFTER_LINES = 60

#: How much readable output a pane needs before a summary is worth asking for.
#: A pane that has printed a banner and a prompt box has nothing to summarize;
#: the deterministic recap already says so accurately.
MIN_LINES_TO_SUMMARIZE = 8

#: Summaries in flight across the whole app. Eight panes coming into view at
#: once must not become eight simultaneous requests on the user's key — the rest
#: simply wait for the next poll, five seconds later.
MAX_CONCURRENT = 2

#: How much of the pane is handed to the model.  The original version kept only
#: the newest rows.  That preserves implementation activity but drops the
#: user's request at the top of a directly driven CLI session — exactly how an
#: outcome such as "make the workspace setup feel simple" became the process
#: report "consolidating the workspace launcher".  Keep both bookends within
#: the same total budget: the beginning identifies WHY; the end says WHERE IT
#: STANDS.  The noisy middle is the least valuable part of a glance label.
INPUT_HEAD_LINES = 48
INPUT_TAIL_LINES = 132
INPUT_CHARS = 9_000
INPUT_HEAD_CHARS = 2_400
INPUT_TAIL_CHARS = INPUT_CHARS - INPUT_HEAD_CHARS

#: How long one summary may take before the pane keeps its previous recap.
#: This bounds the WHOLE candidate walk, and the last candidate may be a
#: subscription CLI that needs a process spawn and several seconds of thinking
#: — 30 s was sized for API calls alone and would cut that path off exactly
#: when it is the only one left.
CALL_TIMEOUT_S = 45.0

#: The slice of that budget one subscription CLI call may take. Below the
#: outer cap on purpose: the CLI is only ever reached after the API families
#: failed, and whatever they burned must still leave the CLI a real chance.
SUBSCRIPTION_CLI_TIMEOUT_S = 35.0

#: How many API provider FAMILIES a single summary may try before giving up. A
#: depleted key fails at call time, after instantiating fine — the next try
#: must come from a different family (AP-22), and two spare families cover
#: every install that has anything to cross to without turning one recap into
#: a tour of every configured provider. A connected coding subscription rides
#: BEHIND this cap as the true last resort; it never competes for these slots.
MAX_PROVIDER_TRIES = 3

#: After this many consecutive failures a pane stops asking for a while. A
#: depleted key, an unreachable provider, or a model that refuses the format
#: fails for every pane and every poll; without this it would fail loudly and
#: repeatedly in the log for as long as the workspace is open.
FAILURES_BEFORE_QUIET = 3
QUIET_S = 600.0

#: This is a DISPLAY budget, not merely a transport cap.  In a normal grid the
#: call-sign and pane actions leave room for about 48 characters; a 90-character
#: model instruction merely guaranteed that CSS would hide the distinguishing
#: half.  Keep this in parity with the deterministic floor in :mod:`.recap`.
HEADLINE_CHARS = recap.HEADLINE_CHARS
DETAIL_CHARS = 640

#: A provider whose output budget was consumed by internal reasoning has been
#: observed returning ``DETAIL: The``.  Showing that fragment in the expanded
#: card is worse than repeating the useful headline, so a detail needs enough
#: substance to stand on its own.
MIN_DETAIL_CHARS = 24
MIN_DETAIL_WORDS = 5

#: Thinking-mandatory models consume their private reasoning from the same
#: output budget.  Most live panes still produced a complete title inside 400
#: tokens; the two that did not were obvious invalid fragments.  Start there,
#: then spend 800 only on an invalid answer.  This preserves title quality
#: without doubling every background recap's cost.
MODEL_OUTPUT_TOKENS = 400
MODEL_RETRY_OUTPUT_TOKENS = 800

#: How long the first line of an UNLABELLED answer may be and still be read as
#: the headline. A model that dropped its labels still wrote a label-length
#: line; a chatty CLI answers in a paragraph, and its first "line" is the whole
#: paragraph. Live 2026-08-11: with every API key dead, the recaps rode a
#: subscription CLI whose system contract is only prepended text, it answered
#: conversationally, and the forgiving unlabelled path shipped "Understood! I
#: see …" as the settled title of every pane. Checked against the RAW line,
#: before :func:`recap.condense` hides the evidence. Labelled answers are
#: exempt: "HEADLINE:" states the intent, and the condense cap does the rest.
UNLABELLED_HEADLINE_CHARS = 72

#: What the user may type into a recap of their own. The same two lengths, with
#: room to spare — a hand-written recap is checked here rather than silently
#: shortened, so nobody writes three sentences and gets one and a half back.
MAX_EDIT_HEADLINE = 200
MAX_EDIT_DETAIL = 2_000

#: ``source`` values on a recap — which layer wrote the sentence the user reads.
BY_MODEL = "model"
BY_RULES = "heuristic"
#: The user wrote it themselves, through the pencil in the pane header. It wins
#: over both of the above and is never overwritten by a background summary.
BY_USER = "user"

#: Why the recap on screen is the one on screen. Machine-readable on purpose:
#: the wording belongs to the UI, and the whole point of the field is that
#: "this line is thin" stops being a mystery. Rendered in the recap card.
WHY_PINNED = "pinned"  # you wrote it
WHY_SUMMARIZED = "summarized"  # a model read the pane and wrote it
WHY_DISABLED = "disabled"  # model recaps switched off in settings
WHY_NOT_STARTED = "not_started"  # nothing has run in this pane yet
WHY_WARMING = "warming"  # too little output so far to summarize
WHY_WORKING = "working"  # a summary is being written right now
WHY_QUEUED = "queued"  # too many summaries at once; next poll
WHY_UNAVAILABLE = "unavailable"  # nothing could summarize it — no key, no provider

#: What a pane reports when the install has no model to summarize with at all —
#: no key, no reachable provider. Not an error: it is the state §3 says every
#: install must survive, and the deterministic recap is the working answer.
NO_PROVIDER_NOTE = "No model is reachable to summarize this pane."

#: Anything in a provider's error text that looks like a credential. Some
#: providers put the API key in the request URL, and this string is rendered in
#: the recap card — where a screenshot would carry it straight out of the app.
_SECRET_RE = re.compile(r"(?i)(key|token|secret|password|authorization)=[^\s&\"']+")

#: Failure shapes worth a plain sentence. The raw provider error is a wall of
#: JSON — the screenshot-famous one opened with ``ClientError: 429 Too Many
#: Requests. {'message': '{\\n "error": …`` — and the recap card is read by
#: someone deciding what to DO, so the first words must say that. Matched
#: against ``TypeName: message`` so an empty-message ``TimeoutError`` still
#: lands on its sentence. First match wins; order the specific ones first.
_FAILURE_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b429\b|too many requests|rate.?limit|quota|depleted|exhausted"),
        "Its provider is out of credits or rate-limited.",
    ),
    (
        re.compile(r"(?i)\b40[13]\b|unauthorized|forbidden|api.?key|invalid.*credential"),
        "Its provider rejected the credential.",
    ),
    (
        re.compile(r"(?i)timeout|timed?[ -]?out"),
        "Its provider did not answer in time.",
    ),
)
_TITLE_WORD_RE = re.compile(r"\b[^\W\d_][\w'-]*\b", flags=re.UNICODE)
_SHELL_FRAGMENT_RE = re.compile(r"(?:^|\s)--?[a-zA-Z]\w*|[*?][./\\\w]")
_DETAIL_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")

# Which language the sentence is written in. Interface languages, so this table
# carries every locale [ui].language accepts and falls back to English for a
# value a newer build introduced (AP-16: a newer install's key must not break
# this one).
_LANGUAGE_NAMES = {"en": "English", "de": "German", "es": "Spanish"}

# What the summarizer is asked for. Written for the reader it actually has:
# somebody glancing across a grid of terminals who wants to know which pane
# needs them. The negative rules are the load-bearing half — a coding CLI's
# screen is mostly its own interface, and a summary that quotes the status bar
# is the failure this module was built to replace.
_SYSTEM = """\
You write a navigation label and recap for one terminal pane in a multi-agent \
coding workspace. The user is glancing across a grid and must recognize the \
ORIGINAL USER GOAL of this pane immediately, without reading its terminal.

You are given the instruction the pane was last sent and a readable replay of \
its terminal screen. That screen belongs to a coding CLI, so it also contains \
the CLI's own interface — banners, menus, spinners, key hints, a token \
counter, and the text on its input line, which is what the USER typed. Read \
past all of that to the work itself.

Answer with exactly two physical lines and nothing else:

HEADLINE: <about 5 words, HARD MAXIMUM 48 characters. A self-contained \
navigation label for the user's problem or desired outcome, normally \
"subject — result". No pane name, agent name, quotation marks, or trailing \
period.>
DETAIL: <exactly two complete sentences on this same physical line. State the \
original goal first, then what has been achieved, what is blocked, or the next \
step. Finish both sentences.>

Rules:
- THE FIRST TWO WORDS MUST IDENTIFY THIS PANE ON THEIR OWN. Narrow sidebars \
truncate the headline after roughly two words, so they are often ALL the user \
sees. Front-load the most specific noun phrase this session has — the feature, \
the bug, the subsystem — and push everything generic behind it. Test yourself: \
if the first two words could open the headline of a DIFFERENT session in the \
same workspace, they are too generic.
- Never OPEN with a bare activity name ("Code Review", "Bug Fix", "Refactor", \
"Research") or process narration such as "Investigating", "Implementing", \
"Working on", "Fixing", "Analyzing", "Reviewing", or an equivalent phrase in \
the requested language. Name WHAT is being reviewed or fixed first; the \
activity may follow after it if room remains.
- The headline answers "Which user problem or outcome is this pane for?", not \
"Which generic engineering activity is happening?" Use the user's vocabulary \
when the original request is visible; otherwise infer the outcome cautiously \
from the work.
- Aim for about 5 words. Shorter is acceptable only when the subject genuinely \
needs fewer; never pad, never exceed 48 characters.
- Keep file paths, class names, commands and implementation mechanisms OUT of \
the headline. Put useful technical evidence in DETAIL instead.
- Be concrete in DETAIL. "Working on the code" is worthless. Name supported \
errors, findings, decisions and results there.
- If the pane is waiting for the user — a permission prompt, a question, a \
choice between options — lead with "Needs input" (translated) in both lines. \
It is the one thing the user has to act on.
- Never quote the CLI's status bar, key hints, token or context counter, \
spinner text, or the contents of its input line.
- Say only what the screen supports. If it shows no real work yet, say that \
plainly instead of inventing some.
- Report, do not address the user, and do not offer help. No preamble, no \
markdown, no bullet points, no code fences.

Headline examples (poor -> useful). Note how the first two words alone \
already say which session this is:
- "Code Review — 6 findings" -> \
"Pane titles review — 6 findings"
- "Consolidating the workspace launcher into a single screen" -> \
"Workspace setup — one clear screen"
- "Fixing three bugs in the terminal notification system" -> \
"Terminal alerts — 3 reliability fixes"
- "Implementing terminal tab renaming based on workspace renaming" -> \
"Terminal tabs — rename without restart"
- "Investigating terminal pane geometry and whitespace" -> \
"Terminal panes — reclaim wasted space"\
"""


@dataclass(frozen=True, slots=True)
class SmartRecap:
    """One pane's recap, where the sentence came from, and why."""

    headline: str
    detail: str
    source: str = BY_RULES
    #: When the model wrote it. ``0.0`` for the deterministic floor, which is
    #: computed fresh on every read and therefore never stale.
    generated_at: float = 0.0
    #: Which of the ``WHY_*`` codes explains this recap. The UI turns it into a
    #: sentence — "this line comes from the screen because no model could be
    #: reached" is the answer a thin recap otherwise never gives.
    reason: str = ""
    #: The model that wrote it, when one did. Shown in the card so a recap that
    #: reads oddly can be traced to the provider that produced it.
    writer: str = ""
    #: What went wrong the last time a summary was attempted, scrubbed of
    #: anything credential-shaped. Empty whenever nothing has.
    note: str = ""


@dataclass(slots=True)
class _PaneState:
    """What is known about one pane's recap, and what it is allowed to do next."""

    headline: str = ""
    detail: str = ""
    generated_at: float = 0.0
    #: Readable row count / prompts sent / status / last manual submit at the
    #: last summary. The comparison against these decides whether anything
    #: worth re-reading has happened.
    lines_at: int = 0
    prompts_at: int = 0
    status_at: str = ""
    #: ``Terminal.last_submit_at`` as of the last summary. ``prompts_sent``
    #: only counts instructions JARVIS sent; a person typing a brand-new task
    #: into the pane and pressing Enter stamps only this — and a hand-typed
    #: instruction must reopen a settled title exactly like a sent one.
    submit_at: float = 0.0
    inflight: bool = False
    failures: int = 0
    quiet_until: float = 0.0
    #: The model behind the last summary, and the last failure's short form.
    writer: str = ""
    note: str = ""
    #: What the user wrote for this pane themselves. Set through :func:`pin`,
    #: wins over everything, and stops the background summarizer from spending
    #: a request to overwrite a label somebody chose on purpose.
    pinned_headline: str = ""
    pinned_detail: str = ""
    pinned_at: float = 0.0


_panes: dict[str, _PaneState] = {}
_tasks: set[asyncio.Task[None]] = set()
_inflight = 0


def describe_failure(exc: BaseException) -> str:
    """A failed summary in one short line the recap card can show.

    A recognized shape — a depleted or rate-limited key, a rejected credential,
    a timeout — leads with a plain sentence, because the card is read by someone
    deciding what to do, not by someone parsing provider JSON. The type is
    always there, because a bare provider message is often empty or a wall of
    JSON, and knowing it was an authentication error rather than a timeout is
    most of the answer. Whatever the provider said follows, truncated and with
    anything credential-shaped removed: this string ends up on screen, and some
    providers put the API key in the URL they echo back (AP-2/AP-12).
    """
    text = _SECRET_RE.sub(r"\1=…", str(exc or "").strip())
    kind = type(exc).__name__
    raw = f"{kind}: {text}" if text else kind
    for pattern, sentence in _FAILURE_HINTS:
        if pattern.search(raw):
            return recap.condense(f"{sentence} ({raw})", 200)
    return recap.condense(raw, 200)


def _state(key: str) -> _PaneState:
    entry = _panes.get(key)
    if entry is None:
        entry = _PaneState()
        _panes[key] = entry
    return entry


def _enabled() -> bool:
    """Is the model-written recap switched on for this install?

    Read live rather than cached, so turning it off in ``jarvis.toml`` takes
    effect on the next poll instead of on the next restart. A config that cannot
    be loaded answers "on": the deterministic floor is what runs anyway when
    nothing else works.
    """
    try:
        from jarvis.core.config import load_config

        return bool(getattr(load_config().agentic_ide, "smart_recaps", True))
    except Exception:  # noqa: BLE001 - a recap must never break a state read
        return True


def _ui_language() -> str:
    """The interface language the recap should be written in."""
    try:
        from jarvis.core.config import load_config

        return str(getattr(load_config().ui, "language", "en") or "en").strip().lower()
    except Exception:  # noqa: BLE001
        return "en"


def _material_change(term: Any, entry: _PaneState, line_count: int) -> bool:
    """Has enough happened in this pane to be worth re-summarizing?

    Deliberately NOT "has it printed more" once the title has settled: progress
    changes what a pane has achieved, never what it is for, and the header is a
    navigation label. After a summary written from a substantial transcript
    (:data:`STABLE_AFTER_LINES`), only the events that can change the pane's
    purpose reopen it — a new instruction (sent by Jarvis OR typed into the
    pane by hand), or a status change (the final summary of a pane that just
    exited). The manual Refresh button bypasses this whole gate, as it always
    has.
    """
    if entry.generated_at <= 0.0:
        return True
    if str(getattr(term, "status", "") or "") != entry.status_at:
        return True
    if int(getattr(term, "prompts_sent", 0) or 0) != entry.prompts_at:
        return True
    if float(getattr(term, "last_submit_at", 0.0) or 0.0) > entry.submit_at:
        return True
    if entry.lines_at >= STABLE_AFTER_LINES:
        return False
    return line_count - entry.lines_at >= MIN_NEW_LINES


def _worth_summarizing(term: Any, line_count: int) -> bool:
    """Would a model see anything here that the string rules cannot?

    A pane that has not started, could not start, or has printed almost nothing
    is described perfectly well by :mod:`.recap` — and describing it with a
    model would spend a request to say the same thing less reliably.
    """
    status = str(getattr(term, "status", "") or "pending")
    if status in {"pending", "error"}:
        return False
    return line_count >= MIN_LINES_TO_SUMMARIZE


def _why_no_summary(term: Any, entry: _PaneState | None, line_count: int | None) -> str:
    """Why this pane is showing the deterministic line rather than a summary.

    Every branch here was already a silent early return somewhere in
    :func:`refresh_soon`; naming them is the difference between a recap that
    reads thin for no visible reason and one that says "there is no reachable
    model to write a better one".
    """
    if not _enabled():
        return WHY_DISABLED
    if str(getattr(term, "status", "") or "pending") in {"pending", "error"}:
        return WHY_NOT_STARTED
    if line_count is not None and line_count < MIN_LINES_TO_SUMMARIZE:
        return WHY_WARMING
    if entry is not None:
        if entry.inflight:
            return WHY_WORKING
        if entry.quiet_until > time.time() or entry.failures:
            return WHY_UNAVAILABLE
    if _inflight >= MAX_CONCURRENT:
        return WHY_QUEUED
    return WHY_WORKING


def recap_for(term: Any, *, lines: Sequence[str] | None = None) -> SmartRecap:
    """This pane's best available recap, right now, without waiting for anything.

    Three layers, in the order the user's own intent outranks the machine's:
    what they wrote for this pane themselves, the model's sentence once one has
    been written, and the deterministic one until then — so a pane always has a
    header, from the moment it opens.
    """
    entry = _panes.get(str(getattr(term, "key", "") or ""))
    if entry is not None and entry.pinned_headline:
        return SmartRecap(
            headline=entry.pinned_headline,
            detail=entry.pinned_detail or entry.pinned_headline,
            source=BY_USER,
            generated_at=entry.pinned_at,
            reason=WHY_PINNED,
        )
    if entry is not None and entry.headline:
        return SmartRecap(
            headline=entry.headline,
            detail=entry.detail,
            source=BY_MODEL,
            generated_at=entry.generated_at,
            reason=WHY_SUMMARIZED,
            writer=entry.writer,
        )
    plain = recap.summarize(term, lines=None if lines is None else list(lines))
    return SmartRecap(
        headline=plain.headline,
        detail=plain.detail,
        source=BY_RULES,
        reason=_why_no_summary(term, entry, None if lines is None else len(lines)),
        note=entry.note if entry is not None else "",
    )


def refresh_soon(term: Any, *, lines: Sequence[str], folder: str = "") -> None:
    """Start a summary for ``term`` in the background if one is due.

    Called from the poll that renders the headers, and deliberately does nothing
    in the common case: a pane that was summarized a moment ago, has printed
    little since, is already being summarized, or is one of too many at once,
    simply keeps the sentence it has.

    Never raises. Called on every pane of every poll, so a failure here would be
    a failure of the workspace view.
    """
    global _inflight
    try:
        if not _enabled():
            return
        key = str(getattr(term, "key", "") or "")
        if not key:
            return
        rows = list(lines)
        if not _worth_summarizing(term, len(rows)):
            return
        entry = _state(key)
        # A recap the user wrote is the answer for this pane until they say
        # otherwise. Summarizing over it would spend a request to produce a
        # sentence nothing renders.
        if entry.pinned_headline:
            return
        now = time.time()
        if entry.inflight or now < entry.quiet_until:
            return
        if entry.generated_at > 0.0 and now - entry.generated_at < MIN_REFRESH_S:
            return
        if not _material_change(term, entry, len(rows)):
            return
        if _inflight >= MAX_CONCURRENT:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop — a synchronous caller (a CLI state dump, a test).
            # The deterministic recap is the whole answer there.
            return
        entry.inflight = True
        _inflight += 1
        task = loop.create_task(_run(term, key, rows, folder))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception as exc:  # noqa: BLE001 - a recap must never break a state read
        logger.debug("Agentic IDE recap: scheduling failed ({})", exc)


async def _run(term: Any, key: str, rows: list[str], folder: str) -> None:
    """One background summary, with every outcome landing in the pane's state."""
    global _inflight
    entry = _state(key)
    lines_at = len(rows)
    prompts_at = int(getattr(term, "prompts_sent", 0) or 0)
    status_at = str(getattr(term, "status", "") or "")
    submit_at = float(getattr(term, "last_submit_at", 0.0) or 0.0)
    try:
        summary = await asyncio.wait_for(
            summarize_with_model(term, rows, folder=folder), timeout=CALL_TIMEOUT_S
        )
    except Exception as exc:  # noqa: BLE001 - a failed recap is not a failed pane
        entry.failures += 1
        entry.note = describe_failure(exc)
        if entry.failures >= FAILURES_BEFORE_QUIET:
            entry.quiet_until = time.time() + QUIET_S
            logger.info(
                "Agentic IDE recap: {} failed {}x ({}) — deterministic recaps for the next {:.0f}s",
                key,
                entry.failures,
                type(exc).__name__,
                QUIET_S,
            )
        else:
            logger.debug("Agentic IDE recap: {} not summarized ({})", key, exc)
        return
    finally:
        entry.inflight = False
        _inflight = max(0, _inflight - 1)

    if summary is None:
        # Nothing reachable to summarize with. That is a state of the install,
        # not of this pane, so back off on the pane rather than retrying it on
        # every poll for as long as the workspace is open.
        entry.failures += 1
        entry.note = NO_PROVIDER_NOTE
        if entry.failures >= FAILURES_BEFORE_QUIET:
            entry.quiet_until = time.time() + QUIET_S
        return

    # A pin that arrived while the summary was in flight is the user's answer,
    # not a race to be won by whichever finished last.
    if entry.pinned_headline:
        return
    _store(
        entry,
        summary,
        lines_at=lines_at,
        prompts_at=prompts_at,
        status_at=status_at,
        submit_at=submit_at,
    )


def _store(
    entry: _PaneState,
    summary: SmartRecap,
    *,
    lines_at: int,
    prompts_at: int,
    status_at: str,
    submit_at: float = 0.0,
) -> None:
    """Keep a fresh summary as this pane's recap and clear the back-off."""
    entry.headline = summary.headline
    entry.detail = summary.detail
    entry.writer = summary.writer
    entry.generated_at = time.time()
    entry.lines_at = lines_at
    entry.prompts_at = prompts_at
    entry.status_at = status_at
    entry.submit_at = submit_at
    entry.failures = 0
    entry.quiet_until = 0.0
    entry.note = ""


def _resolve_brains() -> list[Any]:
    """The brains that could write the recap, best first. Empty when none can.

    Resolution goes through ``jarvis.brain.resolver`` so this module never grows
    its own opinion about providers (AP-21/AP-22): whatever keys the user has
    are what write the recap, and an install with none gets an empty list and
    the deterministic floor. More than one candidate comes back because the
    ordinary failure is CALL-time — a depleted key instantiates fine and only
    429s when asked to write — and the retry must cross to a different family.
    """
    try:
        from jarvis.brain.resolver import frontier_brain_candidates
        from jarvis.core.config import load_config

        config = load_config()
        candidates: list[Any] = []
        for brain in frontier_brain_candidates(config):
            candidates.append(brain)
            if len(candidates) >= MAX_PROVIDER_TRIES:
                break
    except Exception as exc:  # noqa: BLE001 - no brain is an answer, not an error
        logger.info("Agentic IDE recap: no brain reachable ({})", exc)
        return []
    # Every family above needs an API key, and the install this feature broke
    # on live had exactly one — depleted. A connected coding subscription is a
    # credential too (§3), and often the STRONGEST model the user has: the very
    # CLI running in the panes. It goes LAST because a CLI call costs a process
    # spawn and seconds where an API call costs milliseconds — it should write
    # the recap only when everything cheaper is dead.
    #
    # Appended UNCONDITIONALLY, not into a spare slot. It used to be skipped
    # whenever MAX_PROVIDER_TRIES API families were configured — which made it
    # unreachable on exactly the install it was built for: three configured but
    # broken keys occupied every slot, and the one credential provably working
    # (the CLI running in the panes) was never asked. Resolving it here only
    # instantiates the brain; the expensive CLI call happens solely when every
    # API family has already failed.
    subscription = _resolve_subscription(config)
    if subscription is not None:
        candidates.append(subscription)
    return candidates


def _resolve_subscription(config: Any) -> Any | None:
    """A connected subscription brain as the recap's last resort, or None."""
    try:
        from jarvis.brain.resolver import resolve_subscription_brain

        return resolve_subscription_brain(config, cli_timeout_s=SUBSCRIPTION_CLI_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - a broken CLI probe must not cost the recap
        logger.info("Agentic IDE recap: subscription fallback unavailable", exc_info=True)
        return None


def _bounded_rows(
    rows: Sequence[str], *, max_lines: int, max_chars: int, newest: bool = False
) -> list[str]:
    """One end of a transcript, bounded without dropping its nearest row.

    ``newest`` walks backwards so the very latest row survives when that end is
    wider than its character allowance, then restores chronological order for
    the model.  A single pathological JSON row is clipped rather than allowed
    to make the whole section empty.
    """
    source = list(rows)
    picked: list[str] = []
    used = 0
    iterable = reversed(source[-max_lines:]) if newest else iter(source[:max_lines])
    for raw in iterable:
        line = str(raw)
        remaining = max_chars - used
        if remaining <= 1:
            break
        cost = len(line) + 1
        if cost > remaining:
            if not picked:
                picked.append(f"{line[: max(1, remaining - 1)]}…")
            break
        picked.append(line)
        used += cost
    if newest:
        picked.reverse()
    return picked


def _screen_sections(rows: Sequence[str]) -> list[str]:
    """The transcript bookends that carry user intent and current state."""
    source = list(rows)
    total_chars = sum(len(str(line)) + 1 for line in source)
    total_lines = INPUT_HEAD_LINES + INPUT_TAIL_LINES
    if len(source) <= total_lines and total_chars <= INPUT_CHARS:
        return ["Its terminal transcript, oldest row first:\n<<<\n" + "\n".join(source) + "\n>>>"]

    head = _bounded_rows(
        source,
        max_lines=INPUT_HEAD_LINES,
        max_chars=INPUT_HEAD_CHARS,
    )
    # Never repeat rows from a short but extremely wide transcript in both
    # sections.  The line boundary is enough: character clipping happens only
    # inside the chosen edge row.
    tail_source = source[min(INPUT_HEAD_LINES, len(source)) :]
    tail = _bounded_rows(
        tail_source,
        max_lines=INPUT_TAIL_LINES,
        max_chars=INPUT_TAIL_CHARS,
        newest=True,
    )
    sections = [
        "The beginning of its terminal transcript, where the original user "
        "request often appears:\n<<<\n" + "\n".join(head) + "\n>>>"
    ]
    if tail:
        sections.append(
            "The newest part of its terminal transcript, after less useful "
            "middle rows were omitted:\n<<<\n" + "\n".join(tail) + "\n>>>"
        )
    return sections


def build_prompt(term: Any, rows: Sequence[str], *, folder: str = "") -> str:
    """What the summarizer is shown: the brief, the pane, and its screen.

    Public because it is the part worth asserting on — the tests pin that the
    instruction and both transcript bookends survive the budget, and that the
    less useful middle is what gets dropped when they do not.
    """
    instruction = str(getattr(term, "last_prompt", "") or "").strip()
    if len(instruction) > 1_500:
        instruction = f"{instruction[:1_500]}…"
    status = str(getattr(term, "status", "") or "pending")
    cli = getattr(term, "display_name", "") or getattr(term, "agent", "") or "unknown"
    header = [f"Coding CLI: {cli}", f"Pane status: {status}"]
    if folder:
        header.append(f"Working folder: {folder}")
    idle = recap.idle_phrase(term)
    if idle:
        header.append(f"Last printed something: {idle}")

    parts = ["\n".join(header)]
    if instruction:
        parts.append(f"The instruction this pane was last sent:\n<<<\n{instruction}\n>>>")
    else:
        parts.append(
            "This pane has not been sent an instruction from Jarvis; whatever it "
            "is doing was typed into it directly."
        )
    parts.extend(_screen_sections(rows))

    language = _LANGUAGE_NAMES.get(_ui_language(), _LANGUAGE_NAMES["en"])
    parts.append(f"Write both lines in {language}.")
    return "\n\n".join(parts)


def _valid_model_headline(headline: str) -> bool:
    """Whether a model line is a navigation label rather than CLI debris."""
    if len(headline.split()) < 2 or len(_TITLE_WORD_RE.findall(headline)) < 2:
        return False
    if '"' in headline or "`" in headline or _SHELL_FRAGMENT_RE.search(headline):
        return False
    # A navigation label neither exclaims nor trails off into a colon: those
    # are the shapes of a chat answer ("Hello! It looks like …", "Here you
    # go:"), and they are structural, so they hold in every interface language
    # where a phrase list would not.
    if "!" in headline or headline.endswith(("?", ":")):
        return False
    if headline.lstrip().startswith(("...", "./", "../", "/", "\\")):
        return False
    return all(
        headline.count(opening) == headline.count(closing)
        for opening, closing in (("(", ")"), ("[", "]"), ("{", "}"))
    )


def parse_answer(text: str, *, writer: str = "") -> SmartRecap | None:
    """Pull the two lines out of the model's answer, or None if neither is there.

    Deliberately forgiving. Models drop the labels, swap their order, wrap the
    whole thing in a code fence, or answer in one paragraph — and a recap is not
    worth failing over any of that. Two things are NOT accepted: an answer with
    no headline in it, because a blank header would look like a broken pane
    rather than a missing sentence — and a chat-shaped answer, because a CLI
    agent that reacted to the contract instead of following it opens with an
    acknowledgement, and "Understood! I see …" as a settled pane title is the
    live failure this guard exists for.
    """
    headline = ""
    detail_parts: list[str] = []
    seen_detail = False
    labelled = False
    for raw in str(text or "").splitlines():
        line = raw.strip().strip("`").strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("headline:"):
            headline = line.split(":", 1)[1].strip()
            labelled = True
            seen_detail = False
            continue
        if lowered.startswith("detail:"):
            detail_parts.append(line.split(":", 1)[1].strip())
            seen_detail = True
            continue
        if seen_detail:
            detail_parts.append(line)
        elif not headline:
            # An unlabelled answer: its first line is the headline, the rest is
            # the detail.
            headline = line
            seen_detail = True

    headline = headline.strip().strip('"').rstrip(".").strip()
    # An unlabelled first line is only trusted as a headline while it is
    # label-sized. A chatty answer is one paragraph, and condensing it into the
    # budget would dress a chat opener up as a title (see
    # :data:`UNLABELLED_HEADLINE_CHARS` for the live incident).
    if not labelled and len(headline) > UNLABELLED_HEADLINE_CHARS:
        return None
    headline = recap.condense(headline, HEADLINE_CHARS)
    detail = recap.condense(" ".join(detail_parts).strip().strip('"'), DETAIL_CHARS)
    if not _valid_model_headline(headline):
        return None
    detail_words = re.findall(r"\b[\w'-]+\b", detail, flags=re.UNICODE)
    sentence_ends = list(_DETAIL_SENTENCE_END_RE.finditer(detail))
    if len(detail) < MIN_DETAIL_CHARS or len(detail_words) < MIN_DETAIL_WORDS:
        detail = headline
    elif sentence_ends:
        # Keep every complete sentence and drop only the provider's guillotined
        # tail (`...where it stands. A`).  One complete sentence is more useful
        # than repeating the headline merely because the requested second one
        # ran out of budget.
        detail = detail[: sentence_ends[-1].end()].strip()
    else:
        detail = headline
    return SmartRecap(
        headline=headline,
        detail=detail or headline,
        source=BY_MODEL,
        generated_at=time.time(),
        reason=WHY_SUMMARIZED,
        writer=writer,
    )


async def summarize_with_model(
    term: Any, rows: Sequence[str], *, folder: str = ""
) -> SmartRecap | None:
    """Ask a model what this pane has been doing. None when nothing can answer.

    Walks the resolver's candidate chain rather than trusting its first pick:
    the first brain in it is whatever the user configured, and the ordinary way
    that brain fails is at call time — a depleted key instantiates fine and
    429s when asked to write. Each further try is a different provider FAMILY
    (AP-22), so the recap lands on whatever key the user actually has instead
    of dying with the depleted one. Only when every candidate has failed does
    this raise, and it raises the FIRST failure — the configured provider's,
    which is the one the user can act on.
    """
    brains = await asyncio.to_thread(_resolve_brains)
    if not brains:
        return None
    first_failure: Exception | None = None
    for brain in brains:
        try:
            return await _summarize_on(brain, term, rows, folder=folder)
        except Exception as exc:  # noqa: BLE001 - the next family is the handling
            first_failure = first_failure or exc
            logger.info(
                "Agentic IDE recap: {} could not write it ({}) — crossing to the next provider",
                getattr(brain, "model", "") or getattr(brain, "name", "") or type(brain).__name__,
                type(exc).__name__,
            )
    assert first_failure is not None  # noqa: S101 - the loop above ran at least once
    raise first_failure


async def _summarize_on(
    brain: Any, term: Any, rows: Sequence[str], *, folder: str = ""
) -> SmartRecap:
    """One summary on one brain. Raises whatever the provider raises."""
    from jarvis.core.protocols import BrainMessage, BrainRequest

    request = BrainRequest(
        messages=(BrainMessage(role="user", content=build_prompt(term, rows, folder=folder)),),
        system=_SYSTEM,
        # A recap is a reading of the screen, not an opinion about it: the same
        # pane should not be described differently on two consecutive polls.
        temperature=0.1,
        max_tokens=MODEL_OUTPUT_TOKENS,
        stream=True,
        # This is a small deterministic extraction task.  Thinking-by-default
        # Gemini models count internal reasoning against `max_tokens`; live, the
        # 400-token budget was consumed before the visible answer reached more
        # than `DETAIL: The`.  Every provider that has a reasoning control reads
        # this shared hint, and providers without one ignore it.
        reasoning_effort="none",
    )
    # Which model actually answered, for the card's footer. Read off whatever
    # the resolved brain exposes rather than from config: the chain may have
    # crossed to a different provider than the configured one (AP-22), and the
    # honest answer is the one that wrote the sentence.
    writer = str(getattr(brain, "model", "") or getattr(brain, "name", "") or "")
    answer = parse_answer(await _complete_text(brain, request), writer=writer)
    if answer is not None:
        return answer

    # A thinking-mandatory model can spend the small first budget before it has
    # emitted a usable title.  Retry only that measurable failure shape; valid
    # headlines never pay twice merely because their detail was truncated.
    retry = replace(request, max_tokens=MODEL_RETRY_OUTPUT_TOKENS)
    answer = parse_answer(await _complete_text(brain, retry), writer=writer)
    if answer is None:
        raise ValueError("The recap model returned no usable headline.")
    return answer


async def _complete_text(brain: Any, request: Any) -> str:
    """Aggregate one bounded recap request without exposing provider details."""
    chunks: list[str] = []
    async for delta in brain.complete(request):
        if delta.content:
            chunks.append(delta.content)
    return "".join(chunks)


def pin(key: str, headline: str, detail: str = "") -> SmartRecap:
    """Let the user write this pane's recap themselves.

    The one thing a derived label cannot know is what the *user* is using this
    pane for — "the branch I'm about to demo", "leave this one alone". So the
    pencil in the pane header writes straight into the pane's state, the written
    text wins over both the model and the rules, and the background summarizer
    leaves the pane alone until it is cleared again.

    Whitespace-only text clears the pin rather than pinning a blank header,
    which is what a user who selects all and deletes plainly means.
    """
    written = recap.condense(headline, MAX_EDIT_HEADLINE)
    if not written:
        unpin(key)
        return SmartRecap(headline="", detail="", source=BY_RULES, reason=WHY_PINNED)
    entry = _state(key)
    entry.pinned_headline = written
    entry.pinned_detail = recap.condense(detail, MAX_EDIT_DETAIL)
    entry.pinned_at = time.time()
    return SmartRecap(
        headline=entry.pinned_headline,
        detail=entry.pinned_detail or entry.pinned_headline,
        source=BY_USER,
        generated_at=entry.pinned_at,
        reason=WHY_PINNED,
    )


def unpin(key: str) -> None:
    """Hand this pane back to the automatic recap.

    Also clears the back-off, so "reset to automatic" on a pane whose summaries
    were failing an hour ago tries again now instead of sitting out the rest of
    the quiet window.
    """
    entry = _panes.get(key)
    if entry is None:
        return
    entry.pinned_headline = ""
    entry.pinned_detail = ""
    entry.pinned_at = 0.0
    entry.failures = 0
    entry.quiet_until = 0.0


def is_pinned(key: str) -> bool:
    """Did the user write this pane's recap themselves?"""
    entry = _panes.get(key)
    return bool(entry is not None and entry.pinned_headline)


async def summarize_now(term: Any, *, lines: Sequence[str], folder: str = "") -> SmartRecap:
    """Summarize this pane again right now, and wait for the answer.

    The one path in this module that is allowed to block: somebody pressed
    "Refresh" and is watching for the result, so every gate the background
    scheduler applies — the cooldown, the material-change test, the quiet window
    after repeated failures — is deliberately skipped. A hand-written recap is
    dropped first: asking for a fresh summary of a pane you labelled yourself is
    asking for the label back.

    Never raises. A provider that is missing, depleted or unreachable comes back
    as the deterministic recap plus a note saying exactly that, because "why is
    this line thin" is the question the whole feature exists to answer.
    """
    key = str(getattr(term, "key", "") or "")
    entry = _state(key) if key else _PaneState()
    unpin(key)
    rows = list(lines)
    if not _enabled():
        return _floor(term, rows, WHY_DISABLED)
    if not _worth_summarizing(term, len(rows)):
        status = str(getattr(term, "status", "") or "pending")
        why = WHY_NOT_STARTED if status in {"pending", "error"} else WHY_WARMING
        return _floor(term, rows, why)
    try:
        summary = await asyncio.wait_for(
            summarize_with_model(term, rows, folder=folder), timeout=CALL_TIMEOUT_S
        )
    except Exception as exc:  # noqa: BLE001 - a failed refresh is not a failed pane
        entry.note = describe_failure(exc)
        logger.info("Agentic IDE recap: {} could not be refreshed ({})", key or "?", exc)
        return _floor(term, rows, WHY_UNAVAILABLE, note=entry.note)
    if summary is None:
        entry.note = NO_PROVIDER_NOTE
        return _floor(term, rows, WHY_UNAVAILABLE, note=entry.note)
    _store(
        entry,
        summary,
        lines_at=len(rows),
        prompts_at=int(getattr(term, "prompts_sent", 0) or 0),
        status_at=str(getattr(term, "status", "") or ""),
        submit_at=float(getattr(term, "last_submit_at", 0.0) or 0.0),
    )
    return summary


def _floor(term: Any, rows: Sequence[str], why: str, *, note: str = "") -> SmartRecap:
    """The deterministic recap, labelled with why it is what came back."""
    plain = recap.summarize(term, lines=list(rows))
    return SmartRecap(
        headline=plain.headline,
        detail=plain.detail,
        source=BY_RULES,
        reason=why,
        note=note,
    )


def forget(key: str) -> None:
    """Drop what is remembered about one pane — it has been closed."""
    _panes.pop(key, None)


def reset_for_tests() -> None:
    """Clear all cached recaps and counters."""
    global _inflight
    _panes.clear()
    _tasks.clear()
    _inflight = 0


__all__ = [
    "BY_MODEL",
    "BY_RULES",
    "BY_USER",
    "DETAIL_CHARS",
    "HEADLINE_CHARS",
    "MAX_CONCURRENT",
    "MAX_EDIT_DETAIL",
    "MAX_EDIT_HEADLINE",
    "MIN_NEW_LINES",
    "MIN_REFRESH_S",
    "STABLE_AFTER_LINES",
    "UNLABELLED_HEADLINE_CHARS",
    "NO_PROVIDER_NOTE",
    "WHY_DISABLED",
    "WHY_NOT_STARTED",
    "WHY_PINNED",
    "WHY_QUEUED",
    "WHY_SUMMARIZED",
    "WHY_UNAVAILABLE",
    "WHY_WARMING",
    "WHY_WORKING",
    "SmartRecap",
    "build_prompt",
    "describe_failure",
    "forget",
    "is_pinned",
    "parse_answer",
    "pin",
    "recap_for",
    "refresh_soon",
    "reset_for_tests",
    "summarize_now",
    "summarize_with_model",
    "unpin",
]
