"""One agent reports at a time — the Command Deck's way back to the user.

## The gap this closes

:mod:`.notifications` already knows the moment a pane stops working, and the
header bell already carries the count. What nobody ever does is SAY it. In the
grid and in chat that is the right behaviour: the user is looking at the panes,
and a wall of terminals that talks would be unbearable. In the Command Deck it
is the whole point — the user is a team lead who handed out work by voice and
then looked away, and the work coming back is the half that was missing.

## Why a queue rather than "announce each one"

Eight agents can stop inside the same ten seconds. Spoken naively that is eight
announcements stacked on each other, or — with the speech pipeline's own
queueing — one long recital nobody asked for, delivered while the user is
mid-sentence about something else.

So nothing is announced directly. Reports land here, and this module answers
one question per sweep: *is there something to say right now, and what is the
one thing it is?* The rules, in the order they were argued out with the
maintainer:

1. **One speaker.** Exactly one report is on air. Everything else waits.
2. **Headline before detail.** Several landing in the same silence produce ONE
   line — "three are done: Mika, Nova and Kai" — not three reports. That is the
   sentence a team lead would actually get, and it clears the pile-up in one
   breath instead of spending a minute on it.
3. **The user's answer advances the queue.** Asking for one by name puts that
   report on air; "later" puts the queue to sleep.
4. **A blocker cuts the line.** ``needs_input`` outranks ``completed``: a pane
   holding a question has work STOPPED on it, a finished pane has work merely
   waiting to be read.
5. **Never interrupt.** Everything leaves here as ``priority="normal"``, which
   the speech pipeline queues behind whatever is being said. Nothing this
   module produces is ever worth cutting the user off for.
6. **Read means silent.** A pane the user opened and read in the deck drops out
   of the queue without ever being spoken — they already know.
7. **Hanging up is still the killswitch.** Speech stops instantly, because that
   is the pipeline's contract, not ours. The queue SURVIVES and is offered once
   at the start of the next conversation. It is never shouted after the user
   (see ``auflegen`` mandate + the no-nagging one).
8. **Said once.** An unanswered headline is not repeated, and a report that has
   been spoken is not spoken again. After an unanswered line the queue goes
   quiet until the user does something — with one exception, which is rule 4:
   a pane that is BLOCKED wakes it, because there the work has stopped.
9. **Bounded.** :data:`MAX_PENDING` reports; past that the oldest ``completed``
   ones collapse into the count instead of growing a backlog nobody will hear.

## What it will not claim

Nothing here reads the agent's prose to decide whether the work went well. A
"finished" report means the pane went quiet, exactly as the bell's entry does
(:mod:`.notifications`) — the wording says so, and the deck's card says so.
Promising the other thing would be the one lie this whole surface cannot
afford, because the user asked for it precisely so they could stop looking.

## Cost

Zero polling of its own: the sweep in :mod:`.notifications` already looks at
every pane every couple of seconds, and hands its findings here. Announcing is
one bounded flash call through :func:`jarvis.voice.contextual_readback.render_readback`
with a deterministic multilingual fallback, so an install with no provider at
all still gets a spoken line (§3).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from itertools import count
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable, Iterable, Sequence

    from .notifications import Notification

logger = logging.getLogger(__name__)

#: What one report is about — the same four kinds the bell files.
Kind = Literal["completed", "needs_input", "exited", "failed"]

#: Where a report is in its life.
#:
#: ``pending`` waiting · ``on_air`` the one being reported right now · ``reported``
#: delivered · ``dropped`` taken out without being said (the user read the pane,
#: or asked for it to go away).
#:
#: ``reported`` rather than "answered" on purpose: what the deck knows is that
#: it said the line, never that the user took it in. Naming the state after the
#: stronger claim is how the first version ended up treating an ignored report
#: as a closed one.
ReportState = Literal["pending", "on_air", "reported", "dropped"]

#: What the user can do about a report.
Action = Literal["next", "later", "drop"]

#: How many reports wait at once. A dozen is already more than anyone will sit
#: through; past it the oldest finished ones are folded into the headline count,
#: which is the honest way to lose them — they are still on their cards.
MAX_PENDING = 12

#: Kinds that may wake a settled queue, because work has STOPPED rather than
#: finished. Rule 4 and rule 8's one exception.
BLOCKING_KINDS: frozenset[str] = frozenset({"needs_input"})

#: Reporting order. A blocked pane first, then a crashed one (it is not coming
#: back on its own either), then the ordinary finishes.
_PRIORITY: dict[str, int] = {"needs_input": 0, "failed": 1, "exited": 2, "completed": 3}

_ids = count(1)


@dataclass(slots=True)
class Report:
    """One pane's news, waiting for its turn to be said."""

    id: str
    workspace_id: str
    pane_key: str
    pane: str
    agent: str
    kind: Kind
    #: One clause: what happened. Taken from the bell's entry so the spoken
    #: line, the deck card and the notification panel cannot disagree.
    headline: str
    #: What the pane was working on, from its own recap. May be empty.
    detail: str
    at: float
    state: ReportState = "pending"
    #: Has this report itself been spoken? Distinct from having been counted in
    #: a headline — "three are done" names the panes without reporting them.
    spoken: bool = False
    #: Was this one of the panes a headline counted? Stops a second headline
    #: from being produced for a set the user has already been told about.
    in_headline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "pane_key": self.pane_key,
            "pane": self.pane,
            "agent": self.agent,
            "kind": self.kind,
            "headline": self.headline,
            "detail": self.detail,
            "at": self.at,
            "state": self.state,
            "spoken": self.spoken,
        }


@dataclass(frozen=True, slots=True)
class Utterance:
    """What should be said right now, and what saying it covers.

    ``form`` is the shape rather than the wording: ``report`` is one pane's
    news, ``headline`` is the "three are done" line that stands in for several,
    and ``resumed`` is the once-only "while you were away" opening. The words
    themselves are composed later — this is the decision, not the sentence.
    """

    form: Literal["report", "headline", "resumed"]
    reports: tuple[Report, ...]

    @property
    def panes(self) -> tuple[str, ...]:
        return tuple(report.pane for report in self.reports)


class StandupQueue:
    """The per-process queue, and every rule about when it may speak.

    Deliberately synchronous and loop-free: the whole decision is a function of
    (reports, clock, what the user last did), so it can be tested without a
    PTY, a voice session or a wait — which is what the four failure modes in
    the module docstring all needed and none of them got.

    Not thread-safe, by the same reasoning as :class:`.notifications.NotificationCenter`:
    the sweep that fills it and the routes that read it are all on the event
    loop.
    """

    def __init__(self, limit: int = MAX_PENDING) -> None:
        self._reports: list[Report] = []
        self._limit = limit
        #: True once a line has gone out that nobody answered. Rule 8: the
        #: queue then holds its tongue until the user does something. Blocking
        #: news clears it — see :meth:`offer`.
        self._settled = False
        #: True while the user is away (they hung up) and reports arrived. The
        #: next conversation opens with one "while you were away" line.
        self._missed = False
        #: Is a conversation happening at all? Nothing is spoken outside one:
        #: the deck talks to a person who is listening, not to an empty room.
        self._in_conversation = False

    # ------------------------------------------------------------------ fill

    def offer(self, entries: Iterable[Notification], *, now: float | None = None) -> list[Report]:
        """Take what the sweep just found. Returns the reports actually filed.

        Duplicates are dropped rather than stacked: the same pane finishing the
        same episode twice is one piece of news, and the bell's own
        ``announced`` latch already means a second entry is a new episode — but
        a pane that finishes, is asked something, and finishes again inside one
        silence must not produce three lines about the same work.
        """
        moment = time.time() if now is None else now
        filed: list[Report] = []
        for entry in entries:
            report = self._file(entry, moment)
            if report is None:
                continue
            filed.append(report)
            # Rule 4 / rule 8's exception: a pane waiting on the user is not
            # news that can sit. It wakes a queue that had gone quiet.
            if report.kind in BLOCKING_KINDS:
                self._settled = False
        if filed and not self._in_conversation:
            self._missed = True
        self._trim()
        return filed

    def _file(self, entry: Notification, now: float) -> Report | None:
        ident = (entry.workspace_id, entry.pane_key)
        for existing in self._reports:
            if (existing.workspace_id, existing.pane_key) != ident:
                continue
            if existing.state not in ("pending", "on_air"):
                continue
            if existing.kind == entry.kind:
                # Same news about the same pane, still unsaid. Refresh what it
                # says (a recap can improve between sweeps) and keep its place.
                existing.headline = entry.title
                existing.detail = entry.detail
                return None
            if _PRIORITY.get(entry.kind, 9) < _PRIORITY.get(existing.kind, 9):
                # It finished, and now it is asking. The question is the news;
                # the finish is what led to it.
                existing.kind = entry.kind  # type: ignore[assignment]
                existing.headline = entry.title
                existing.detail = entry.detail
                existing.at = now
                existing.in_headline = False
                return None
            return None
        report = Report(
            id=f"sr{next(_ids)}",
            workspace_id=entry.workspace_id,
            pane_key=entry.pane_key,
            pane=entry.pane,
            agent=entry.agent,
            kind=entry.kind,
            headline=entry.title,
            detail=entry.detail,
            at=now,
        )
        self._reports.append(report)
        return report

    def _trim(self) -> None:
        """Rule 9 — keep the waiting list bounded, losing the least useful end.

        Only ``completed`` reports are ever dropped, and only the oldest. A
        blocked or crashed pane is never quietly forgotten: those are the two
        the user most needs to hear about, and a queue that silently loses them
        is worse than one that says nothing at all.
        """
        waiting = [r for r in self._reports if r.state == "pending"]
        excess = len(waiting) - self._limit
        if excess <= 0:
            return
        droppable = [r for r in waiting if r.kind == "completed"]
        for report in droppable[:excess]:
            report.state = "dropped"

    # ------------------------------------------------------------------ read

    def pending(self) -> list[Report]:
        """Everything still waiting, blockers first, then oldest first."""
        return sorted(
            (r for r in self._reports if r.state == "pending"),
            key=lambda r: (_PRIORITY.get(r.kind, 9), r.at),
        )

    def on_air(self) -> Report | None:
        return next((r for r in self._reports if r.state == "on_air"), None)

    def find(self, report_id: str) -> Report | None:
        return next((r for r in self._reports if r.id == report_id), None)

    def sleeping(self) -> bool:
        """Has a line gone unanswered, so the queue is holding its tongue?"""
        return self._settled

    # --------------------------------------------------------------- speaking

    def take_due(self, *, now: float | None = None) -> Utterance | None:
        """The one thing to say right now, marked as said. ``None`` for silence.

        Marking happens HERE rather than after the sentence is spoken, and that
        is deliberate: composing a line is an awaited call, the sweep runs
        every couple of seconds, and a second sweep arriving mid-compose would
        otherwise decide the same report is still due and say it twice.
        """
        _ = now
        if not self._in_conversation:
            return None
        # A report the user asked for BY NAME jumps every rule below it: they
        # said "tell me about Nova", and anything else answering that is the
        # deck deciding it knows better.
        requested = next((r for r in self._reports if r.state == "on_air" and not r.spoken), None)
        if requested is not None:
            requested.spoken = True
            self._settled = True
            return Utterance(form="report", reports=(requested,))
        waiting = self.pending()
        if not waiting:
            return None
        if self._missed:
            # Rule 7: one opening line for everything that happened while the
            # user was away. It is a headline even for a single report — the
            # point is the handover, not the count.
            self._missed = False
            self._settled = True
            for report in waiting:
                report.in_headline = True
            return Utterance(form="resumed", reports=tuple(waiting))
        if self._settled:
            return None  # rule 8
        unheralded = [r for r in waiting if not r.in_headline]
        if not unheralded:
            return None
        if len(unheralded) == 1:
            # One piece of news is simply said. A "one agent is done, shall I
            # tell you?" would be a question with one possible answer, which
            # costs the user a turn and tells them nothing.
            report = unheralded[0]
            self._retire_on_air()
            report.state = "on_air"
            report.spoken = True
            self._settled = True
            return Utterance(form="report", reports=(report,))
        for report in unheralded:
            report.in_headline = True
        self._settled = True
        return Utterance(form="headline", reports=tuple(unheralded))

    # ------------------------------------------------------------- user input

    def acknowledge(self, report_id: str, action: Action) -> Report | None:
        """What the user said about a report. Returns it, or ``None`` if gone.

        ``next`` is the interesting one: it is both "I heard that" for whatever
        was on air and "tell me this one" for the report named. One call rather
        than two because that is one utterance — "yes, and what about Nova?".
        """
        report = self.find(report_id)
        if report is None:
            return None
        if action == "drop":
            report.state = "dropped"
            self._settled = False
            return report
        if action == "later":
            # The queue sleeps, but nothing is thrown away: the deck keeps
            # showing the lane, and the next thing the user says wakes it.
            self._retire_on_air()
            self._settled = True
            return report
        self._retire_on_air()
        # Put ON AIR but not yet spoken: the sentence is composed on the next
        # beat, and marking it said here would mean a compose that fails leaves
        # the user having asked for a report they never get.
        report.state = "on_air"
        report.spoken = False
        self._settled = False
        return report

    def _retire_on_air(self) -> None:
        """Whatever was being reported is finished with.

        Deliberately not a state called "answered": the user may well have said
        nothing at all. What is true is that the report was DELIVERED, and a
        delivered report must not sit on air blocking the queue behind it —
        which is exactly what it did in the first version, where one unanswered
        line silenced the deck permanently.
        """
        current = self.on_air()
        if current is not None:
            current.state = "reported" if current.spoken else "pending"

    def wake(self) -> None:
        """The user did something — the queue may speak again.

        Called when a turn is taken in the deck. Rule 8 stops the queue from
        repeating itself into silence; it must not stop it from answering a
        person who has just come back to it.
        """
        self._settled = False
        self._retire_on_air()

    def conversation_started(self) -> None:
        self._in_conversation = True
        self._settled = False

    def conversation_ended(self) -> None:
        """Hung up. Speech is already dead; the queue simply stops offering.

        Whatever is on air is retired rather than left mid-report: the user did
        not answer it, and it must not be waiting on air for a conversation
        that may be an hour away.
        """
        self._in_conversation = False
        self._retire_on_air()
        if self.pending():
            self._missed = True

    def in_conversation(self) -> bool:
        return self._in_conversation

    # ------------------------------------------------------------- housekeeping

    def drop_pane(self, workspace_id: str, pane_key: str) -> int:
        """Rule 6 — the user read this pane, so there is nothing to tell them."""
        dropped = 0
        for report in self._reports:
            if report.state not in ("pending", "on_air"):
                continue
            if report.workspace_id != workspace_id or report.pane_key != pane_key:
                continue
            report.state = "dropped"
            dropped += 1
        return dropped

    def forget_workspace(self, workspace_id: str) -> int:
        before = len(self._reports)
        self._reports = [r for r in self._reports if r.workspace_id != workspace_id]
        return before - len(self._reports)

    def retain(self, live: Sequence[tuple[str, str]]) -> int:
        """Drop reports whose pane is gone — nothing to jump to, nothing to say."""
        alive = set(live)
        before = len(self._reports)
        self._reports = [r for r in self._reports if (r.workspace_id, r.pane_key) in alive]
        return before - len(self._reports)

    def clear(self) -> None:
        self._reports.clear()
        self._settled = False
        self._missed = False
        self._in_conversation = False

    def state(self) -> dict[str, Any]:
        """Everything the deck's lane needs in one response."""
        on_air = self.on_air()
        return {
            "sleeping": self._settled,
            "in_conversation": self._in_conversation,
            "on_air": None if on_air is None else on_air.to_dict(),
            "pending": [r.to_dict() for r in self.pending()],
            "reports": [r.to_dict() for r in self._reports if r.state in ("pending", "on_air")],
        }


# --------------------------------------------------------------------- wording


def spoken_facts(utterance: Utterance) -> dict[str, object]:
    """The ground truth a composed sentence may rephrase and nothing else.

    Handed to :func:`~jarvis.voice.contextual_readback.render_readback`, whose
    honesty guard rejects anything the model adds on top. Kept small on
    purpose: the pane names and what happened to them is the whole message, and
    every extra field is another thing a rephrasing can get subtly wrong.
    """
    if utterance.form == "report":
        report = utterance.reports[0]
        return {
            "agent": report.pane,
            "what_happened": report.headline,
            "was_working_on": report.detail,
        }
    return {
        "agents": list(utterance.panes),
        "count": len(utterance.reports),
        "what_happened": [r.headline for r in utterance.reports],
    }


def canned_line(utterance: Utterance, language: str) -> str:
    """The deterministic sentence, in the three languages the pipeline speaks.

    This is what an install with no provider — or a provider that timed out —
    actually says, so it has to stand on its own rather than read as a
    placeholder. It is also the honesty floor the composed version is checked
    against.

    Product surface, so German and Spanish are allowed here (CLAUDE.md §1,
    closed-list item: runtime voice output).
    """
    lang = (language or "en").lower()[:2]
    names = _spoken_list(utterance.panes, lang)
    count_ = len(utterance.reports)
    if utterance.form == "report":
        report = utterance.reports[0]
        if report.kind in BLOCKING_KINDS:
            if lang == "de":
                return f"{report.pane} wartet auf dich."  # i18n-allow: voice output
            if lang == "es":
                return f"{report.pane} está esperando tu respuesta."  # i18n-allow: voice output
            return f"{report.pane} is waiting on you."
        if lang == "de":
            return f"{report.pane} ist durch."  # i18n-allow: voice output
        if lang == "es":
            return f"{report.pane} ha terminado."  # i18n-allow: voice output
        return f"{report.pane} is done."
    if utterance.form == "resumed":
        if lang == "de":
            return f"Während du weg warst: {names}."  # i18n-allow: voice output
        if lang == "es":
            return f"Mientras no estabas: {names}."  # i18n-allow: voice output
        return f"While you were away: {names}."
    if lang == "de":
        return f"{count_} sind durch: {names}. Womit fange ich an?"  # i18n-allow: voice output
    if lang == "es":
        return f"{count_} han terminado: {names}. ¿Por cuál empiezo?"  # i18n-allow: voice output
    return f"{count_} are done: {names}. Which one first?"


def _spoken_list(names: Sequence[str], language: str) -> str:
    """ "Mika, Nova and Kai" — a list said the way a person says one."""
    items = [n for n in names if n]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    joiner = {"de": "und", "es": "y"}.get(language, "and")  # i18n-allow: voice output
    return f"{', '.join(items[:-1])} {joiner} {items[-1]}"


# ----------------------------------------------------------------------- store
_QUEUE = StandupQueue()


def queue() -> StandupQueue:
    """The process-wide queue the sweep fills and the routes read."""
    return _QUEUE


def reset() -> None:
    """Drop everything — for tests, and for a registry that was torn down."""
    _QUEUE.clear()
    reset_switch_cache()


@dataclass(slots=True)
class _Wiring:
    """How this module reaches the voice, injected rather than imported.

    Nothing here imports the speech pipeline, and that is not tidiness: this
    runs inside the pane sweep, which must keep working in a headless install,
    in the tests, and on a machine with no audio at all. Unwired, the queue
    still fills and the deck still shows the lane — it simply says nothing,
    which is the honest degradation (§3).
    """

    #: Publish an announcement. Given the text and the language.
    speak: Callable[[str, str], Any] | None = None
    #: The language this turn should be spoken in. ONE resolver decides that
    #: for every layer (CLAUDE.md §1); this asks it rather than guessing.
    language: Callable[[], str] | None = None
    #: The flash composer that turns facts into a sentence. ``None`` keeps the
    #: deterministic line, which is a complete answer rather than a fallback.
    composer: Any = None


_wiring = _Wiring()


def wire(
    *,
    speak: Callable[[str, str], Any] | None = None,
    language: Callable[[], str] | None = None,
    composer: Any = None,
) -> None:
    """Give the queue a voice. Called once, where the speech stack is built."""
    _wiring.speak = speak
    _wiring.language = language
    _wiring.composer = composer


def unwire() -> None:
    """Take the voice away again — shutdown, and tests that assert silence."""
    _wiring.speak = None
    _wiring.language = None
    _wiring.composer = None


#: How long the config switch is trusted before it is read again. Same reason
#: and same number as :data:`.notifications.SWITCH_TTL_S`: ``load_config``
#: parses the TOML on every call and this runs beside a two-second sweep.
SWITCH_TTL_S = 15.0

_switch: tuple[float, bool] = (0.0, True)


def enabled() -> bool:
    """Is the deck allowed to speak on this install?

    A config that cannot be read answers "on", matching the bell: failing
    closed here would make a broken config look like a broken feature, and the
    surface is opt-in already — you have to be in the deck for any of it.
    """
    global _switch
    cached_at, value = _switch
    now = time.monotonic()
    if now - cached_at < SWITCH_TTL_S:
        return value
    try:
        from jarvis.core.config import load_config

        value = bool(getattr(load_config().agentic_ide, "deck_reports", True))
    except Exception:  # noqa: BLE001 - never let a config read stop the sweep
        value = True
    _switch = (now, value)
    return value


def reset_switch_cache() -> None:
    """Forget the cached switch — for tests, and for a live config change."""
    global _switch
    _switch = (0.0, True)


def deck_workspaces(registry: Any) -> set[str]:
    """Which open workspaces are being READ as a Command Deck right now.

    The deck is the only surface that speaks, and "is it on screen" is the
    frontend's answer, reported through ``set_surface_context``. Asking per
    sweep rather than remembering it means navigating away goes quiet on the
    next beat instead of whenever something else happens to notice.
    """
    from .workspace_view import VIEW_DECK

    found: set[str] = set()
    try:
        for session in registry.sessions:
            if getattr(session, "surface_view", "") == VIEW_DECK:
                found.add(str(session.id))
    except Exception:  # noqa: BLE001 - a registry read must not end the sweep
        return set()
    return found


async def pump(registry: Any, entries: Sequence[Notification]) -> str:
    """The sweep's one call into this module: take the news, say what is due.

    Everything is filtered to workspaces currently being read as a deck. A
    finished pane in a workspace the user is watching as a grid is not news
    they need told — they are looking at it.

    Never raises. It runs inside the pane sweep, which also files the bell's
    entries and the resume checkpoints, and none of those may die because the
    deck could not think of a sentence.
    """
    try:
        if not enabled():
            return ""
        decks = deck_workspaces(registry)
        if not decks:
            # Nothing is being read as a deck. The queue keeps whatever it
            # holds — the user may come back to it — but takes nothing new.
            return ""
        fresh = [entry for entry in entries if entry.workspace_id in decks]
        if fresh:
            _QUEUE.offer(fresh)
        return await announce_due()
    except Exception as exc:  # noqa: BLE001 - a mute deck beats a dead sweep
        logger.warning("Agentic IDE: standup pump failed: %s", exc)
        return ""


async def announce_due() -> str:
    """Say the one thing that is due, if anything is. Returns what was said.

    Never raises: this is called from the pane sweep, and a failure to speak
    must not take down the loop that also files the bell's entries and the
    resume checkpoints.
    """
    if _wiring.speak is None:
        return ""
    try:
        utterance = _QUEUE.take_due()
        if utterance is None:
            return ""
        language = _resolve_language()
        text = await _compose(utterance, language)
        if not text:
            return ""
        await _deliver(text, language)
        return text
    except Exception as exc:  # noqa: BLE001 - a mute deck beats a dead sweep
        logger.warning("Agentic IDE: standup announcement failed: %s", exc)
        return ""


def _resolve_language() -> str:
    if _wiring.language is None:
        return "en"
    try:
        return str(_wiring.language() or "en")
    except Exception:  # noqa: BLE001 - a language lookup must not silence the deck
        return "en"


async def _compose(utterance: Utterance, language: str) -> str:
    from jarvis.voice.contextual_readback import render_readback

    return await render_readback(
        _wiring.composer,
        instruction=_INSTRUCTION[utterance.form],
        language=language,
        canned=lambda: canned_line(utterance, language),
        facts=spoken_facts(utterance),
        # The report IS the fact. A rephrasing that adds an outcome ("Mika
        # fixed the tests") would be inventing the one thing this surface
        # promised not to claim — see "What it will not claim" above.
        honesty_bound=True,
    )


async def _deliver(text: str, language: str) -> None:
    speak = _wiring.speak
    if speak is None:
        return
    result = speak(text, language)
    if hasattr(result, "__await__"):
        await result


#: What the composer is asked to write, per shape. Instructions rather than
#: templates: the maintainer's standing rule is that Jarvis must not read fixed
#: sentences out of a table, and these say what the line is FOR while leaving
#: the wording to the moment.
_INSTRUCTION: dict[str, str] = {
    "report": (
        "Tell the user, in one short spoken sentence, that this coding agent "
        "has stopped working. Do not claim the work succeeded — only that the "
        "agent is done and waiting. Do not offer to summarise."
    ),
    "headline": (
        "Several coding agents finished at once. Say so in ONE short spoken "
        "sentence naming them, and ask which one to start with. Do not report "
        "any of them yet."
    ),
    "resumed": (
        "The user has just come back to the conversation. In one short spoken "
        "sentence, tell them which coding agents finished while they were "
        "away. Do not report any of them in detail yet."
    ),
}


__all__ = [
    "BLOCKING_KINDS",
    "MAX_PENDING",
    "SWITCH_TTL_S",
    "Action",
    "Kind",
    "Report",
    "ReportState",
    "StandupQueue",
    "Utterance",
    "announce_due",
    "canned_line",
    "deck_workspaces",
    "enabled",
    "pump",
    "queue",
    "reset",
    "reset_switch_cache",
    "spoken_facts",
    "unwire",
    "wire",
]
