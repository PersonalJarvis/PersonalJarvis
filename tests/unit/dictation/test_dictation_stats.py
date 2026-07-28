"""The lifetime dictation counters.

Two things here are easy to get subtly wrong and impossible to notice once
shipped, so they get the most tests: days are bucketed by the user's LOCAL
calendar date (UTC bucketing moves an evening dictation to tomorrow for
everyone east of UTC), and the streak gives today grace (so the badge reads
the real run over breakfast instead of a demoralising and wrong 0).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from jarvis.dictation.stats import (
    DictationStats,
    current_streak,
    local_day,
    longest_streak,
    summarize_entries,
    today_key,
)


@pytest.fixture()
def stats(tmp_path: Path) -> DictationStats:
    return DictationStats(tmp_path / "dictation_stats.json")


def _day(offset: int) -> str:
    return (datetime.now().astimezone().date() + timedelta(days=offset)).isoformat()


# --------------------------------------------------------------------------
# Local-day bucketing
# --------------------------------------------------------------------------


def test_a_naive_timestamp_is_read_as_utc() -> None:
    assert local_day("2026-07-28T12:00:00") is not None


def test_bucketing_uses_the_local_calendar_day_not_the_utc_one() -> None:
    """23:30 in UTC+2 is still *today* locally, and yesterday in UTC."""
    moment = datetime(2026, 7, 28, 23, 30, tzinfo=timezone(timedelta(hours=2)))
    assert moment.astimezone(UTC).date().isoformat() == "2026-07-28"
    # Whatever the runner's zone is, the answer must be that instant's LOCAL
    # date — never a hardcoded UTC date.
    assert local_day(moment.isoformat()) == moment.astimezone().date().isoformat()


def test_an_unparseable_timestamp_buckets_to_nothing() -> None:
    assert local_day("not-a-date") is None
    assert local_day("") is None
    assert local_day(None) is None


# --------------------------------------------------------------------------
# Streaks
# --------------------------------------------------------------------------


def test_streak_counts_back_from_today() -> None:
    assert current_streak([_day(0), _day(-1), _day(-2)]) == 3


def test_a_quiet_today_gets_grace_instead_of_reading_zero() -> None:
    """Before the first dictation of the day the run ending yesterday shows."""
    assert current_streak([_day(-1), _day(-2)]) == 2


def test_the_streak_only_reaches_zero_once_yesterday_was_quiet_too() -> None:
    assert current_streak([_day(-2), _day(-3)]) == 0


def test_a_gap_ends_the_current_streak() -> None:
    assert current_streak([_day(0), _day(-2), _day(-3)]) == 1


def test_no_days_is_a_zero_streak() -> None:
    assert current_streak([]) == 0


def test_longest_streak_scans_the_whole_history() -> None:
    days = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-02-01", "2026-02-02"]
    assert longest_streak(days) == 3


def test_longest_streak_ignores_unparseable_days() -> None:
    assert longest_streak(["nope", "2026-01-01", "2026-01-02"]) == 2


# --------------------------------------------------------------------------
# The sidecar
# --------------------------------------------------------------------------


def test_a_missing_file_reads_as_empty(stats: DictationStats) -> None:
    summary = stats.summary()
    assert summary["totals"] == {
        "dictations": 0,
        "words": 0,
        "seconds": 0.0,
        "wpm": 0.0,
    }
    assert summary["by_day"] == []
    assert stats.exists is False


def test_record_accumulates_totals_and_days(stats: DictationStats) -> None:
    now = datetime.now(UTC).isoformat()
    assert stats.record(created_at=now, word_count=10, duration_s=30.0) is True
    assert stats.record(created_at=now, word_count=20, duration_s=30.0) is True
    summary = stats.summary()
    assert summary["source"] == "lifetime"
    assert summary["totals"]["dictations"] == 2
    assert summary["totals"]["words"] == 30
    assert summary["totals"]["seconds"] == 60.0
    assert summary["totals"]["wpm"] == 30.0
    assert summary["today"] == {"dictations": 2, "words": 30}
    assert summary["by_day"][0]["date"] == today_key()


def test_a_wordless_dictation_is_not_counted(stats: DictationStats) -> None:
    """A failure still belongs in the history — it does not belong in the WPM."""
    assert stats.record(created_at=None, word_count=0, duration_s=45.0) is False
    assert stats.summary()["totals"]["dictations"] == 0


def test_words_per_minute_is_zero_without_a_duration(stats: DictationStats) -> None:
    stats.record(created_at=None, word_count=10, duration_s=0.0)
    assert stats.summary()["totals"]["wpm"] == 0.0


def test_counters_survive_a_reload(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    DictationStats(path).record(created_at=None, word_count=7, duration_s=10.0)
    assert DictationStats(path).summary()["totals"]["words"] == 7


def test_reset_zeroes_everything_including_the_streak(stats: DictationStats) -> None:
    stats.record(created_at=None, word_count=7, duration_s=10.0)
    assert stats.reset() is True
    summary = stats.summary()
    assert summary["totals"]["words"] == 0
    assert summary["streak"]["current_days"] == 0
    assert summary["by_day"] == []


def test_a_corrupt_file_reads_as_empty_instead_of_raising(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert DictationStats(path).summary()["totals"]["words"] == 0


def test_one_bad_day_bucket_does_not_invalidate_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "totals": {"dictations": 1, "words": 5, "seconds": 10.0},
                "days": {
                    "not-a-date": {"dictations": 1, "words": 5},
                    "2026-01-01": {"dictations": 1, "words": 5, "seconds": 10.0},
                },
            }
        ),
        encoding="utf-8",
    )
    summary = DictationStats(path).summary()
    assert [d["date"] for d in summary["by_day"]] == ["2026-01-01"]


def test_by_day_is_newest_first_and_capped(stats: DictationStats) -> None:
    base = datetime.now(UTC)
    for offset in range(5):
        stats.record(
            created_at=(base - timedelta(days=offset)).isoformat(),
            word_count=1,
            duration_s=1.0,
        )
    dates = [d["date"] for d in stats.summary(by_day_limit=3)["by_day"]]
    assert len(dates) == 3
    assert dates == sorted(dates, reverse=True)


def test_the_streak_is_built_from_the_recorded_days(stats: DictationStats) -> None:
    base = datetime.now(UTC)
    for offset in range(3):
        stats.record(
            created_at=(base - timedelta(days=offset)).isoformat(),
            word_count=4,
            duration_s=2.0,
        )
    assert stats.summary()["streak"]["current_days"] == 3
    assert stats.summary()["streak"]["longest_days"] == 3


# --------------------------------------------------------------------------
# The history-window fallback
# --------------------------------------------------------------------------


def test_summarize_entries_labels_itself_as_a_window() -> None:
    """An install without the sidecar must not present 30 days as a lifetime."""
    from jarvis.dictation.history import DictationEntry

    now = datetime.now(UTC).isoformat()
    entries = [
        DictationEntry(
            id=str(i), created_at=now, raw_text="a b", text="a b",
            word_count=2, duration_s=6.0,
        )
        for i in range(3)
    ]
    summary = summarize_entries(entries)
    assert summary["source"] == "window"
    assert summary["totals"]["dictations"] == 3
    assert summary["totals"]["words"] == 6
    assert summary["today"]["words"] == 6


def test_summarize_entries_skips_wordless_rows() -> None:
    from jarvis.dictation.history import DictationEntry

    entry = DictationEntry(
        id="a", created_at=datetime.now(UTC).isoformat(), raw_text="", text="",
        outcome="failed", word_count=0, duration_s=9.0,
    )
    assert summarize_entries([entry])["totals"]["dictations"] == 0


def test_summarize_entries_tolerates_an_empty_sequence() -> None:
    assert summarize_entries([])["totals"]["words"] == 0


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------


def test_two_stats_at_one_path_share_one_lock(tmp_path: Path) -> None:
    """The counters are constructed per entry recorded, never held onto."""
    nested = tmp_path / "sub"
    nested.mkdir()
    direct = DictationStats(tmp_path / "dictation_stats.json")
    roundabout = DictationStats(nested / ".." / "dictation_stats.json")
    assert direct._lock is roundabout._lock


def test_concurrent_recording_loses_no_count(tmp_path: Path) -> None:
    """Read-modify-write on one file from two threads must not drop a count."""
    import threading

    path = tmp_path / "dictation_stats.json"
    workers, rounds = 2, 40
    start = threading.Barrier(workers, timeout=15)
    errors: list[Exception] = []

    def record() -> None:
        try:
            start.wait()
            for _ in range(rounds):
                assert DictationStats(path).record(word_count=3, duration_s=1.0)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=record) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()
    assert not errors, errors

    totals = DictationStats(path).summary()["totals"]
    assert totals["dictations"] == workers * rounds
    assert totals["words"] == workers * rounds * 3


def test_entries_written_before_word_count_existed_still_count() -> None:
    """A history from before the stats feature must not read as "never dictated".

    Field report 2026-07-28: every row already on disk carried ``word_count=0``
    because the field did not exist when it was written, and the derived
    summary skipped anything with no words. The panel therefore showed 0 words,
    0 dictations and a 0-day streak while the list right below it displayed
    seven real dictations — the single most visible number in the section,
    wrong for every existing install.
    """
    from jarvis.dictation.stats import summarize_entries

    class _Row:
        def __init__(self, text: str, word_count: int, created_at: str) -> None:
            self.text = text
            self.raw_text = text
            self.word_count = word_count
            self.created_at = created_at
            self.duration_s = 2.0

    now = datetime.now(UTC).isoformat()
    summary = summarize_entries(
        [
            _Row("hello there friend", 0, now),  # pre-upgrade row
            _Row("counted already", 2, now),  # post-upgrade row
            _Row("", 0, now),  # genuinely empty: still nothing
        ]
    )

    assert summary["totals"]["dictations"] == 2
    assert summary["totals"]["words"] == 5
    assert summary["today"]["words"] == 5
