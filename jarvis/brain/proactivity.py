"""How much initiative the assistant takes, said the same way on every surface.

The assistant was purely reactive (2026-10-07): every rule it was given about
the end of a reply pulled the same way ("end on a statement", "let them
lead", "skip closing offers", "do not invent a next step"), and nothing told
it that a grounded idea is welcome. So it never brought one, even when its
notes said a deadline was two days away.

This module is the one place that says what initiative means:

* :func:`directive` — the standing rule for the configured level. It tells
  the model when an idea is worth one sentence, how to phrase it so it is
  not a question the person must answer, and that initiative never
  authorises an action: anything outside the request, and anything that
  sends, contacts, spends, deletes, installs, changes settings or starts
  background work, is only offered until the person says yes.
* :func:`upcoming_block` — the dated goals and plans from the assistant's
  notebooks that fall within the next two weeks, so an idea can be grounded
  in what the person said they are working towards instead of invented.

The level is ``off`` | ``balanced`` | ``high`` (``[brain] proactivity``). It
changes live: the settings route and the ``ConfigReloaded`` subscriber call
:func:`apply_level`, and every prompt builder reads :func:`current_level`.
Society agents never get this block — their briefing is a work order.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from typing import Any, Final

log = logging.getLogger(__name__)

LEVELS: Final[tuple[str, ...]] = ("off", "balanced", "high")
DEFAULT_LEVEL: Final[str] = "balanced"
#: How far ahead a dated note counts as "coming up".
HORIZON_DAYS: Final[int] = 14
#: Most dated notes one prompt lists; the nearest win.
UPCOMING_LIMIT: Final[int] = 5
_UPCOMING_LIMIT_COMPACT: Final[int] = 3
#: A dated note is quoted up to this many characters.
_NOTE_MAX_CHARS: Final[int] = 220

_WEEKDAYS: Final[tuple[str, ...]] = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)

_lock = threading.Lock()
#: The level applied at runtime; ``None`` reads the config object instead.
_applied: str | None = None


#: Values a person writes by hand when they mean "off".
_OFF_WORDS: Final[frozenset[str]] = frozenset({"false", "none", "no", "0", "disabled"})


def normalize_level(value: object) -> str:
    """A known level; ``false``/``none``/``disabled`` mean off; anything else the default."""
    if value is False:
        return "off"
    if isinstance(value, str):
        level = value.strip().casefold()
        if level in LEVELS:
            return level
        if level in _OFF_WORDS:
            return "off"
    return DEFAULT_LEVEL


def apply_level(level: object) -> str:
    """Make ``level`` the live level for every surface; returns the normalised value."""
    global _applied
    normalised = normalize_level(level)
    with _lock:
        _applied = normalised
    return normalised


def reset_applied_level() -> None:
    """Forget the live level (tests); the config object answers again."""
    global _applied
    with _lock:
        _applied = None


def current_level(config: Any = None) -> str:
    """The live level, else the one in ``config.brain.proactivity``.

    Once the settings route or a Self-Mod write applied a level, it wins over
    the config object until the process restarts; a hand edit of
    ``jarvis.toml`` that emits no ``ConfigReloaded`` takes effect on restart.
    """
    with _lock:
        applied = _applied
    if applied is not None:
        return applied
    brain = getattr(config, "brain", None)
    return normalize_level(getattr(brain, "proactivity", DEFAULT_LEVEL))


# ── the standing rule ──────────────────────────────────────────────────────

_OFF: Final[str] = (
    "INITIATIVE: the user has turned suggestions off. Answer and do what they ask, "
    "without unrequested ideas, follow-up offers or reminders from your notes. Still "
    "say plainly when something you notice would make the requested result wrong "
    "or fail, because that is part of answering."
)

_SCOPE: Final[str] = (
    "Inside the current request you may look things up that the request obviously "
    "needs, as long as the lookup only reads, for example checking the calendar for a "
    "clash before proposing a time. "
    "Initiative never authorises an action. Anything outside the current request, and "
    "anything that sends a message or email, calls or contacts someone, spends money, "
    "buys, deletes or overwrites, changes settings, installs something, or starts "
    "agents, background work or schedules, is only offered, never done, until the "
    "person says yes. Never offer work they already asked for: do it. "
    "When they clearly accept an offer that named the exact action, that acceptance is "
    "the request, so do not ask a second time in your own words. A bare or unclear "
    "answer, or one that may belong to a different question, is not acceptance. The "
    "tools' own approval prompts still apply, so an action that needs confirmation "
    "still waits for it."
)

_GROUNDING: Final[str] = (
    "Ground every idea in this conversation, the dated plans and goals in your notes, "
    "the screen context they asked about, or a tool result; never invent a fact, a "
    "deadline or a need, and never use a generic closer such as asking whether there "
    "is anything else. Say it as a plain offer or observation (\"I can also draft the "
    "reply to Anna.\"), not as a question they must answer, keep it to one short "
    "sentence when you are speaking, and do not repeat an idea they ignored or "
    "declined in this conversation. When they say they want fewer or no suggestions, "
    "stop, and keep that as a lasting preference. This section is the one sanctioned "
    "exception to the rules about ending a reply without offers or next steps: those "
    "rules forbid filler and invented next steps, not one grounded idea. The user's own "
    "standing instructions win over this section."
)

_BALANCED: Final[str] = (
    "INITIATIVE (bring useful ideas, never noise): think one step ahead like a capable "
    "partner, not only a responder. First answer or do exactly what was asked. Then, "
    "when you notice something specific that would clearly help with what they are "
    "doing right now or with a goal, plan or deadline from your notes, add it: the "
    "obvious next step, a risk or conflict they have not mentioned, a better option, "
    "or an original idea that fits their goal. At most one such addition per reply, "
    "and most replies need none. Skip it for greetings, small talk, thanks, when they "
    "are venting, in quick back-and-forth, and when they asked for a short answer. "
    "Mention something dated from your notes only when it relates to the current "
    "topic or they ask about their day or plans, and at most once per conversation. "
    + _GROUNDING
    + " "
    + _SCOPE
)

_HIGH: Final[str] = (
    "INITIATIVE (the user wants you to be proactive): think ahead like a partner who "
    "owns their goals with them. First answer or do exactly what was asked. Then add "
    "what would genuinely move them forward: the next step, a risk or conflict, a "
    "better option, or an original idea that fits a goal from your notes. Up to two "
    "short additions per reply when both are worth it; none when nothing specific "
    "fits. Skip them while they are venting or in quick back-and-forth. When "
    "something dated from your notes is due within two days, you may give a one-line "
    "heads-up early in the conversation, even right after you greet them back (an "
    "exception to letting them lead), once per conversation. "
    + _GROUNDING
    + " "
    + _SCOPE
)

_COMPACT: Final[dict[str, str]] = {
    "off": (
        "INITIATIVE: suggestions are off. Do what is asked, without unrequested ideas "
        "or reminders; still say when something would make the result wrong."
    ),
    "balanced": (
        "INITIATIVE: after doing what was asked, add at most one short, specific idea "
        "when it clearly helps with their current task or a goal in your notes; most "
        "replies need none, and never on greetings or small talk. Say it as a plain "
        "offer, not a question, and never repeat a declined idea. Ideas never authorise "
        "actions: anything that sends, contacts, spends, deletes, changes settings or "
        "starts background work is only offered until they say yes. A clear yes to an "
        "exact offer is the request, so do not ask again yourself; a bare or unclear "
        "answer is not a yes, and the tools' own approval prompts still apply."
    ),
    "high": (
        "INITIATIVE: be proactive. After doing what was asked, add up to two short, "
        "specific ideas that move their goals forward, and a one-line heads-up when "
        "something in your notes is due within two days, once per conversation. Say "
        "ideas as plain offers, never repeat a declined one. Ideas never authorise "
        "actions: anything that sends, contacts, spends, deletes, changes settings or "
        "starts background work is only offered until they say yes. A clear yes to an "
        "exact offer is the request, so do not ask again yourself; a bare or unclear "
        "answer is not a yes, and the tools' own approval prompts still apply."
    ),
}


def directive(level: object = None, *, compact: bool = False) -> str:
    """The initiative rule for ``level`` (``None`` reads :func:`current_level`)."""
    resolved = current_level() if level is None else normalize_level(level)
    if compact:
        return _COMPACT[resolved]
    return {"off": _OFF, "balanced": _BALANCED, "high": _HIGH}[resolved]


# ── what is coming up ──────────────────────────────────────────────────────


def _when(day: date, today: date) -> str:
    delta = (day - today).days
    label = "today" if delta == 0 else "tomorrow" if delta == 1 else f"in {delta} days"
    return f"{label} ({_WEEKDAYS[day.weekday()]}, {day.isoformat()})"


def _clip(text: str) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= _NOTE_MAX_CHARS else flat[: _NOTE_MAX_CHARS - 1].rstrip() + "…"


def select_upcoming(
    notes: list[tuple[date, str]],
    *,
    today: date,
    horizon_days: int = HORIZON_DAYS,
    limit: int = UPCOMING_LIMIT,
) -> list[tuple[date, str]]:
    """The dated notes between today and the horizon, nearest first, without repeats."""
    end = today + timedelta(days=horizon_days)
    seen: set[tuple[date, str]] = set()
    rows: list[tuple[date, str]] = []
    for day, text in notes:
        key = (day, " ".join((text or "").split()))
        if today <= day <= end and key not in seen and key[1]:
            seen.add(key)
            rows.append(key)
    rows.sort(key=lambda row: row[0])
    return rows[:limit]


def upcoming_lines(
    notes: list[tuple[date, str]],
    *,
    today: date,
    horizon_days: int = HORIZON_DAYS,
    limit: int = UPCOMING_LIMIT,
) -> list[str]:
    """:func:`select_upcoming` as prompt lines: when, then the note."""
    rows = select_upcoming(notes, today=today, horizon_days=horizon_days, limit=limit)
    return [f"- {_when(day, today)}: {_clip(text)}" for day, text in rows]


def upcoming_notes(*, today: date | None = None) -> list[tuple[date, str]]:
    """What the assistant may bring up next, for the assistant page; ``[]`` on a fault."""
    try:
        from jarvis.memory.learning.notebook import dated_notes

        notes = dated_notes()
    except Exception:  # noqa: BLE001 — the page renders without the list
        log.debug("initiative: dated notes unavailable", exc_info=True)
        return []
    return select_upcoming(notes, today=today or date.today())


def upcoming_block(
    level: object = None,
    *,
    today: date | None = None,
    compact: bool = False,
) -> str:
    """Dated plans from the notebooks that fall in the next two weeks; ``""`` if none.

    Empty when initiative is off: the notes still reach the prompt through the
    notebook snapshot, but nothing points the model at them.
    """
    resolved = current_level() if level is None else normalize_level(level)
    if resolved == "off":
        return ""
    try:
        from jarvis.memory.learning.notebook import dated_notes

        notes = dated_notes()
    except Exception:  # noqa: BLE001 — a notebook fault must never break a prompt build
        log.debug("initiative: dated notes unavailable", exc_info=True)
        return ""
    lines = upcoming_lines(
        notes,
        today=today or date.today(),
        limit=_UPCOMING_LIMIT_COMPACT if compact else UPCOMING_LIMIT,
    )
    if not lines:
        return ""
    return (
        "## Coming up (dated plans from your notes)\n"
        "Background for anticipating what helps; mention one only as the initiative "
        "rule allows.\n" + "\n".join(lines)
    )


__all__ = [
    "DEFAULT_LEVEL",
    "HORIZON_DAYS",
    "LEVELS",
    "UPCOMING_LIMIT",
    "apply_level",
    "current_level",
    "directive",
    "normalize_level",
    "reset_applied_level",
    "select_upcoming",
    "upcoming_block",
    "upcoming_lines",
    "upcoming_notes",
]
