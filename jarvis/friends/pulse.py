"""UsagePulse — the shareable answer to "how much do you actually use this?".

The product question is social: a friends list where every row reads "no data"
is not a friends list, it is a contact book. The privacy question is that usage
data is behavioural data — when someone works, how long, how consistently.

Design
------
The pulse resolves that tension with three rules, in this order:

1. **Counts, never content.** Every field is an integer, a small enum, or a
   coarse level. There is no field that can carry a title, a transcript, a path
   or an identifier, so no future caller can put one there. The one field that
   *looks* like a timeline — :attr:`UsagePulse.activity_levels` — carries levels
   0–3, not counts, precisely so a friend cannot reconstruct a working day from
   it.
2. **Graded by the profile the user already set.** The same
   ``minimal``/``standard``/``detailed`` choice that governs live status
   (:mod:`jarvis.friends.status_filter`) governs the pulse. One control, one
   mental model — a second, separate privacy switch is a switch someone forgets
   to check.
3. **Redacted before it leaves.** :func:`redact_pulse` runs on the *sending*
   side. The receiving instance never has the full pulse to accidentally render,
   log, or forward.

:data:`PULSE_NEVER` is the counterpart to the status hard blacklist: fields that
stay unshared no matter which profile is active and no matter what a custom
whitelist asks for. It exists so the "custom whitelist" escape hatch cannot be
used to widen the pulse past what the design allows (AP-1/AP-11,
constraint-self-bypass protection).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import StatusProfile

log = logging.getLogger(__name__)

LastActiveBucket = Literal["now", "today", "this_week", "quiet", "unknown"]
"""How recently the person used their assistant — deliberately coarse.

An exact "last seen" timestamp is a presence tracker; four buckets answer
"is it worth writing to them right now" without one.
"""

#: Days of level history a pulse carries. Two weeks is enough for a heat strip
#: and short enough that it is not a behavioural record.
PULSE_HISTORY_DAYS = 14

#: Level thresholds for one day of activity. The exact counts stay local.
_LEVEL_THRESHOLDS: tuple[int, ...] = (1, 3, 7)

#: "Active now" window. Fifteen minutes is a session, not a heartbeat.
_ACTIVE_NOW_NS = 15 * 60 * 1_000_000_000
_TODAY_NS = 24 * 60 * 60 * 1_000_000_000
_THIS_WEEK_NS = 7 * _TODAY_NS

#: Fields released per profile. Cumulative on purpose: "standard" is a superset
#: of "minimal", so raising the profile can never *remove* something a friend
#: already sees, which is the only way the levels stay explainable in one line.
PULSE_FIELDS: dict[StatusProfile, frozenset[str]] = {
    "minimal": frozenset(
        {"last_active_bucket", "streak_days", "active_days_30"}
    ),
    "standard": frozenset(
        {
            "last_active_bucket",
            "streak_days",
            "active_days_30",
            "longest_streak_days",
            "sessions_today",
            "sessions_7d",
            "activity_levels",
        }
    ),
    "detailed": frozenset(
        {
            "last_active_bucket",
            "streak_days",
            "active_days_30",
            "longest_streak_days",
            "sessions_today",
            "sessions_7d",
            "activity_levels",
            "missions_7d",
            "agent_tasks_7d",
            "dictated_words_7d",
            "member_since_days",
        }
    ),
}

#: Never shared, whatever the profile or the custom whitelist says. These are
#: the fields the local UI shows about *yourself* and that carry more precision
#: than a friend has any reason to hold.
PULSE_NEVER: frozenset[str] = frozenset(
    {
        "last_active_ns",  # exact wall-clock presence
        "generated_at_ns",  # travels in the envelope, not in the payload
        "node_id",
        "display_name",
    }
)


class UsagePulse(BaseModel):
    """A friend's usage, as far as they chose to share it.

    Fields absent from the sharing profile are ``None`` rather than zero: a
    person who shares nothing and a person who did nothing must not look the
    same, or the UI ends up telling a friendly lie about both.
    """

    model_config = ConfigDict(frozen=False)

    #: Which grade produced this payload. The receiving UI shows it, so an empty
    #: card reads as "they share little", not as "this feature is broken".
    profile: StatusProfile = "minimal"

    last_active_bucket: LastActiveBucket = "unknown"
    streak_days: int | None = None
    longest_streak_days: int | None = None
    active_days_30: int | None = None
    sessions_today: int | None = None
    sessions_7d: int | None = None
    missions_7d: int | None = None
    agent_tasks_7d: int | None = None
    dictated_words_7d: int | None = None
    member_since_days: int | None = None

    #: One level per day, oldest first, ``PULSE_HISTORY_DAYS`` long. Values are
    #: 0–3 activity levels, never counts.
    activity_levels: list[int] | None = None

    #: Set on the receiving side from the envelope, so a stale card can be
    #: labelled as stale instead of silently presented as current.
    fetched_at_ns: int = Field(default=0)

    def is_empty(self) -> bool:
        """True when the profile released nothing but the bucket."""
        return all(
            getattr(self, name) is None
            for name in (
                "streak_days",
                "active_days_30",
                "sessions_7d",
                "activity_levels",
            )
        )


class LocalPulseSources(BaseModel):
    """What :func:`build_local_pulse` was able to read, for honest degradation.

    A missing dictation sidecar is not an error — it means the user never
    dictated. The UI needs to tell those two apart, so the flags travel with
    the pulse locally (they are stripped before sending).
    """

    model_config = ConfigDict(frozen=False)

    has_activity_ledger: bool = False
    has_dictation_stats: bool = False


def activity_level(total: int) -> int:
    """One day's total count -> a 0–3 level.

    Bucketing is the whole privacy trick of the heat strip: "level 2" tells a
    friend the day was busy; it does not tell them you ran eleven missions
    between 23:00 and 02:00.
    """
    value = max(0, int(total or 0))
    level = 0
    for threshold in _LEVEL_THRESHOLDS:
        if value >= threshold:
            level += 1
    return level


def last_active_bucket(last_active_ns: int, *, now_ns: int | None = None) -> LastActiveBucket:
    """Coarse recency bucket. ``0`` (never recorded) reads as ``unknown``."""
    stamp = int(last_active_ns or 0)
    if stamp <= 0:
        return "unknown"
    now = int(now_ns if now_ns is not None else time.time_ns())
    delta = now - stamp
    if delta < 0:
        # A peer's clock ran ahead. Treat it as current rather than as a
        # negative age that would fall through to "quiet".
        return "now"
    if delta <= _ACTIVE_NOW_NS:
        return "now"
    if delta <= _TODAY_NS:
        return "today"
    if delta <= _THIS_WEEK_NS:
        return "this_week"
    return "quiet"


def build_local_pulse(
    *,
    ledger: Any | None = None,
    now_ns: int | None = None,
    member_since_ns: int | None = None,
) -> tuple[UsagePulse, LocalPulseSources]:
    """Assemble this instance's own full pulse from the local count stores.

    Never raises: any store that cannot be read contributes nothing and is
    reported as absent in the returned :class:`LocalPulseSources`.
    """
    now = int(now_ns if now_ns is not None else time.time_ns())
    sources = LocalPulseSources()
    pulse = UsagePulse(profile="detailed", fetched_at_ns=now)

    days: dict[str, Any] = {}
    last_active = 0
    try:
        from .activity import ActivityLedger

        store = ledger if ledger is not None else ActivityLedger()
        days, last_active = store.load()
        sources.has_activity_ledger = bool(days) or last_active > 0
        current, longest = store.streaks()
    except Exception:  # noqa: BLE001 — a missing ledger is "no activity", never a 500
        log.debug("socials pulse: activity ledger unavailable", exc_info=True)
        current, longest = 0, 0

    day_keys = _recent_day_keys(now, PULSE_HISTORY_DAYS)
    pulse.activity_levels = [
        activity_level(getattr(days.get(key), "total", 0)) for key in day_keys
    ]
    week_keys = day_keys[-7:]
    pulse.sessions_today = _sum_metric(days, day_keys[-1:], "voice_sessions")
    pulse.sessions_7d = _sum_metric(days, week_keys, "voice_sessions")
    pulse.missions_7d = _sum_metric(days, week_keys, "missions")
    pulse.agent_tasks_7d = _sum_metric(days, week_keys, "agent_tasks")
    pulse.active_days_30 = _active_days(days, _recent_day_keys(now, 30))
    pulse.streak_days = current
    pulse.longest_streak_days = longest
    pulse.last_active_bucket = last_active_bucket(last_active, now_ns=now)

    words, had_dictation = _dictated_words_7d(week_keys)
    sources.has_dictation_stats = had_dictation
    pulse.dictated_words_7d = words

    since = int(member_since_ns or 0) or _first_day_ns(days)
    if since:
        pulse.member_since_days = max(0, int((now - since) // _TODAY_NS))

    return pulse, sources


def redact_pulse(
    pulse: UsagePulse,
    profile: StatusProfile,
    custom_whitelist: list[str] | None = None,
) -> UsagePulse:
    """Return a copy carrying only what ``profile`` releases.

    A ``custom_whitelist`` may widen the profile — it can never reach a field in
    :data:`PULSE_NEVER`, and it can never invent a field the pulse does not have.
    Everything else is set to ``None``, which the UI renders as "not shared"
    rather than as zero.
    """
    allowed = PULSE_FIELDS.get(profile)
    if allowed is None:
        allowed = PULSE_FIELDS["minimal"]
    if custom_whitelist:
        allowed = allowed | frozenset(str(name) for name in custom_whitelist)
    allowed = allowed - PULSE_NEVER

    out = UsagePulse(profile=profile, fetched_at_ns=pulse.fetched_at_ns)
    for name in UsagePulse.model_fields:
        if name in {"profile", "fetched_at_ns"}:
            continue
        if name == "last_active_bucket":
            # The bucket is the one field every profile releases — without it a
            # friends list cannot tell "quiet" from "not shared" at all, and the
            # value is already coarse by construction.
            out.last_active_bucket = pulse.last_active_bucket
            continue
        if name in allowed:
            setattr(out, name, getattr(pulse, name))
    return out


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _recent_day_keys(now_ns: int, count: int) -> list[str]:
    """The last ``count`` LOCAL day keys, oldest first, ending today."""
    from datetime import UTC, datetime, timedelta

    try:
        today = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=UTC).astimezone().date()
    except (OSError, OverflowError, ValueError, TypeError):
        today = datetime.now().astimezone().date()
    span = max(1, int(count))
    return [(today - timedelta(days=offset)).isoformat() for offset in range(span - 1, -1, -1)]


def _first_day_ns(days: dict[str, Any]) -> int:
    """Epoch-ns of the earliest recorded day — "using this since".

    Derived rather than stored: an install date field would be one more thing
    to migrate, and the first day someone actually used the assistant is the
    more honest answer anyway.
    """
    if not days:
        return 0
    from datetime import date as _date
    from datetime import datetime
    from datetime import time as _time

    try:
        earliest = min(_date.fromisoformat(key) for key in days)
    except (TypeError, ValueError):
        return 0
    try:
        moment = datetime.combine(earliest, _time.min).astimezone()
        return int(moment.timestamp() * 1_000_000_000)
    except (OSError, OverflowError, ValueError):
        return 0


def _sum_metric(days: dict[str, Any], keys: list[str], metric: str) -> int:
    total = 0
    for key in keys:
        day = days.get(key)
        if day is None:
            continue
        total += max(0, int(getattr(day, metric, 0) or 0))
    return total


def _active_days(days: dict[str, Any], keys: list[str]) -> int:
    return sum(1 for key in keys if getattr(days.get(key), "total", 0) > 0)


def _dictated_words_7d(week_keys: list[str]) -> tuple[int, bool]:
    """Words dictated across the given days, and whether the sidecar existed.

    Imported lazily: this module is read by REST handlers, and the dictation
    package pulls in the history locks and the cleanup tables.
    """
    try:
        from jarvis.dictation.stats import DictationStats

        stats = DictationStats()
        if not stats.exists:
            return 0, False
        _, days = stats.load()
    except Exception:  # noqa: BLE001 — a missing sidecar is "no words", never a 500
        log.debug("socials pulse: dictation stats unavailable", exc_info=True)
        return 0, False
    total = 0
    for key in week_keys:
        day = days.get(key)
        if day is not None:
            total += max(0, int(getattr(day, "words", 0) or 0))
    return total, True


__all__ = [
    "PULSE_FIELDS",
    "PULSE_HISTORY_DAYS",
    "PULSE_NEVER",
    "LastActiveBucket",
    "LocalPulseSources",
    "UsagePulse",
    "activity_level",
    "build_local_pulse",
    "last_active_bucket",
    "redact_pulse",
]
