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
* **Refreshed only on a material change.** A pane is re-summarized when it has
  printed a few new lines, changed status, or been given a new instruction, and
  at most once per :data:`MIN_REFRESH_S`. A grid that nobody has touched costs
  nothing at all.
* **Bounded.** At most :data:`MAX_CONCURRENT` summaries are in flight across the
  whole app, each with its own timeout, and a pane whose summaries keep failing
  goes quiet for a while instead of retrying every poll.
* **Optional by construction** (§3). No key, no reachable provider, a provider
  that 429s — every one of those paths ends at the deterministic recap, which is
  exactly what the pane showed before this module existed. Nothing here is
  load-bearing, and nothing here may raise into a state read.

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
from dataclasses import dataclass
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

#: How much readable output a pane needs before a summary is worth asking for.
#: A pane that has printed a banner and a prompt box has nothing to summarize;
#: the deterministic recap already says so accurately.
MIN_LINES_TO_SUMMARIZE = 8

#: Summaries in flight across the whole app. Eight panes coming into view at
#: once must not become eight simultaneous requests on the user's key — the rest
#: simply wait for the next poll, five seconds later.
MAX_CONCURRENT = 2

#: How much of the pane is handed to the model, newest rows last. Enough to
#: cover the arc of a task (what was asked, what was tried, where it ended) and
#: far short of the whole 600-row scrollback.
INPUT_LINES = 180
INPUT_CHARS = 9_000

#: How long one summary may take before the pane keeps its previous recap. A
#: recap nobody is waiting for does not deserve a long leash.
CALL_TIMEOUT_S = 30.0

#: After this many consecutive failures a pane stops asking for a while. A
#: depleted key, an unreachable provider, or a model that refuses the format
#: fails for every pane and every poll; without this it would fail loudly and
#: repeatedly in the log for as long as the workspace is open.
FAILURES_BEFORE_QUIET = 3
QUIET_S = 600.0

#: Transport caps. The header is clipped by the pane's width anyway; the long
#: form is read in a card that wraps and scrolls, so its cap only has to bound
#: the payload. It used to be 420 and cut the last sentence off mid-word.
HEADLINE_CHARS = 120
DETAIL_CHARS = 640

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
You write the status line for one terminal pane in a multi-agent coding \
workspace. The user is glancing across a grid of panes and needs to know, \
without reading any of them, what THIS pane has been doing and where it stands.

You are given the instruction the pane was last sent and a readable replay of \
its terminal screen. That screen belongs to a coding CLI, so it also contains \
the CLI's own interface — banners, menus, spinners, key hints, a token \
counter, and the text on its input line, which is what the USER typed. Read \
past all of that to the work itself.

Answer with exactly two lines and nothing else:

HEADLINE: <at most 90 characters, and a COMPLETE thought inside that budget — \
this line is clipped by the pane's width, so front-load the part that \
identifies the work and never let it run past 90 into an ellipsis. What this \
pane is doing or has achieved, as a plain statement. No pane name, no agent \
name, no quotation marks, no trailing period.>
DETAIL: <three or four full sentences, and the place where the actual answer \
lives: what the goal was, what has been done so far, where the work stands \
now, and what is outstanding or what the next step is. Finish every sentence \
you start.>

Rules:
- Be concrete. "Working on the code" is worthless. Name the files, commands, \
errors, findings and decisions that are actually on the screen.
- If the pane is waiting for the user — a permission prompt, a question, a \
choice between options — say that first in both lines. It is the one thing \
the user has to act on.
- Never quote the CLI's status bar, key hints, token or context counter, \
spinner text, or the contents of its input line.
- Say only what the screen supports. If it shows no real work yet, say that \
plainly instead of inventing some.
- Report, do not address the user, and do not offer help. No preamble, no \
markdown, no bullet points, no code fences.\
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
    #: Readable row count / prompts sent / status at the last summary. The
    #: comparison against these decides whether anything worth re-reading has
    #: happened.
    lines_at: int = 0
    prompts_at: int = 0
    status_at: str = ""
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

    The type is always there, because a bare provider message is often empty or
    a wall of JSON, and knowing it was an authentication error rather than a
    timeout is most of the answer. Whatever the provider said follows, truncated
    and with anything credential-shaped removed: this string ends up on screen,
    and some providers put the API key in the URL they echo back (AP-2/AP-12).
    """
    text = _SECRET_RE.sub(r"\1=…", str(exc or "").strip())
    kind = type(exc).__name__
    return recap.condense(f"{kind}: {text}" if text else kind, 200)


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
    """Has enough happened in this pane to be worth re-summarizing?"""
    if entry.generated_at <= 0.0:
        return True
    if str(getattr(term, "status", "") or "") != entry.status_at:
        return True
    if int(getattr(term, "prompts_sent", 0) or 0) != entry.prompts_at:
        return True
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
    tail = None if lines is None else list(lines)[-recap.TAIL_LINES :]
    plain = recap.summarize(term, tail=tail)
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
                "Agentic IDE recap: {} failed {}x ({}) — deterministic recaps for "
                "the next {:.0f}s",
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
    _store(entry, summary, lines_at=lines_at, prompts_at=prompts_at, status_at=status_at)


def _store(
    entry: _PaneState,
    summary: SmartRecap,
    *,
    lines_at: int,
    prompts_at: int,
    status_at: str,
) -> None:
    """Keep a fresh summary as this pane's recap and clear the back-off."""
    entry.headline = summary.headline
    entry.detail = summary.detail
    entry.writer = summary.writer
    entry.generated_at = time.time()
    entry.lines_at = lines_at
    entry.prompts_at = prompts_at
    entry.status_at = status_at
    entry.failures = 0
    entry.quiet_until = 0.0
    entry.note = ""


def _resolve_brain():  # noqa: ANN202 - Brain | None, avoids an import cycle
    """A model that can write the recap, or None when the install has none.

    Resolution goes through ``jarvis.brain.resolver`` so this module never grows
    its own opinion about providers (AP-21/AP-22): whatever single key the user
    has is what writes the recap, and an install with none gets None and the
    deterministic floor.
    """
    try:
        from jarvis.brain.resolver import resolve_frontier_brain
        from jarvis.core.config import load_config

        return resolve_frontier_brain(load_config())
    except Exception as exc:  # noqa: BLE001 - no brain is an answer, not an error
        logger.info("Agentic IDE recap: no brain reachable ({})", exc)
        return None


def build_prompt(term: Any, rows: Sequence[str], *, folder: str = "") -> str:
    """What the summarizer is shown: the brief, the pane, and its screen.

    Public because it is the part worth asserting on — the tests pin that the
    instruction and the newest rows survive the budget, and that the oldest rows
    are what gets dropped when they do not.
    """
    tail: list[str] = []
    used = 0
    for line in reversed(list(rows)[-INPUT_LINES:]):
        used += len(line) + 1
        if used > INPUT_CHARS:
            break
        tail.append(line)
    tail.reverse()

    instruction = str(getattr(term, "last_prompt", "") or "").strip()
    if len(instruction) > 1_500:
        instruction = f"{instruction[:1_500]}…"
    status = str(getattr(term, "status", "") or "pending")
    header = [
        f"Coding CLI: {getattr(term, 'display_name', '') or getattr(term, 'agent', '') or 'unknown'}",
        f"Pane status: {status}",
    ]
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
    parts.append("Its terminal screen, oldest row first:\n<<<\n" + "\n".join(tail) + "\n>>>")

    language = _LANGUAGE_NAMES.get(_ui_language(), _LANGUAGE_NAMES["en"])
    parts.append(f"Write both lines in {language}.")
    return "\n\n".join(parts)


def parse_answer(text: str, *, writer: str = "") -> SmartRecap | None:
    """Pull the two lines out of the model's answer, or None if neither is there.

    Deliberately forgiving. Models drop the labels, swap their order, wrap the
    whole thing in a code fence, or answer in one paragraph — and a recap is not
    worth failing over any of that. The one thing that is NOT accepted is an
    answer with no headline in it, because a blank header would look like a
    broken pane rather than a missing sentence.
    """
    headline = ""
    detail_parts: list[str] = []
    seen_detail = False
    for raw in str(text or "").splitlines():
        line = raw.strip().strip("`").strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("headline:"):
            headline = line.split(":", 1)[1].strip()
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

    headline = recap.condense(headline.strip().strip('"').rstrip(".").strip(), HEADLINE_CHARS)
    detail = recap.condense(" ".join(detail_parts).strip().strip('"'), DETAIL_CHARS)
    if not headline:
        return None
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

    Raises only what the provider raises — the caller turns that into "keep the
    previous sentence", which is the whole error handling this feature needs.
    """
    brain = await asyncio.to_thread(_resolve_brain)
    if brain is None:
        return None
    from jarvis.core.protocols import BrainMessage, BrainRequest

    request = BrainRequest(
        messages=(BrainMessage(role="user", content=build_prompt(term, rows, folder=folder)),),
        system=_SYSTEM,
        # A recap is a reading of the screen, not an opinion about it: the same
        # pane should not be described differently on two consecutive polls.
        temperature=0.1,
        max_tokens=400,
        stream=True,
    )
    chunks: list[str] = []
    async for delta in brain.complete(request):
        if delta.content:
            chunks.append(delta.content)
    # Which model actually answered, for the card's footer. Read off whatever
    # the resolved brain exposes rather than from config: the chain may have
    # crossed to a different provider than the configured one (AP-22), and the
    # honest answer is the one that wrote the sentence.
    writer = str(getattr(brain, "model", "") or getattr(brain, "name", "") or "")
    return parse_answer("".join(chunks), writer=writer)


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
    )
    return summary


def _floor(term: Any, rows: Sequence[str], why: str, *, note: str = "") -> SmartRecap:
    """The deterministic recap, labelled with why it is what came back."""
    plain = recap.summarize(term, tail=list(rows)[-recap.TAIL_LINES :])
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
