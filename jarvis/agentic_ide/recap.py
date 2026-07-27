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

# The header label is truncated by CSS anyway; this is the transport cap, so a
# pane that printed a 3,000-character JSON blob cannot push that through the
# state payload of every poll.
HEADLINE_CHARS = 120
# Two readable sentences in a tooltip roughly 20 rem wide.
DETAIL_CHARS = 280
# How much of the instruction is quoted. A prompt is often a whole brief; the
# first line of it is what identifies the task.
TASK_CHARS = 80

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
)

# Leading decoration a TUI puts in front of a real line: bullets, tree glyphs,
# check marks, spinner frames. Stripped so the recap starts on a word. The
# allowed openers are kept out of the class deliberately — a path, a quote or an
# @reference is content, not decoration.
_LEAD_RE = re.compile(r'^[^\w"\'(\[@/#]+')


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


def _condense(text: str, limit: int) -> str:
    """One line of ``text``, whitespace collapsed, cut on a word boundary."""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit]
    spaced = cut.rsplit(" ", 1)[0]
    # A single very long token (a path, a hash) has no space to cut at — take
    # the hard cut rather than returning nothing.
    return f"{spaced or cut}…"


def _informative(line: str) -> bool:
    """True for a transcript row that says something about the work."""
    text = line.strip()
    if len(text) < 4 or text.startswith(_INPUT_MARKERS):
        return False
    lowered = text.lower()
    if any(fragment in lowered for fragment in _CHROME_FRAGMENTS):
        return False
    # A row of numbers, punctuation or box residue is not a sentence.
    return sum(1 for ch in text if ch.isalpha()) >= 3


def _activity(tail: Sequence[str]) -> str:
    """The most recent row that reads like something the agent did."""
    for line in reversed(list(tail)):
        if not _informative(line):
            continue
        return _condense(_LEAD_RE.sub("", line.strip()), HEADLINE_CHARS)
    return ""


def _task(term: Any) -> str:
    """The instruction this pane was last given, short enough to quote.

    The FIRST line of it: a composed prompt is a structured markdown brief whose
    later lines are context and file references, and the opening line is the one
    that names the job.
    """
    first_line = str(getattr(term, "last_prompt", "") or "").split("\n", 1)[0]
    return _condense(first_line, TASK_CHARS)


def _idle_phrase(term: Any) -> str:
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
    return _condense(" ".join(part for part in parts if part), DETAIL_CHARS)


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


def _task_sentence(task: str, *, sent: int) -> str:
    """What this pane was told to do, or an honest note that nothing was."""
    if task:
        return f'Last asked to: "{task}".'
    if sent:
        return "Its last instruction came from Jarvis."
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
    # One sentence about the instruction, computed once: every branch below
    # opens with it, and a plain terminal answers it differently.
    asked = (
        _task_sentence(task, sent=sent)
        if _typed_into(term)
        else "This is a plain terminal — you type into it yourself."
    )
    activity = _activity(tail)
    idle = _idle_phrase(term)

    if status == "error":
        problem = _condense(getattr(term, "error", ""), HEADLINE_CHARS)
        problem = problem or "it could not be started"
        return Recap(
            headline=f"Not running — {problem}",
            detail=_sentences(
                f"This pane is not running: {problem}.",
                asked,
            ),
        )

    if status == "pending":
        return Recap(
            headline="Not started yet — connect to it to start its agent.",
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
            headline=_condense(closing, HEADLINE_CHARS),
            detail=_sentences(
                asked,
                f"{ended}" + (f", last printing: {activity}." if activity else "."),
            ),
        )

    # Live. Freshest signal first: what it is printing beats what it was told,
    # because the instruction is minutes old by the time the pane is glanced at
    # and the output is what changed since.
    if activity:
        headline = activity
    elif task:
        headline = f'Working on: "{task}"'
    else:
        headline = "Running — nothing printed yet."

    now = f"Working now, last output {idle}: {activity}." if idle and activity else (
        f"Working now: {activity}." if activity else "Running, with nothing printed yet."
    )
    return Recap(
        headline=_condense(headline, HEADLINE_CHARS),
        detail=_sentences(asked, now),
    )


__all__ = ["DETAIL_CHARS", "HEADLINE_CHARS", "TAIL_LINES", "Recap", "summarize"]
