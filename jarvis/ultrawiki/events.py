"""UltraWiki episodic events — derivation logic with absolute time anchoring.

Design doc 01 (`uw_events`) and doc 03 ("cross-source reconstruction"). An
episodic question — *when did I have dinner with X in Y* — must hit a
**precomputed** row, because reconstructing it at read time from chat
fragments, a calendar entry and a photo's metadata cannot fit the voice
budget. This module is the deterministic half of that: it turns what the
per-document distillation ALREADY produced into event structures, and it does
the one thing a language model must never be trusted with — the arithmetic
that turns *"next Friday"* into an absolute instant.

Three rules the whole module exists to keep:

1. **No new model call, ever.** Events ride the distillation that already ran
   (``distill.PROMPT_VERSION`` 2 emits an ``events`` array in the SAME call).
   A corpus distilled under the old prompt still yields events through the
   legacy path below, which reads nothing but the stored distillation text.
2. **Time is stored absolute, never as text.** A relative expression is
   resolved against the source item's OWN timestamp at extraction time
   (design doc 01), so an event keeps meaning years later and after the
   sentence that produced it has scrolled away. **No anchor, no event**: an
   item whose timestamp does not parse produces nothing rather than an event
   floating in an unknown year.
3. **The model normalizes language, this module does the maths.** The prompt
   asks for ISO-8601 whenever the source states a date outright and otherwise
   for ONE token from a closed English vocabulary (``yesterday``,
   ``last friday``, ``3 days ago``, ...). A German, Spanish or Japanese source
   therefore arrives here already normalized, and the resolver below never
   needs a per-language phrase table — which is exactly what makes it work for
   every locale instead of the two somebody happened to test.

Everything here is pure, deterministic, stdlib-only and offline: no model, no
network, no clock (the anchor is always passed in), no database. The SQL side
lives in ``jarvis/ultrawiki/event_store.py``.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from jarvis.ultrawiki.types import content_hash_for

__all__ = [
    "ENTITY_CREATE_CONFIDENCE",
    "EVENT_KIND_VALUES",
    "EVENT_VERSION",
    "MAX_ENTITIES_CREATED_PER_ITEM",
    "MAX_EVENTS_PER_ITEM",
    "MAX_IDENTITY_NAMES_PER_ITEM",
    "MAX_IDENTITY_PROPOSALS_PER_ITEM",
    "MAX_PARTICIPANTS",
    "RELATIVE_VOCABULARY",
    "TIME_ANCHOR_VALUES",
    "TIME_PRECISION_VALUES",
    "DerivedEvent",
    "EventKind",
    "EventTime",
    "TimeAnchor",
    "TimePrecision",
    "coerce_kind",
    "derive_events",
    "format_occurred",
    "iso_utc",
    "parse_absolute",
    "parse_instant",
    "resolve_time",
    "scan_absolute_dates",
    "window_end",
]


# ---------------------------------------------------------------------------
# Canonical value sets (five-layer drift rule, AP-4 / BUG-008)
# ---------------------------------------------------------------------------


class EventKind(StrEnum):
    """What kind of episodic fact this is (design doc 01, ``event_type``).

    A deliberately SHORT closed list: the kinds a person actually asks
    questions about. ``OTHER`` is the honest bucket — an event whose kind is
    unknown is still worth having, because its date, participants and place
    are the answer to "when did that happen".
    """

    MEAL = "meal"
    TRAVEL = "travel"
    MEETING = "meeting"
    PURCHASE = "purchase"
    MILESTONE = "milestone"
    OTHER = "other"


class TimePrecision(StrEnum):
    """How sharply the source pinned the moment down.

    Precision is not decoration: it decides the END of the interval an event
    occupies, and therefore whether a range query matches it. "In March" is
    one event covering 31 days, not an event at midnight on the 1st.
    """

    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class TimeAnchor(StrEnum):
    """Where the absolute time came from — the honesty column.

    Distinguishing these is what keeps a confident-looking date from lying: a
    ``RECORDED`` event happened *around* when the message was written and is
    worth far less than an ``ABSOLUTE`` one the source spelled out.
    """

    #: The source stated a date/time outright ("2026-03-14", "14 March 2026").
    ABSOLUTE = "absolute"
    #: A relative expression resolved against the item's own timestamp.
    RELATIVE = "relative"
    #: Nothing was stated; the item's own timestamp IS the anchor.
    RECORDED = "recorded"


#: Extraction version stamped onto every row — bump when the derivation rules
#: change so a re-extraction pass can be told apart from old rows.
EVENT_VERSION = 1

#: Hard caps. One item never becomes a wall of events, and a hallucinated
#: participant list never becomes an entity flood on the write path.
MAX_EVENTS_PER_ITEM = 5
MAX_PARTICIPANTS = 12
MAX_TITLE_CHARS = 160
MAX_SUMMARY_CHARS = 400
MAX_PLACE_CHARS = 120

#: Legacy path only: at most this many dates are lifted out of a distillation
#: that predates the ``events`` key, so an itinerary full of dates does not
#: become an itinerary full of junk events.
MAX_LEGACY_EVENTS = 2

#: Confidence floor/ceiling and the values the two derivation paths assign
#: when the source offers none. The legacy path scores LOW on purpose: it
#: knows a date, and nothing about what actually happened.
DEFAULT_CONFIDENCE = 0.5
LEGACY_CONFIDENCE = 0.35

#: Per-ITEM identity budget. The caps above bound one EVENT; multiplied out
#: they leave one document free to spend 5 x 12 participants + 5 places = 65
#: identity resolutions, each of which scans up to
#: :data:`~jarvis.ultrawiki.identity.FUZZY_CANDIDATE_LIMIT` names and may
#: queue :data:`~jarvis.ultrawiki.identity.MAX_PROPOSALS` proposals — roughly
#: 300 pending rows from a SINGLE import, against the identity layer's design
#: property that the confirmation queue stays short enough for a human to
#: actually work through.
#:
#: The three budgets below bound the whole item instead, and each one is set
#: so that a genuinely busy real document still lands intact:
#:
#: - Names: one full :data:`MAX_PARTICIPANTS` roster (a dozen people is
#:   already an unusually crowded note) plus room for the places and a few
#:   names the other events add. Real documents repeat their people, so this
#:   is only ever reached by an extraction that invented names.
#: - Creations: the irreversible half — a new row in the People view outlives
#:   the guess that produced it — so it is bounded well below the name budget.
#: - Proposals: what one import may add to the confirmation queue. Ten pairs
#:   is already more than anyone triages in one sitting. Enforced by RESERVING
#:   a whole :data:`~jarvis.ultrawiki.identity.MAX_PROPOSALS` batch before each
#:   creating resolution, so the number is a hard ceiling rather than an
#:   average that a final batch can overshoot.
#:
#: Exceeding a budget is never an error: the excess names still LINK to
#: whoever the user already curated and are otherwise stored by their spelling
#: (searchable, nobody new invented). The drop is deterministic — document
#: order, first come first served — so the name budget selects the same prefix
#: on every pass over unchanged content. The creation budget converges instead
#: of repeating: a person an earlier pass created is simply linked by the next
#: one, which is what re-derivation is supposed to do.
MAX_IDENTITY_NAMES_PER_ITEM = 16
MAX_ENTITIES_CREATED_PER_ITEM = 8
MAX_IDENTITY_PROPOSALS_PER_ITEM = 10

#: An event below this confidence may LINK its participants and place to
#: entities that already exist, but never CREATES one. Creating is the
#: expensive direction: a new row shows up in the People view, seeds the merge
#: queue with its near-names, and outlives the guess that produced it — while
#: linking a name the user already curated costs nothing and is reversible by
#: re-derivation. So an uncertain event enriches what is known and never
#: invents anybody.
ENTITY_CREATE_CONFIDENCE = 0.5


# ---------------------------------------------------------------------------
# Absolute time parsing
# ---------------------------------------------------------------------------


def iso_utc(moment: datetime) -> str:
    """UTC ISO-8601 with a ``Z`` suffix and second resolution."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return (
        aware.astimezone(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


#: Month names the written forms use. English is the artifact language of this
#: repo (§1) AND the language the distillation prompt normalizes into, so this
#: table is a formatter, not a matcher for user text.
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTH_INDEX = {name.lower(): number for number, name in enumerate(_MONTHS, start=1)}
_MONTH_INDEX.update({name[:3].lower(): number for number, name in enumerate(_MONTHS, start=1)})

_MONTH_ALTERNATION = "|".join(sorted(_MONTH_INDEX, key=len, reverse=True))

#: ISO-ish instants: date, optional time, optional zone. The one format the
#: prompt asks for, and the only one a machine-written source ever emits.
_ISO_RE = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
    r"(?P<zone>Z|[+-]\d{2}:?\d{2})?)?"
)
_ISO_MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})$")
_ISO_YEAR_RE = re.compile(r"^(?P<year>\d{4})$")

#: Day-first dotted dates ("14.03.2026"). Dots are the one separator with an
#: unambiguous convention worldwide; slash dates are deliberately NOT parsed,
#: because 03/04/2026 is two different days on two continents and a personal
#: memory that guesses is worse than one that stays quiet.
_DOTTED_RE = re.compile(r"\b(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})\b")

#: Written English dates in both orders, with or without a year.
_WRITTEN_DMY_RE = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{_MONTH_ALTERNATION})"
    r"(?:\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)
_WRITTEN_MDY_RE = re.compile(
    rf"\b(?P<month>{_MONTH_ALTERNATION})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    r"(?:,?\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)

#: A clock time appended to any of the above ("at 19:30", "19:30", "7pm").
#: A BARE number never counts as a time — otherwise "3 days ago" would resolve
#: to 03:00 and state a made-up hour as a fact. One of the three explicit
#: markers must be present: an "at" prefix, a ":MM" component, or am/pm.
_CLOCK_RE = re.compile(
    r"\b(?P<prefix>at\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
    r"\s*(?P<ampm>am|pm)?\b",
    re.IGNORECASE,
)

#: Coarse dayparts the prompt may append instead of a clock time. Values are
#: the conventional midpoint of each; precision stays HOUR so the stored
#: interval still says "somewhere in that part of the day".
_DAYPARTS = {
    "morning": 9,
    "noon": 12,
    "midday": 12,
    "afternoon": 15,
    "evening": 19,
    "night": 21,
}

#: Written weekday names. Spelled out rather than taken from ``calendar.
#: day_name``, which follows the process LOCALE — a store indexed on a German
#: desktop and queried on an English one would otherwise hold two different
#: spellings of the same day.
_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _safe_date(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def _clock_from(text: str) -> tuple[int, int] | None:
    """Extract a wall-clock time from a fragment, if it states one."""
    lowered = text.lower()
    for word, hour in _DAYPARTS.items():
        if word in lowered:
            return hour, 0
    for match in _CLOCK_RE.finditer(text):
        if not (match.group("prefix") or match.group("minute") or match.group("ampm")):
            continue  # a bare number is a count, not a time
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        ampm = (match.group("ampm") or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    return None


def parse_instant(value: str) -> datetime | None:
    """Parse one ISO-8601 timestamp into an aware UTC datetime, or ``None``.

    Deliberately tolerant of the shapes connectors actually produce (``Z``,
    ``+02:00``, a bare date, a space instead of ``T``) and deliberately silent
    on everything else — an unparseable anchor means "no event", never "now".
    """
    text = str(value or "").strip()
    if not text:
        return None
    match = _ISO_RE.match(text)
    if match is None:
        return None
    base = _safe_date(
        int(match.group("year")), int(match.group("month")), int(match.group("day"))
    )
    if base is None:
        return None
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    second = int(match.group("second") or 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    moment = base.replace(hour=hour, minute=minute, second=second)
    zone = match.group("zone")
    if zone and zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
        moment = moment - sign * offset
    return moment


def parse_absolute(value: str) -> tuple[datetime, TimePrecision] | None:
    """Parse an absolute date/time expression into ``(instant, precision)``.

    Accepts ISO instants and dates, ``YYYY-MM``, ``YYYY``, dotted day-first
    dates, and written English dates in either order — each optionally
    followed by a clock time or a daypart word.
    """
    text = str(value or "").strip()
    if not text:
        return None

    iso_month = _ISO_MONTH_RE.match(text)
    if iso_month is not None:
        moment = _safe_date(int(iso_month.group("year")), int(iso_month.group("month")), 1)
        return None if moment is None else (moment, TimePrecision.MONTH)
    iso_year = _ISO_YEAR_RE.match(text)
    if iso_year is not None:
        moment = _safe_date(int(iso_year.group("year")), 1, 1)
        return None if moment is None else (moment, TimePrecision.YEAR)

    iso = _ISO_RE.match(text)
    if iso is not None:
        moment = parse_instant(text)
        if moment is None:
            return None
        if iso.group("hour") is None:
            tail = text[iso.end() :]
            clock = _clock_from(tail) if tail.strip() else None
            if clock is not None:
                return moment.replace(hour=clock[0], minute=clock[1]), TimePrecision.MINUTE
            return moment, TimePrecision.DAY
        return moment, TimePrecision.MINUTE

    found = _scan_one(text)
    if found is None:
        return None
    moment, precision, end = found
    if precision is TimePrecision.DAY:
        clock = _clock_from(text[end:])
        if clock is not None:
            return moment.replace(hour=clock[0], minute=clock[1]), TimePrecision.MINUTE
    return moment, precision


def _scan_one(text: str) -> tuple[datetime, TimePrecision, int] | None:
    """First dotted/written date in *text* as ``(instant, precision, end)``."""
    best: tuple[int, datetime, TimePrecision, int] | None = None
    for pattern in (_DOTTED_RE, _WRITTEN_DMY_RE, _WRITTEN_MDY_RE):
        match = pattern.search(text)
        if match is None:
            continue
        groups = match.groupdict()
        month_raw = groups.get("month") or ""
        month = (
            int(month_raw)
            if month_raw.isdigit()
            else _MONTH_INDEX.get(month_raw.lower(), 0)
        )
        year_raw = groups.get("year")
        if not year_raw:
            # A written date without a year cannot be anchored on its own; the
            # caller's anchor year would be a guess, and a guessed year is the
            # exact failure this module refuses to ship.
            continue
        moment = _safe_date(int(year_raw), month, int(groups.get("day") or 0))
        if moment is None:
            continue
        if best is None or match.start() < best[0]:
            best = (match.start(), moment, TimePrecision.DAY, match.end())
    if best is None:
        return None
    return best[1], best[2], best[3]


def scan_absolute_dates(
    text: str, *, limit: int = MAX_LEGACY_EVENTS
) -> list[tuple[datetime, TimePrecision]]:
    """Every distinct absolute date stated in *text*, in order of appearance.

    The legacy derivation path's only time signal: a distillation written
    before the ``events`` key still SAYS when things happened, in prose.
    """
    out: list[tuple[datetime, TimePrecision]] = []
    seen: set[str] = set()
    for pattern in (_ISO_RE, _DOTTED_RE, _WRITTEN_DMY_RE, _WRITTEN_MDY_RE):
        for match in pattern.finditer(text or ""):
            parsed = parse_absolute(match.group(0))
            if parsed is None:
                continue
            moment, precision = parsed
            key = f"{iso_utc(moment)}|{precision}"
            if key in seen:
                continue
            seen.add(key)
            out.append((moment, precision))
            if len(out) >= max(1, int(limit)):
                return out
    return out


# ---------------------------------------------------------------------------
# Relative time resolution — the closed vocabulary
# ---------------------------------------------------------------------------

#: What the distillation prompt is allowed to emit when the source used a
#: relative expression. Documented here because prompt and resolver must move
#: together: a token the prompt invents and this table does not know resolves
#: to nothing and the event falls back to the item's own timestamp.
RELATIVE_VOCABULARY: tuple[str, ...] = (
    "today",
    "yesterday",
    "tomorrow",
    "this <weekday>",
    "last <weekday>",
    "next <weekday>",
    "last week",
    "next week",
    "last month",
    "next month",
    "last year",
    "next year",
    "<n> days ago",
    "<n> weeks ago",
    "<n> months ago",
    "<n> years ago",
    "in <n> days",
    "in <n> weeks",
    "in <n> months",
    "in <n> years",
)

_REL_WEEKDAY_RE = re.compile(
    r"\b(?P<which>this|last|next|past|coming)\s+(?P<weekday>"
    + "|".join(_WEEKDAYS)
    + r")\b",
    re.IGNORECASE,
)
_REL_AGO_RE = re.compile(
    r"\b(?P<count>\d{1,3})\s+(?P<unit>day|days|week|weeks|month|months|year|years)\s+ago\b",
    re.IGNORECASE,
)
_REL_IN_RE = re.compile(
    r"\bin\s+(?P<count>\d{1,3})\s+(?P<unit>day|days|week|weeks|month|months|year|years)\b",
    re.IGNORECASE,
)
_REL_UNIT_RE = re.compile(
    r"\b(?P<which>last|next|this)\s+(?P<unit>week|month|year)\b", re.IGNORECASE
)


def _start_of_day(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _shift_months(moment: datetime, months: int) -> datetime:
    total = (moment.year * 12 + moment.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return moment.replace(year=year, month=month, day=min(moment.day, last_day))


def _resolve_relative(
    text: str, anchor: datetime
) -> tuple[datetime, TimePrecision] | None:
    """Resolve one closed-vocabulary relative expression against *anchor*."""
    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return None
    day = _start_of_day(anchor)

    if "today" in lowered or "tonight" in lowered:
        return day, TimePrecision.DAY
    if "yesterday" in lowered:
        return day - timedelta(days=1), TimePrecision.DAY
    if "tomorrow" in lowered:
        return day + timedelta(days=1), TimePrecision.DAY

    weekday = _REL_WEEKDAY_RE.search(lowered)
    if weekday is not None:
        which = weekday.group("which")
        target = _WEEKDAYS[weekday.group("weekday")]
        delta = (target - day.weekday()) % 7
        if which in {"last", "past"}:
            back = (day.weekday() - target) % 7 or 7
            return day - timedelta(days=back), TimePrecision.DAY
        if which in {"next", "coming"}:
            return day + timedelta(days=delta or 7), TimePrecision.DAY
        # "this <weekday>" = the one inside the anchor's own Monday-Sunday week.
        return day - timedelta(days=day.weekday()) + timedelta(days=target), TimePrecision.DAY

    ago = _REL_AGO_RE.search(lowered)
    if ago is not None:
        return _apply_offset(day, int(ago.group("count")), ago.group("unit"), sign=-1)
    ahead = _REL_IN_RE.search(lowered)
    if ahead is not None:
        return _apply_offset(day, int(ahead.group("count")), ahead.group("unit"), sign=1)

    unit = _REL_UNIT_RE.search(lowered)
    if unit is not None:
        which = unit.group("which")
        step = 0 if which == "this" else (-1 if which == "last" else 1)
        kind = unit.group("unit")
        if kind == "week":
            monday = day - timedelta(days=day.weekday())
            return monday + timedelta(weeks=step), TimePrecision.WEEK
        if kind == "month":
            shifted = _shift_months(day, step)
            return shifted.replace(day=1), TimePrecision.MONTH
        return day.replace(month=1, day=1, year=day.year + step), TimePrecision.YEAR
    return None


def _apply_offset(
    day: datetime, count: int, unit: str, *, sign: int
) -> tuple[datetime, TimePrecision]:
    unit = unit.rstrip("s")
    if unit == "day":
        return day + sign * timedelta(days=count), TimePrecision.DAY
    if unit == "week":
        return day + sign * timedelta(weeks=count), TimePrecision.WEEK
    if unit == "month":
        return _shift_months(day, sign * count), TimePrecision.MONTH
    return day.replace(year=day.year + sign * count), TimePrecision.YEAR


# ---------------------------------------------------------------------------
# The resolved interval
# ---------------------------------------------------------------------------


def window_end(moment: datetime, precision: TimePrecision) -> datetime:
    """Last instant still inside the window *precision* describes.

    This is what makes "in March" match a query for the 14th: an event is not
    a point unless the source said so.
    """
    if precision is TimePrecision.MINUTE:
        return moment.replace(second=59)
    if precision is TimePrecision.HOUR:
        return moment.replace(minute=59, second=59)
    if precision is TimePrecision.DAY:
        return moment.replace(hour=23, minute=59, second=59)
    if precision is TimePrecision.WEEK:
        return (moment + timedelta(days=6)).replace(hour=23, minute=59, second=59)
    if precision is TimePrecision.MONTH:
        last_day = calendar.monthrange(moment.year, moment.month)[1]
        return moment.replace(day=last_day, hour=23, minute=59, second=59)
    return moment.replace(month=12, day=31, hour=23, minute=59, second=59)


@dataclass(frozen=True, slots=True)
class EventTime:
    """One event's absolute, bi-temporal position.

    ``occurred_at``/``occurred_end`` are VALID time (when it happened in the
    world); ``recorded_at`` is TRANSACTION time (when the source recorded the
    statement). Keeping them apart is what lets a Monday message about a
    Friday dinner answer both "when was the dinner" and "when did I plan it".
    """

    occurred_at: str
    occurred_end: str
    precision: TimePrecision
    anchor: TimeAnchor
    recorded_at: str

    @classmethod
    def build(
        cls,
        moment: datetime,
        precision: TimePrecision,
        anchor: TimeAnchor,
        *,
        recorded_at: datetime,
        end: datetime | None = None,
    ) -> EventTime:
        finish = end if end is not None and end >= moment else window_end(moment, precision)
        return cls(
            occurred_at=iso_utc(moment),
            occurred_end=iso_utc(finish),
            precision=precision,
            anchor=anchor,
            recorded_at=iso_utc(recorded_at),
        )


def resolve_time(
    raw_when: str,
    *,
    recorded_at: datetime,
    raw_end: str = "",
) -> EventTime:
    """Turn one ``when`` expression into an absolute interval.

    Precedence: an absolute expression wins, a closed-vocabulary relative one
    is resolved against *recorded_at*, and anything else falls back to
    *recorded_at* itself with :attr:`TimeAnchor.RECORDED` — honest about the
    fact that the item's own timestamp is all that is known.
    """
    text = str(raw_when or "").strip()
    absolute = parse_absolute(text) if text else None
    if absolute is not None:
        moment, precision = absolute
        anchor = TimeAnchor.ABSOLUTE
    else:
        relative = _resolve_relative(text, recorded_at) if text else None
        if relative is not None:
            moment, precision = relative
            clock = _clock_from(text)
            if clock is not None and precision is TimePrecision.DAY:
                moment = moment.replace(hour=clock[0], minute=clock[1])
                precision = TimePrecision.MINUTE
            anchor = TimeAnchor.RELATIVE
        else:
            moment, precision, anchor = recorded_at, TimePrecision.MINUTE, TimeAnchor.RECORDED

    end: datetime | None = None
    finish_text = str(raw_end or "").strip()
    if finish_text:
        finish = parse_absolute(finish_text)
        if finish is None:
            resolved = _resolve_relative(finish_text, recorded_at)
            finish = resolved if resolved is not None else None
        if finish is not None:
            end = window_end(finish[0], finish[1])
    return EventTime.build(
        moment, precision, anchor, recorded_at=recorded_at, end=end
    )


def format_occurred(occurred_at: str, precision: TimePrecision | str) -> str:
    """Human-readable date label for one event ("14 March 2026 at 19:30").

    Used in the search card and by every surface that verbalizes an event, so
    a date is written ONE way across UI, CLI, chat and voice.
    """
    moment = parse_instant(occurred_at)
    if moment is None:
        return str(occurred_at or "")
    level = TimePrecision(str(precision)) if precision else TimePrecision.DAY
    if level is TimePrecision.YEAR:
        return f"{moment.year}"
    if level is TimePrecision.MONTH:
        return f"{_MONTHS[moment.month - 1]} {moment.year}"
    day_label = f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}"
    if level is TimePrecision.WEEK:
        return f"week of {day_label}"
    if level is TimePrecision.DAY:
        return day_label
    if level is TimePrecision.HOUR:
        return f"{day_label} around {moment.hour:02d}:00"
    return f"{day_label} at {moment.hour:02d}:{moment.minute:02d}"


# ---------------------------------------------------------------------------
# The derived event
# ---------------------------------------------------------------------------


def coerce_kind(value: Any) -> EventKind:
    """Map whatever the distillation said onto the closed kind list."""
    text = str(value or "").strip().lower()
    for kind in EventKind:
        if text == kind.value:
            return kind
    return EventKind.OTHER


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _names(value: Any) -> list[str]:
    """Salvage a participant list; scalars wrap, junk and duplicates drop."""
    raw: Sequence[Any]
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple)):
        raw = value
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        name = _clean(entry, MAX_TITLE_CHARS)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= MAX_PARTICIPANTS:
            break
    return out


@dataclass(frozen=True, slots=True)
class DerivedEvent:
    """One episodic fact, ready to be stored — absolute in time by construction."""

    kind: EventKind
    title: str
    summary: str
    time: EventTime
    participants: tuple[str, ...] = ()
    place: str = ""
    confidence: float = DEFAULT_CONFIDENCE
    extraction_version: int = EVENT_VERSION

    @property
    def dedupe_key(self) -> str:
        """Stable identity of this event WITHIN its item.

        Re-deriving an unchanged item must land on the same key, which is what
        makes ``replace_events`` idempotent instead of duplicating on every
        pipeline pass.
        """
        return content_hash_for(
            str(self.kind),
            self.time.occurred_at,
            self.title.casefold(),
            self.place.casefold(),
            "|".join(sorted(name.casefold() for name in self.participants)),
        )[:32]

    @property
    def date_label(self) -> str:
        return format_occurred(self.time.occurred_at, self.time.precision)

    def search_text(self) -> str:
        """The keyword-index card: everything a person might ask this event by.

        Deliberately redundant — the label, the ISO date, the weekday, the
        month name and the bare year all appear, so "March", "2026" and
        "14.03" reach the same row. This is the text the FTS leg indexes.
        """
        moment = parse_instant(self.time.occurred_at)
        parts = [self.title, str(self.kind), self.date_label]
        if moment is not None:
            parts.extend(
                [
                    f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}",
                    f"{moment.day:02d}.{moment.month:02d}.{moment.year:04d}",
                    _WEEKDAY_NAMES[moment.weekday()],
                    _MONTHS[moment.month - 1],
                    str(moment.year),
                ]
            )
        if self.participants:
            parts.append("with " + ", ".join(self.participants))
        if self.place:
            parts.append("in " + self.place)
        if self.summary:
            parts.append(self.summary)
        return " ".join(part for part in parts if part).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "title": self.title,
            "summary": self.summary,
            "occurred_at": self.time.occurred_at,
            "occurred_end": self.time.occurred_end,
            "occurred_precision": str(self.time.precision),
            "time_anchor": str(self.time.anchor),
            "recorded_at": self.time.recorded_at,
            "date_label": self.date_label,
            "participants": list(self.participants),
            "place": self.place,
            "confidence": round(float(self.confidence), 3),
            "extraction_version": int(self.extraction_version),
        }


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _confidence(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(0.0, min(1.0, number))


def _from_payload(
    entry: Mapping[str, Any],
    *,
    recorded: datetime,
    fallback_title: str,
    fallback_summary: str,
) -> DerivedEvent | None:
    """One entry of the distillation's ``events`` array, or ``None`` if junk."""
    title = _clean(entry.get("title"), MAX_TITLE_CHARS) or fallback_title
    place = _clean(entry.get("where") or entry.get("place"), MAX_PLACE_CHARS)
    participants = _names(entry.get("participants") or entry.get("people"))
    when = _clean(entry.get("when"), MAX_TITLE_CHARS)
    kind = coerce_kind(entry.get("kind") or entry.get("event_type"))
    if not title and not place and not participants and not when:
        return None  # an entry with nothing in it is not an event
    time = resolve_time(
        when, recorded_at=recorded, raw_end=_clean(entry.get("when_end"), MAX_TITLE_CHARS)
    )
    summary = _clean(entry.get("summary"), MAX_SUMMARY_CHARS) or fallback_summary
    return DerivedEvent(
        kind=kind,
        title=title or str(kind),
        summary=summary,
        time=time,
        participants=tuple(participants),
        place=place,
        confidence=_confidence(entry.get("confidence"), DEFAULT_CONFIDENCE),
    )


def _legacy_events(
    distill: Mapping[str, Any],
    *,
    recorded: datetime,
    fallback_title: str,
) -> list[DerivedEvent]:
    """Events from a distillation that predates the ``events`` key.

    Reads nothing but text that is ALREADY stored — no model call, no
    re-distillation — and fires only where the distillation states an
    absolute date outright. A date is the one episodic signal that can be
    recognized without understanding the sentence; guessing a KIND from words
    would need a per-language lexicon, and shipping one for two languages is
    how a feature quietly stops existing for everyone else.

    **No participants.** The pre-v2 document has no field that means "people
    who were there". ``entities`` is defined as *mentioned people, places,
    organizations, projects, systems* — one bag of everything the item names —
    so reading it as a guest list turns cities, vendors and file formats into
    people, and the People view fills up with software. An event with a date
    and no names is worth having; an event with the wrong names is not.
    """
    question = _clean(distill.get("question"), MAX_TITLE_CHARS)
    summary = _clean(distill.get("summary"), MAX_SUMMARY_CHARS)
    resolution = _clean(distill.get("resolution"), MAX_SUMMARY_CHARS)
    haystack = " ".join(part for part in (question, summary, resolution) if part)
    dates = scan_absolute_dates(haystack)
    if not dates:
        return []
    title = question or fallback_title or summary[:MAX_TITLE_CHARS]
    return [
        DerivedEvent(
            kind=EventKind.OTHER,
            title=title or "Recorded event",
            summary=summary,
            time=EventTime.build(
                moment, precision, TimeAnchor.ABSOLUTE, recorded_at=recorded
            ),
            confidence=LEGACY_CONFIDENCE,
        )
        for moment, precision in dates
    ]


def derive_events(
    *,
    distill: Mapping[str, Any] | None,
    title: str = "",
    recorded_at: str = "",
    max_events: int = MAX_EVENTS_PER_ITEM,
) -> list[DerivedEvent]:
    """Derive every episodic event one distilled item carries.

    *distill* is the parsed distillation JSON exactly as the pipeline already
    holds it; *recorded_at* is the item's own ``timestamp_utc`` and is the
    ONLY clock involved — without a parseable one this returns nothing, by
    design (rule 2 in the module docstring).

    Never calls a model, never touches the network, and is safe to run on the
    write path inside the distillation stage that produced its input.
    """
    recorded = parse_instant(recorded_at)
    if recorded is None or not distill:
        return []
    fallback_title = _clean(title, MAX_TITLE_CHARS)
    fallback_summary = _clean(distill.get("summary"), MAX_SUMMARY_CHARS)

    raw_events = distill.get("events")
    events: list[DerivedEvent] = []
    if isinstance(raw_events, (list, tuple)):
        for entry in raw_events:
            if not isinstance(entry, Mapping):
                continue
            derived = _from_payload(
                entry,
                recorded=recorded,
                fallback_title=fallback_title,
                fallback_summary=fallback_summary,
            )
            if derived is not None:
                events.append(derived)
            if len(events) >= max(1, int(max_events)):
                break
    if events:
        return _dedupe(events)
    return _dedupe(
        _legacy_events(distill, recorded=recorded, fallback_title=fallback_title)
    )


def _dedupe(events: Sequence[DerivedEvent]) -> list[DerivedEvent]:
    seen: set[str] = set()
    out: list[DerivedEvent] = []
    for event in events:
        key = event.dedupe_key
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


#: Convenience for schema derivation (five-layer rule): the CHECK lists both
#: dialects build their DDL from, never retyped by hand.
EVENT_KIND_VALUES: tuple[str, ...] = tuple(kind.value for kind in EventKind)
TIME_PRECISION_VALUES: tuple[str, ...] = tuple(level.value for level in TimePrecision)
TIME_ANCHOR_VALUES: tuple[str, ...] = tuple(anchor.value for anchor in TimeAnchor)
