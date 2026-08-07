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

Honest limits: a coding CLI's screen is mostly the CLI's own interface — its
spinner, status bar and input box are redrawn on every frame, and no blacklist
of chrome phrases wins against a TUI that randomizes its spinner verbs. So a
coding pane's HEADLINE never quotes the screen at all: it names the session
from the instruction it was sent, from the first request the user typed into
it, or with a neutral "who, since when" label — while a plain shell, whose
screen is a log rather than an interface, may still cite its newest row. What
the pane last printed stays in the DETAIL, explicitly labelled as a quote.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .activity import observed

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

# How far into the transcript's OPENING the user's first typed request is
# looked for. The first submitted prompt is echoed near the top of the
# scrollback, right after the CLI's banner.
_TYPED_SEARCH_LINES = 40
# The live input box (and the footer drawn under it) occupies the bottom rows
# of the screen; whatever is there is an unfinished thought being typed, not
# the session's purpose. Rows this close to the end never qualify.
_TYPED_SKIP_TAIL = 6

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


def _typed_request(term: Any, rows: Sequence[str]) -> str:
    """The first thing the user typed INTO this pane, read off its transcript.

    A pane driven by hand has no ``last_prompt``, yet its purpose was stated in
    its very first submitted prompt — which the CLI echoes into its scrollback
    behind an input marker. That echo is USER text: the one part of a TUI's
    screen that is safe to put in a header, because it is the same thing
    ``last_prompt`` would have held had the instruction come through Jarvis.

    Three impostors share the marker and are excluded: the LIVE input box, by
    position (it sits in the bottom rows, and what is there is being typed
    right now); the box's placeholder suggestion ('Try "fix lint errors"'), by
    its wording; and anything the AGENT prints with the same prefix — a
    markdown blockquote, a diff line — by position again: the echo precedes the
    agent's first response by construction, so the search ends at the first row
    of agent prose. The CLI's own banner is allowed to precede the echo, and is
    recognized the one way that needs no per-CLI list: it names the CLI.
    """
    names = tuple(
        value
        for value in (
            str(getattr(term, "display_name", "") or "").lower(),
            str(getattr(term, "agent", "") or "").lower(),
        )
        if value
    )
    source = [str(row) for row in list(rows)[:-_TYPED_SKIP_TAIL]][:_TYPED_SEARCH_LINES]
    for raw in source:
        text = raw.strip()
        marker = next((mark for mark in _INPUT_MARKERS if text.startswith(mark)), None)
        if marker is None:
            if _informative(text) and not any(name in text.lower() for name in names):
                # The agent's prose has started; any later marker row is the
                # agent QUOTING something, not the echo of what the user typed.
                return ""
            continue
        content = text[len(marker) :].strip()
        lowered = content.lower()
        if lowered.startswith('try "'):
            continue
        if sum(1 for ch in content if ch.isalpha()) < 3:
            continue
        if any(fragment in lowered for fragment in _chrome_fragments()):
            continue
        if _bare_path_list(content):
            continue
        return condense(content, HEADLINE_CHARS)
    return ""


def _session_label(term: Any) -> str:
    """This pane's honest self-description when nothing names the work.

    Never a row off the screen: a full-screen coding CLI's screen is the CLI's
    own interface, and quoting it produced headers reading "Churned for 1m 53s"
    — the spinner, timestamped. A neutral "who, since when" label carries less,
    but everything it says is true, and saying more is the job of the model
    recap above this floor, not of a string heuristic.
    """
    name = str(getattr(term, "display_name", "") or getattr(term, "agent", "") or "Session")
    started = getattr(term, "started_at", None)
    if started:
        clock = time.strftime("%H:%M", time.localtime(float(started)))
        return condense(f"{name} — running since {clock}", HEADLINE_CHARS)
    return condense(f"{name} — running", HEADLINE_CHARS)


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


def summarize(term: Any, *, lines: Sequence[str] | None = None) -> Recap:
    """Recap the session running in ``term``.

    ``lines`` lets a caller that has already read the transcript pass the rows
    in rather than have them read a second time — :meth:`Terminal.to_dict`
    counts the transcript anyway, and replaying a 600-row screen twice per poll
    per pane is a cost with nothing to show for it. The FULL rows, not a tail:
    the headline may need the transcript's opening (the user's first typed
    request is echoed there) while the detail quotes its newest readable row.

    Duck-typed on purpose (``Terminal`` lives in :mod:`.session`, which imports
    this module): every attribute is read defensively so a test double carrying
    half a pane still produces a sentence instead of an ``AttributeError``.
    """
    if lines is None:
        transcript = getattr(term, "transcript", None)
        try:
            lines = transcript.lines() if transcript is not None else []
        except Exception:  # noqa: BLE001 - a recap must never break a state read
            lines = []
    rows = list(lines)
    tail = rows[-TAIL_LINES:]

    status = str(getattr(term, "status", "") or "pending")
    task = _task(term)
    sent = int(getattr(term, "prompts_sent", 0) or 0)
    activity = _activity(tail)
    idle = idle_phrase(term)
    # Is this pane's agent STILL AT IT, from the one detector that answers that
    # (see .activity)? Asked rather than guessed from "has it ever printed
    # anything", which is what this used to do — and which called a pane that
    # had been finished for twenty minutes "Working now", one line under a badge
    # correctly saying it had stopped. Two descriptions of one pane, disagreeing
    # inside a single payload.
    doing = observed(term).activity
    # Is this a coding CLI (full-screen TUI) or a plain shell? The distinction
    # decides what the HEADLINE may quote: a shell's screen is a log of what
    # ran, safe to cite; a TUI's screen is the TUI's own interface, and citing
    # it is how headers came to read "Churned for 1m 53s" — the spinner.
    coding_tui = _typed_into(term)
    # The user's first typed request, for a CODING pane nobody briefed through
    # Jarvis. Never computed for a shell: a shell prompt echoes every command
    # behind the same marker, and titling a shell by its first command would
    # pin it forever to "git status" — its newest row is its honest headline.
    typed = "" if (task or not coding_tui) else _typed_request(term, rows)
    # One sentence about the instruction, computed once: every branch below
    # opens with it, and a plain terminal answers it differently.
    asked = (
        _task_sentence(task, sent=sent, working=status == "live" and bool(activity or idle))
        if coding_tui
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
        # The job still names the session — an exited pane is found in a list
        # by what it WAS FOR, and the badge beside the title says it stopped.
        if task:
            headline = _task(term, HEADLINE_TASK_CHARS)
        elif typed:
            headline = typed
        elif activity and not coding_tui:
            headline = condense(f"{ended}. Last output: {activity}", HEADLINE_CHARS)
        else:
            headline = condense(ended, HEADLINE_CHARS)
        return Recap(
            headline=headline,
            detail=_sentences(
                asked,
                f"{ended}" + (f", last printing: {activity}." if activity else "."),
            ),
        )

    # Live. The INSTRUCTION leads, and that ordering was learned the hard way:
    # this used to open with the newest readable row, on the theory that the
    # freshest signal is the most useful one. It is not. The last row of a
    # full-screen coding CLI is a fragment of a sentence it is halfway through
    # printing — or, worse, the CLI's own spinner: panes shipped with headers
    # reading "Churned for 1m 53s" and "2 MCP servers need attention", true
    # quotes of the screen and complete non-answers. So for a coding TUI the
    # headline now NEVER quotes the screen. What names the session, in order:
    # the instruction Jarvis sent, the first request the user typed into the
    # pane themselves, and failing both a neutral "who, since when" label. A
    # plain shell keeps citing its newest row — its screen is a log, not an
    # interface. The row a TUI printed still follows in the tooltip, and the
    # real answer to "what has it achieved" comes from .recap_engine.
    #
    # The job goes in BARE — no "Working on:" prefix and no quotes. The header
    # is a few centimetres wide and clips the end of whatever is in it, so ten
    # characters of framing is ten characters of the answer pushed off the edge;
    # the long form below still says which of the two it is.
    if task:
        headline = _task(term, HEADLINE_TASK_CHARS)
    elif typed:
        headline = typed
    elif activity and not coding_tui:
        headline = activity
    elif activity or idle:
        headline = _session_label(term)
    else:
        headline = "Running — no work visible yet"

    # What it is doing RIGHT NOW, in the same words the pane's badge uses. The
    # last row it printed rides along in every case: it is the evidence for the
    # claim, and it is what the reader checks the claim against.
    printed = f"Last printed{' ' + idle if idle else ''}: {activity}." if activity else ""
    if not activity:
        now = "Running, with nothing printed yet."
    elif doing == "working":
        now = f"Working now. {printed}"
    elif doing == "asking":
        now = f"Stopped with a question on screen, waiting for your answer. {printed}"
    elif doing == "waiting":
        now = f"Not working right now — waiting at its prompt. {printed}"
    else:
        # No confident reading: a pane whose process this caller cannot see, or
        # one still taking the terminal. The evidence stands on its own rather
        # than being dressed up as a claim about what the agent is doing.
        now = printed
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
