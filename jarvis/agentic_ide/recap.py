"""A one-glance answer to "what is this pane actually doing?".

Every pane header used to say the same two things: the call-sign the user chose
and the CLI behind it ("Mika — CLAUDE CODE"). Both are constant for the life of
the pane, so a grid of eight terminals answered the one question a grid of eight
terminals raises — *which of these is doing the thing I care about?* — with
eight identical labels. The information was on screen the whole time, buried in
a full-screen TUI the user has to read pane by pane.

This module derives a short recap from state the workspace already keeps, and
deliberately nothing else:

* what the pane was last **asked** to do (``last_prompt`` — the instruction that
  went in through the prompt bar or by voice),
* what it last **printed** (the readable transcript, already replayed off a
  terminal screen by :mod:`.transcript`),
* whether it is running, starting, dead, or broken, and how long ago it spoke.

No model call, no network, no background task — a recap is computed when someone
asks for one, from a buffer that is filled anyway. That is a hard constraint,
not an optimisation: this text is rendered for every pane of every workspace on
a poll, and an LLM round-trip per pane per few seconds would cost more than the
feature is worth and would fail entirely for a user without a spare key (§3).

Two forms come out, because the header and the tooltip answer differently sized
questions:

* :attr:`Recap.headline` — ONE clause for a label that will be truncated by the
  pane's width. Front-loaded with the freshest signal, since the beginning is
  the part that survives the ellipsis.
* :attr:`Recap.detail` — one or two plain sentences for the hover tooltip: what
  it was asked to do, and what has happened since.

Honest limits: a coding CLI's last printed line is a heuristic for "what it is
doing", not a summary of the session, and a TUI that redraws a status bar as its
bottom row will surface that instead of its last real step. So the wording never
claims more than it knows — it quotes what the pane printed rather than
asserting what the agent "is working on".
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

# How many transcript rows the recap looks back over. Enough to step past a
# status bar and a blank frame row, small enough that the scan is free.
TAIL_LINES = 14

# The header has room for roughly one short navigation label once the pane's
# call-sign and controls have taken their share.  The old 120-character cap
# delegated the real limit to CSS, which routinely hid the words that finally
# identified the user's goal.  Forty-eight characters keeps the WHOLE label in
# a normal pane and forces both the model and the deterministic floor to put the
# differentiating subject first.
HEADLINE_CHARS = 48
# The long form is read in a card that wraps and scrolls, not in a one-line
# tooltip — so the cap is here to bound the payload, not to make the text fit.
# It used to be 280, which cut the second sentence off mid-word in exactly the
# place the user had opened the card to read.
DETAIL_CHARS = 480
# How much of the instruction is quoted in the long form. A prompt is often a
# whole brief; this is the part of it that identifies the task.
TASK_CHARS = 200
# The same job in the header.  It shares the exact cap: a fallback must not be
# allowed to overflow merely because its source was an instruction rather than
# a transcript row.
HEADLINE_TASK_CHARS = HEADLINE_CHARS

# Glyphs an agent TUI draws in front of its input line. A row starting with one
# is the prompt box, not something the agent did.
_INPUT_MARKERS = ("❯", ">", "›")

# Fragments that mark a row as the TUI's own chrome — key hints, the context
# meter, the permission-mode banner. They are drawn on every frame, so without
# this the recap of every busy pane would read "? for shortcuts".
_CHROME_FRAGMENTS = (
    "for shortcuts",
    "to interrupt",
    "to cancel",
    "ctrl+c",
    "ctrl-c",
    "shift+tab",
    "esc to",
    "press enter",
    "auto-compact",
    "context left",
    "accept edits on",
    "bypassing permissions",
    "plan mode on",
    # The collapsed permission-mode footer ("auto mode on · 1 shell") carries
    # none of the key hints above, so it needs its own entry — observed live as
    # the headline of a busy pane.
    "auto mode on",
)


def _chrome_fragments() -> tuple[str, ...]:
    """The shared set, plus whatever the registered CLIs add to it.

    The list above is what every TUI observed so far draws, and it is shared
    rather than per-CLI on purpose — these phrases are near-universal, and
    splitting them per product would mean a new entry starting with none of
    them and its recaps reading "? for shortcuts". An entry adds only its own
    peculiarities.
    """
    try:
        from jarvis.workspace import agents as workspace_agents

        extra = tuple(
            fragment.lower()
            for agent in workspace_agents.coding_agents()
            for fragment in agent.chrome_fragments
        )
    except Exception:  # noqa: BLE001 - a recap must never fail on decoration
        extra = ()
    return (*_CHROME_FRAGMENTS, *extra)


# Leading decoration a TUI puts in front of a real line: bullets, tree glyphs,
# check marks, spinner frames. Stripped so the recap starts on a word. The
# allowed openers are kept out of the class deliberately — a path, a quote or an
# @reference is content, not decoration.
_LEAD_RE = re.compile(r'^[^\w"\'(\[@/#]+')

# Markdown a composed brief opens a line with: a heading marker, a list bullet,
# a numbered step, a block quote. Decoration around the sentence, never part of
# the job — quoting it back is how the header ended up reading "## Task".
_MARKUP_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d{1,2}[.)]\s+|>\s+)")
# Emphasis and code ticks, which a header renders literally because it renders
# no markdown at all.
_EMPHASIS_RE = re.compile(r"[*_`]{1,3}")

# A raw path list is a frequent last readable row after a search command.  It
# contains plenty of letters, so the older "three alphabetic characters" rule
# accepted it as work and headers ended up reading `jarvis/core/bus.py",
# "jarvis/memory`.  A path can be useful after a verb ("Wrote src/app.py"), but
# paths on their own name implementation debris, not the user's task.
_PATH_PART_RE = re.compile(r"[\\/]")

# Section labels a brief is structured with. On their own they name the FORM of
# the document, not the work, so the job is on the line after them — and where
# one prefixes the sentence ("Task: fix the login test") the sentence is the
# part worth quoting. Lower-case, colon already stripped.
_SECTION_LABELS = frozenset(
    {
        "brief",
        "context",
        "goal",
        "goals",
        "instruction",
        "instructions",
        "objective",
        "objectives",
        "prompt",
        "request",
        "summary",
        "task",
        "tasks",
        "what to do",
        "your task",
    }
)


def _job_line(text: str) -> str:
    """The line of an instruction that actually names the job.

    A prompt that arrives from the composer is a structured markdown brief, so
    its first line is as often ``# Task`` or ``**Context**`` as it is the work —
    and a header that quotes the first line quotes the scaffolding. This walks
    the opening of the brief instead, strips the markup off each line, and takes
    the first one that says something. A brief that is nothing but labels still
    answers with its first label rather than with nothing.
    """
    label_seen = ""
    for raw in str(text or "").splitlines()[:12]:
        line = _EMPHASIS_RE.sub("", _MARKUP_RE.sub("", raw.strip())).strip()
        if not line or line.startswith("```"):
            continue
        # A horizontal rule, a box edge, a row of dashes under a heading.
        if sum(1 for ch in line if ch.isalpha()) < 3:
            continue
        head, sep, rest = line.partition(":")
        if sep and head.strip().lower() in _SECTION_LABELS:
            if rest.strip():
                return rest.strip()
            label_seen = label_seen or line
            continue
        if line.rstrip(":").strip().lower() in _SECTION_LABELS:
            label_seen = label_seen or line
            continue
        return line
    return label_seen


@dataclass(frozen=True, slots=True)
class Recap:
    """What one pane is doing, in the two lengths the UI needs.

    ``headline`` goes in the pane header and is expected to be clipped;
    ``detail`` is the one-or-two-sentence version behind the hover tooltip.
    Both are plain English and both may be empty — a pane that has done nothing
    yet has nothing to report, and inventing a sentence for it would be noise.
    """

    headline: str
    detail: str


def condense(text: str, limit: int) -> str:
    """One line of ``text``, whitespace collapsed, cut on a word boundary."""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    # The ellipsis belongs INSIDE the advertised budget.  Returning `limit + 1`
    # made every caller's cap slightly dishonest, and this cap now corresponds
    # to actual pixels in the pane header rather than only a transport guard.
    cut = cleaned[: max(1, limit - 1)]
    spaced = cut.rsplit(" ", 1)[0]
    # A single very long token (a path, a hash) has no space to cut at — take
    # the hard cut rather than returning nothing.
    return f"{spaced or cut}…"


def _bare_path_list(text: str) -> bool:
    """Whether ``text`` is only one or more filesystem-looking tokens."""
    tokens = [
        token.strip("\"'()[]{}<>,:;")
        for token in re.split(r"\s+", text.strip())
        if token.strip("\"'()[]{}<>,:;")
    ]
    return bool(tokens) and all(_PATH_PART_RE.search(token) for token in tokens)


def _informative(line: str) -> bool:
    """True for a transcript row that says something about the work."""
    text = line.strip()
    if len(text) < 4 or text.startswith(_INPUT_MARKERS):
        return False
    lowered = text.lower()
    if any(fragment in lowered for fragment in _chrome_fragments()):
        return False
    if _bare_path_list(text):
        return False
    # A row of numbers, punctuation or box residue is not a sentence.
    return sum(1 for ch in text if ch.isalpha()) >= 3


def _activity(tail: Sequence[str]) -> str:
    """The most recent row that reads like something the agent did."""
    for line in reversed(list(tail)):
        if not _informative(line):
            continue
        return condense(_LEAD_RE.sub("", line.strip()), HEADLINE_CHARS)
    return ""


def _task(term: Any, limit: int = TASK_CHARS) -> str:
    """The instruction this pane was last given, short enough to quote.

    ``limit`` is the caller's, because the header and the card have very
    different amounts of room and the same sentence has to serve both.
    """
    return condense(_job_line(getattr(term, "last_prompt", "")), limit)


def idle_phrase(term: Any) -> str:
    """How long ago this pane last printed anything, in words."""
    last = getattr(term, "last_output_at", None)
    if not last:
        return ""
    seconds = max(0, int(time.time() - float(last)))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def _sentences(*parts: str) -> str:
    """Join the non-empty parts into one paragraph, capped for transport."""
    return condense(" ".join(part for part in parts if part), DETAIL_CHARS)


def _typed_into(term: Any) -> bool:
    """Can Jarvis send this pane an instruction at all?

    False for a plain terminal: it is a shell, and Jarvis deliberately does not
    type into one (see ``session.accepts_prompts``). Saying "no instruction has
    been sent yet" about such a pane would promise one that is never coming.

    Every step is defensive, like the rest of this module: an entry that predates
    the shell panes, or a registry this build does not have, answers "yes, it
    takes prompts" — which is what every pane did before plain terminals existed.
    """
    try:
        from jarvis.workspace.agents import get_agent

        spec = get_agent(str(getattr(term, "agent", "") or ""))
    except Exception:  # noqa: BLE001 - a recap must never break a state read
        return True
    return spec is None or bool(getattr(spec, "is_coding_agent", True))


def _task_sentence(task: str, *, sent: int, working: bool = False) -> str:
    """What this pane was told to do, or an honest note that nothing was.

    ``working`` distinguishes the two panes the no-instruction sentence used to
    conflate: one that is genuinely idle, and one whose prompt was typed
    straight into the terminal — busy the whole time, yet captioned with a
    sentence that read like "nothing is happening here".
    """
    if task:
        return f'Last asked to: "{task}".'
    if sent:
        return "Its last instruction came from Jarvis."
    if working:
        return "It is being driven directly in the terminal, not through Jarvis."
    return "No instruction has been sent to it from Jarvis yet."


def summarize(term: Any, *, tail: Sequence[str] | None = None) -> Recap:
    """Recap the session running in ``term``.

    ``tail`` lets a caller that has already read the transcript pass the rows in
    rather than have them read a second time — :meth:`Terminal.to_dict` counts
    the transcript anyway, and replaying a 600-row screen twice per poll per
    pane is a cost with nothing to show for it.

    Duck-typed on purpose (``Terminal`` lives in :mod:`.session`, which imports
    this module): every attribute is read defensively so a test double carrying
    half a pane still produces a sentence instead of an ``AttributeError``.
    """
    if tail is None:
        transcript = getattr(term, "transcript", None)
        try:
            tail = transcript.tail(TAIL_LINES) if transcript is not None else []
        except Exception:  # noqa: BLE001 - a recap must never break a state read
            tail = []

    status = str(getattr(term, "status", "") or "pending")
    task = _task(term)
    sent = int(getattr(term, "prompts_sent", 0) or 0)
    activity = _activity(tail)
    idle = idle_phrase(term)
    # A live pane that is visibly producing output but was never sent a prompt
    # is being driven by hand — the caption must say that, not imply idleness.
    working = status == "live" and bool(activity or idle)
    # One sentence about the instruction, computed once: every branch below
    # opens with it, and a plain terminal answers it differently.
    asked = (
        _task_sentence(task, sent=sent, working=working)
        if _typed_into(term)
        else "This is a plain terminal — you type into it yourself."
    )

    if status == "error":
        problem = condense(getattr(term, "error", ""), HEADLINE_CHARS)
        problem = problem or "it could not be started"
        return Recap(
            headline=condense(f"Not running — {problem}", HEADLINE_CHARS),
            detail=_sentences(
                f"This pane is not running: {problem}.",
                asked,
            ),
        )

    if status == "pending":
        return Recap(
            headline="Not started — waiting for terminal connection",
            detail=_sentences(
                "This pane has no agent running yet; it starts as soon as the terminal connects.",
                asked if task else "",
            ),
        )

    if status == "exited":
        code = getattr(term, "exit_code", None)
        ended = "Finished and exited" if code in (0, None) else f"Exited with code {code}"
        closing = f"{ended}. Last output: {activity}" if activity else f"{ended}."
        return Recap(
            headline=condense(closing, HEADLINE_CHARS),
            detail=_sentences(
                asked,
                f"{ended}" + (f", last printing: {activity}." if activity else "."),
            ),
        )

    # Live. The INSTRUCTION leads, and that ordering was learned the hard way:
    # this used to open with the newest readable row, on the theory that the
    # freshest signal is the most useful one. It is not. The last row of a
    # full-screen coding CLI is a fragment of a sentence it is halfway through
    # printing — a pane that had just written a design document reported
    # "without interrupting Claude's current work", which is a true quote and a
    # complete non-answer. What the pane was ASKED to do is at least always
    # about the job. The row it printed follows in the tooltip, and the real
    # answer to "what has it achieved" comes from .recap_engine.
    #
    # The job goes in BARE — no "Working on:" prefix and no quotes. The header
    # is a few centimetres wide and clips the end of whatever is in it, so ten
    # characters of framing is ten characters of the answer pushed off the edge;
    # the long form below still says which of the two it is.
    if task:
        headline = _task(term, HEADLINE_TASK_CHARS)
    elif activity:
        headline = activity
    else:
        headline = "Running — no work visible yet"

    now = (
        f"Working now, last output {idle}: {activity}."
        if idle and activity
        else (f"Working now: {activity}." if activity else "Running, with nothing printed yet.")
    )
    return Recap(
        headline=condense(headline, HEADLINE_CHARS),
        detail=_sentences(asked, now),
    )


__all__ = [
    "DETAIL_CHARS",
    "HEADLINE_CHARS",
    "HEADLINE_TASK_CHARS",
    "TAIL_LINES",
    "TASK_CHARS",
    "Recap",
    "condense",
    "idle_phrase",
    "summarize",
]
