"""Lifetime dictation totals — a never-pruned sidecar next to the history.

Why a second file instead of counting the history
-------------------------------------------------
The history is a deliberately small rolling window (200 entries / 30 days by
default) so that the most sensitive text this application holds ages out on
its own. Deriving "words dictated" or a "day streak" from that window would
quietly lie: the totals would stop growing once the window filled, and a
streak could never exceed the retention setting no matter how long someone had
been using the feature.

So the counters live in their own sidecar, ``dictation_stats.json``, written
next to the history file. It stores **no text at all** — only per-day counts
and durations — which is why it can be kept forever without becoming the
privacy problem the history window exists to avoid.

Two details that are easy to get wrong
--------------------------------------
1. **Days are LOCAL days.** Entries are timestamped in UTC, but "did I dictate
   today" is a question about the user's wall clock. Bucketing by UTC date
   shreds the streak for everyone east of UTC — their evening work lands on
   tomorrow — and for everyone west of it their morning lands on yesterday.
2. **Today gets grace.** Before the first dictation of the day the streak
   counts back from *yesterday*, so the badge reads "6 days" over breakfast
   instead of a demoralising and simply wrong 0. It only reaches 0 once
   yesterday was quiet too. Same semantics as the board's activity streak.

This is an information badge, nothing else: no popup, no notification, no
"you lost your streak". A number that can punish you is a different product.

Storage pattern mirrors the history sidecar: atomic tempfile + ``os.replace``,
so a crash mid-write never leaves a torn file, and every public method
degrades to "no stats" instead of raising.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jarvis.dictation._locks import store_lock

log = logging.getLogger(__name__)

#: How many days the API hands to the UI. Enough for a year-ish of sparkline
#: without shipping a decade of JSON on every poll.
DEFAULT_BY_DAY_LIMIT = 90

#: Upper bound on the retained day buckets. At ~40 bytes a day this is a few
#: hundred kilobytes after ten years; the cap exists so the file cannot grow
#: without any bound at all, not because the data is expensive.
MAX_DAYS_RETAINED = 3_650


@dataclass(frozen=True, slots=True)
class DayStat:
    """One local calendar day of dictation activity."""

    date: str
    dictations: int = 0
    words: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "dictations": self.dictations,
            "words": self.words,
            "seconds": round(self.seconds, 2),
        }


# ----------------------------------------------------------------------
# Day bucketing
# ----------------------------------------------------------------------


def today_key() -> str:
    """Today's LOCAL calendar date as ``YYYY-MM-DD``."""
    return datetime.now().astimezone().date().isoformat()


def local_day(created_at: str | None) -> str | None:
    """ISO timestamp -> the LOCAL calendar day it belongs to.

    A naive timestamp is read as UTC (that is what the history writes), then
    converted to the local zone before the date is taken. Anything unparseable
    returns ``None`` so the caller can skip it rather than mis-bucket it.
    """
    if not created_at:
        return None
    try:
        moment = datetime.fromisoformat(str(created_at))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        from datetime import UTC

        moment = moment.replace(tzinfo=UTC)
    try:
        return moment.astimezone().date().isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _parse_day(value: str) -> _date | None:
    try:
        return _date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def current_streak(active_days: Iterable[str]) -> int:
    """Consecutive local days up to today with at least one dictation.

    Grace rule: a quiet *today* does not break the run — the count starts from
    yesterday instead. The streak only reads 0 once yesterday was quiet too.
    """
    days = {d for d in active_days if d}
    if not days:
        return 0
    cursor = datetime.now().astimezone().date()
    if cursor.isoformat() not in days:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def longest_streak(active_days: Iterable[str]) -> int:
    """The best run of consecutive active days ever recorded."""
    parsed = sorted({d for d in (_parse_day(v) for v in active_days) if d is not None})
    longest = 0
    run = 0
    previous: _date | None = None
    for day in parsed:
        run = run + 1 if previous is not None and (day - previous).days == 1 else 1
        longest = max(longest, run)
        previous = day
    return longest


def _wpm(words: int, seconds: float) -> float:
    """Words per minute, or ``0.0`` when there is no time to divide by."""
    if seconds <= 0.0:
        return 0.0
    return round(float(words) / (float(seconds) / 60.0), 1)


def _summary_payload(
    *,
    source: str,
    totals: dict[str, Any],
    days: dict[str, DayStat],
    by_day_limit: int,
) -> dict[str, Any]:
    key = today_key()
    today = days.get(key, DayStat(date=key))
    ordered = sorted(days.values(), key=lambda d: d.date, reverse=True)
    active = [d.date for d in days.values() if d.dictations > 0]
    limit = max(0, int(by_day_limit))
    return {
        "source": source,
        "totals": {
            "dictations": int(totals.get("dictations", 0)),
            "words": int(totals.get("words", 0)),
            "seconds": round(float(totals.get("seconds", 0.0)), 2),
            "wpm": _wpm(
                int(totals.get("words", 0)), float(totals.get("seconds", 0.0))
            ),
        },
        "today": {"dictations": today.dictations, "words": today.words},
        "streak": {
            "current_days": current_streak(active),
            "longest_days": longest_streak(active),
        },
        "by_day": [d.to_dict() for d in ordered[:limit]],
    }


# ----------------------------------------------------------------------
# The sidecar
# ----------------------------------------------------------------------


def default_stats_path() -> Path:
    """``<user data>/data/dictation_stats.json``."""
    from jarvis.core.paths import user_data_dir

    return Path(user_data_dir()) / "data" / "dictation_stats.json"


class DictationStats:
    """Per-day and lifetime counters. Never raises to the caller.

    Every method degrades to "no stats" on a broken or unreadable file — a
    counter is never worth costing someone their dictation.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_stats_path()
        # Shared per path rather than per instance: DictationHistory builds a
        # fresh DictationStats for every entry it records, so a per-object lock
        # would leave the counters' read-modify-write cycle unguarded against
        # any other writer (jarvis.dictation._locks).
        self._lock = store_lock(self._path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def exists(self) -> bool:
        try:
            return self._path.is_file()
        except OSError:
            return False

    # -- reading ---------------------------------------------------------

    def load(self) -> tuple[dict[str, Any], dict[str, DayStat]]:
        """``(totals, days)``. An unreadable or corrupt file reads as empty."""
        totals: dict[str, Any] = {"dictations": 0, "words": 0, "seconds": 0.0}
        days: dict[str, DayStat] = {}
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return totals, days
        except OSError as exc:
            log.warning("dictation stats unreadable: %s", exc)
            return totals, days
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            log.warning("dictation stats are corrupt, ignoring them: %s", exc)
            return totals, days
        if not isinstance(payload, dict):
            return totals, days

        stored_totals = payload.get("totals")
        if isinstance(stored_totals, dict):
            totals = {
                "dictations": _as_int(stored_totals.get("dictations")),
                "words": _as_int(stored_totals.get("words")),
                "seconds": _as_float(stored_totals.get("seconds")),
            }
        stored_days = payload.get("days")
        if isinstance(stored_days, dict):
            for key, value in stored_days.items():
                if not isinstance(value, dict) or _parse_day(key) is None:
                    continue  # one bad bucket never invalidates the rest
                days[str(key)] = DayStat(
                    date=str(key),
                    dictations=_as_int(value.get("dictations")),
                    words=_as_int(value.get("words")),
                    seconds=_as_float(value.get("seconds")),
                )
        return totals, days

    def summary(self, *, by_day_limit: int = DEFAULT_BY_DAY_LIMIT) -> dict[str, Any]:
        """The lifetime view: totals, today, streak and the recent days."""
        totals, days = self.load()
        return _summary_payload(
            source="lifetime", totals=totals, days=days, by_day_limit=by_day_limit
        )

    # -- writing ---------------------------------------------------------

    def record(
        self,
        *,
        created_at: str | None = None,
        word_count: int = 0,
        duration_s: float = 0.0,
    ) -> bool:
        """Count one dictation. ``False`` when nothing was recorded.

        Only dictations that produced words are counted. A failed or empty one
        still belongs in the history (that is how a Restore finds it), but
        counting it would drag the words-per-minute figure toward zero for a
        reason that has nothing to do with how fast the user speaks.
        """
        words = _as_int(word_count)
        if words <= 0:
            return False
        day = local_day(created_at) or today_key()
        seconds = max(0.0, _as_float(duration_s))
        try:
            with self._lock:
                totals, days = self.load()
                previous = days.get(day, DayStat(date=day))
                days[day] = DayStat(
                    date=day,
                    dictations=previous.dictations + 1,
                    words=previous.words + words,
                    seconds=previous.seconds + seconds,
                )
                totals = {
                    "dictations": _as_int(totals.get("dictations")) + 1,
                    "words": _as_int(totals.get("words")) + words,
                    "seconds": _as_float(totals.get("seconds")) + seconds,
                }
                self._write(totals, days)
        except Exception:  # noqa: BLE001 — a counter never costs a dictation
            log.warning("could not record the dictation statistics", exc_info=True)
            return False
        return True

    def reset(self) -> bool:
        """Zero everything. What "delete my dictation history" also calls.

        The streak resets with it — the UI copy has to say so, because a
        silently reset streak reads as a bug rather than a consequence.
        """
        try:
            with self._lock:
                self._write({"dictations": 0, "words": 0, "seconds": 0.0}, {})
                return True
        except Exception:  # noqa: BLE001
            log.warning("could not reset the dictation statistics", exc_info=True)
            return False

    def _write(self, totals: dict[str, Any], days: dict[str, DayStat]) -> None:
        ordered = sorted(days.values(), key=lambda d: d.date, reverse=True)
        kept = ordered[:MAX_DAYS_RETAINED]
        payload = {
            "version": 1,
            "totals": {
                "dictations": _as_int(totals.get("dictations")),
                "words": _as_int(totals.get("words")),
                "seconds": round(_as_float(totals.get("seconds")), 2),
            },
            "days": {d.date: d.to_dict() for d in kept},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".dictation_stats_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


# ----------------------------------------------------------------------
# Fallback: derive a summary from the history window
# ----------------------------------------------------------------------


def summarize_entries(
    entries: Sequence[Any],
    *,
    by_day_limit: int = DEFAULT_BY_DAY_LIMIT,
) -> dict[str, Any]:
    """Build the same summary shape out of history entries alone.

    This is the honest fallback for an install that predates the sidecar: the
    numbers are real, they are just bounded by the history window. The caller
    surfaces that as ``source: "window"`` so the UI can label the panel
    truthfully instead of presenting a 30-day count as a lifetime total.
    """
    totals = {"dictations": 0, "words": 0, "seconds": 0.0}
    days: dict[str, DayStat] = {}
    for entry in entries or ():
        words = _as_int(getattr(entry, "word_count", 0))
        if words <= 0:
            # Entries recorded before ``word_count`` existed carry a zero they
            # never earned, and skipping them made a full history read as "you
            # have never dictated anything" — the one number this panel exists
            # to show. Counting the stored transcript recovers them; an entry
            # that genuinely holds no text (failed, cancelled, empty) still
            # counts as nothing and is skipped below.
            words = _words_in_text(entry)
        if words <= 0:
            continue
        day = local_day(getattr(entry, "created_at", "")) or today_key()
        seconds = max(0.0, _as_float(getattr(entry, "duration_s", 0.0)))
        previous = days.get(day, DayStat(date=day))
        days[day] = DayStat(
            date=day,
            dictations=previous.dictations + 1,
            words=previous.words + words,
            seconds=previous.seconds + seconds,
        )
        totals["dictations"] = _as_int(totals["dictations"]) + 1
        totals["words"] = _as_int(totals["words"]) + words
        totals["seconds"] = _as_float(totals["seconds"]) + seconds
    return _summary_payload(
        source="window", totals=totals, days=days, by_day_limit=by_day_limit
    )


def _words_in_text(entry: Any) -> int:
    """Word count of an entry's transcript, for rows that never stored one.

    Uses the SAME counter the cleanup and the sidecar use, so a back-filled
    number is comparable with a recorded one rather than merely similar.
    Imported lazily: this module is read by REST handlers, and the cleanup
    module carries the per-language filler tables.
    """
    text = str(getattr(entry, "text", "") or "") or str(
        getattr(entry, "raw_text", "") or ""
    )
    if not text.strip():
        return 0
    try:
        from jarvis.dictation.cleanup import count_words

        return max(0, int(count_words(text)))
    except Exception:  # noqa: BLE001 — a missing counter is "no words", never a 500
        log.debug("word back-fill failed for a history entry", exc_info=True)
        return 0


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "DEFAULT_BY_DAY_LIMIT",
    "MAX_DAYS_RETAINED",
    "DayStat",
    "DictationStats",
    "current_streak",
    "default_stats_path",
    "local_day",
    "longest_streak",
    "summarize_entries",
    "today_key",
]
