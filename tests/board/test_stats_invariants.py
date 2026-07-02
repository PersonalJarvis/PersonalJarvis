"""Invariant tests for the board hero stats (words, streak, active time).

The board previously violated three invariants that these tests pin down:

1. **Monotonic ledger** — the session recorder prunes ``sessions.db`` rows
   older than the retention window at every boot with a millisecond-precise
   cutoff. The aggregator then recomputed partially-pruned days from the
   remaining rows and blindly overwrote the complete ``daily_stats`` row, so
   ACTIVE TIME, conversation counts and word counts silently decayed day by
   day. Once a day is in the ledger, a shrinking *source* must never shrink
   the ledger.
2. **Honest crash seal** — sessions left open by a crash were sealed at the
   next boot with ``ended_ms = boot time``, inflating a day's active time by
   many phantom hours (observed: a 14.8 h "session"). Stale sessions must be
   sealed with their last recorded activity instead.
3. **Streak grace** — the running streak anchored strictly at *today*, so it
   displayed 0 every morning until the first interaction of the day. A quiet
   today must not break yesterday's streak.

Plus determinism guards: the aggregation must derive everything from stored
timestamps — never file mtime — and per-day totals must be timezone-stable.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis.board.aggregator import BoardAggregator, _ensure_daily_stats_columns
from jarvis.board.store import BoardStore
from jarvis.core.bus import EventBus
from jarvis.sessions.constants import HANGUP_SHUTDOWN
from jarvis.sessions.init import bootstrap_sessions, shutdown_sessions
from jarvis.sessions.store import SessionStore

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _now() -> datetime:
    return datetime.now().astimezone()


def _add_session(
    store: SessionStore,
    *,
    sid: str,
    start: datetime,
    duration_s: float | None,
    turns: list[tuple[float, float, str, str]] = (),
    hangup_reason: str = "hotkey",
) -> None:
    """Insert one session with optional turns via the real store API.

    ``turns`` entries are ``(offset_s, turn_duration_s, user_text, jarvis_text)``
    relative to the session start. ``duration_s=None`` leaves the session open.
    """
    store.upsert_session(session_id=sid, started_ms=_ms(start))
    for i, (offset_s, dur_s, user_text, jarvis_text) in enumerate(turns):
        turn_id = f"{sid}-t{i}"
        t0 = _ms(start + timedelta(seconds=offset_s))
        store.upsert_turn(turn_id=turn_id, session_id=sid, idx=i, started_ms=t0)
        store.finalize_turn(
            turn_id=turn_id,
            ended_ms=t0 + int(dur_s * 1000),
            user_text=user_text,
            user_lang="en",
            jarvis_text=jarvis_text,
            jarvis_lang="en",
            tier="fast",
            provider="test",
            model="test",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            latency_total_ms=0,
            tool_calls=[],
        )
    if duration_s is not None:
        store.finalize_session(
            session_id=sid,
            ended_ms=_ms(start + timedelta(seconds=duration_s)),
            hangup_reason=hangup_reason,
            turn_count=len(turns),
            total_cost_usd=0.0,
            total_tokens_in=0,
            total_tokens_out=0,
            providers_used=[],
        )


def _snapshot_daily(agg: BoardAggregator) -> dict[str, dict[str, Any]]:
    rows = agg.db.execute(
        "SELECT date, session_count, user_words_count, jarvis_words_count, "
        "active_events_count, conversation_seconds_estimate FROM daily_stats"
    ).fetchall()
    return {r["date"]: dict(r) for r in rows}


def _totals(agg: BoardAggregator) -> dict[str, float]:
    row = agg.db.execute(
        "SELECT COALESCE(SUM(session_count),0) s, COALESCE(SUM(user_words_count),0) uw, "
        "COALESCE(SUM(jarvis_words_count),0) jw, "
        "COALESCE(SUM(conversation_seconds_estimate),0) secs FROM daily_stats"
    ).fetchone()
    return {"sessions": row["s"], "user_words": row["uw"],
            "jarvis_words": row["jw"], "seconds": row["secs"]}


def _mk_board_db(db_path: Path) -> sqlite3.Connection:
    """Create an empty board DB with the real schema for streak fixtures."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    schema = (Path("jarvis/board/schema.sql")).read_text(encoding="utf-8")
    conn.executescript(schema)
    _ensure_daily_stats_columns(conn)
    return conn


def _insert_active_day(conn: sqlite3.Connection, day: str, events: int = 5) -> None:
    conn.execute(
        "INSERT INTO daily_stats (date, active_events_count) VALUES (?, ?) "
        "ON CONFLICT(date) DO UPDATE SET active_events_count = excluded.active_events_count",
        (day, events),
    )


def _tz(name: str, fallback_hours: int) -> timezone | Any:
    """A real IANA zone when tzdata is available, else a fixed offset."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - no tzdata on this host
        return timezone(timedelta(hours=fallback_hours))


# ----------------------------------------------------------------------
# 1. Monotonic ledger under retention pruning
# ----------------------------------------------------------------------


def test_prune_records_a_horizon(tmp_path: Path) -> None:
    """``prune_older_than`` must record its cutoff so readers can tell
    "deleted" from "never existed". The horizon only ever moves forward."""
    store = SessionStore(db_path=tmp_path / "sessions.db")
    store.open()
    try:
        _add_session(store, sid="old", start=_now() - timedelta(days=40), duration_s=60)
        before = _ms(_now() - timedelta(days=30))
        store.prune_older_than(30)
        after = _ms(_now() - timedelta(days=30))
        horizon = store.prune_horizon_ms()
        assert horizon is not None
        assert before <= horizon <= after

        # A later prune with a LARGER window must not move the horizon back.
        store.prune_older_than(60)
        assert store.prune_horizon_ms() == horizon
    finally:
        store.close()


def test_pruned_source_never_shrinks_daily_ledger(tmp_path: Path) -> None:
    """Core monotonicity: re-aggregating after a retention prune must leave
    every already-recorded day (and therefore all totals) untouched — even for
    the boundary day the ms-precise cutoff slices in half."""
    sessions_db = tmp_path / "sessions.db"
    store = SessionStore(db_path=sessions_db)
    store.open()

    now = _now()
    cutoff = now - timedelta(days=30)
    # Two sessions on the cutoff day, one on each side of the cutoff instant,
    # both clamped into the cutoff day so the day is only PARTIALLY pruned.
    day_start = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    before_cutoff = cutoff - timedelta(hours=1)
    if before_cutoff < day_start:
        before_cutoff = day_start + (cutoff - day_start) / 2
    after_cutoff = cutoff + timedelta(hours=1)
    if after_cutoff >= day_end:
        after_cutoff = cutoff + (day_end - cutoff) / 2

    _add_session(
        store, sid="ancient", start=now - timedelta(days=40), duration_s=600,
        turns=[(1, 5, "three words here", "four words in reply")],
    )
    _add_session(
        store, sid="boundary-pruned", start=before_cutoff, duration_s=600,
        turns=[(1, 5, "alpha beta gamma delta", "echo foxtrot")],
    )
    _add_session(
        store, sid="boundary-kept", start=after_cutoff, duration_s=300,
        turns=[(1, 5, "one two", "three")],
    )
    _add_session(
        store, sid="fresh", start=now - timedelta(days=5), duration_s=120,
        turns=[(1, 5, "hello there", "hi")],
    )

    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",
        db_path=tmp_path / "board" / "personal.db",
        sessions_db_path=sessions_db,
    )
    agg.run()
    daily_before = _snapshot_daily(agg)
    totals_before = _totals(agg)
    assert totals_before["sessions"] == 4
    assert totals_before["seconds"] == 600 + 600 + 300 + 120

    # The recorder's boot-time retention prune removes "ancient" entirely and
    # slices the boundary day in half.
    pruned = store.prune_older_than(30)
    assert pruned == 2
    store.close()

    agg.run()
    assert _snapshot_daily(agg) == daily_before
    assert _totals(agg) == totals_before
    agg.close()


def test_fresh_board_db_still_backfills_frozen_dates(tmp_path: Path) -> None:
    """The freeze must protect existing rows, not forbid first-time inserts:
    rebuilding a deleted board DB must still ingest whatever the sources have,
    including dates below the prune horizon."""
    sessions_db = tmp_path / "sessions.db"
    store = SessionStore(db_path=sessions_db)
    store.open()
    now = _now()
    _add_session(
        store, sid="kept", start=now - timedelta(days=29, hours=1), duration_s=600,
        turns=[(1, 5, "still here", "yes")],
    )
    store.prune_older_than(30)  # horizon exists, nothing was actually deleted
    store.close()

    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",
        db_path=tmp_path / "board" / "personal.db",
        sessions_db_path=sessions_db,
    )
    agg.run()
    totals = _totals(agg)
    assert totals["sessions"] == 1
    assert totals["seconds"] == 600
    agg.close()


# ----------------------------------------------------------------------
# 2. Honest crash seal
# ----------------------------------------------------------------------


def test_crash_seal_uses_last_activity_not_boot_time(tmp_path: Path) -> None:
    """A session left open by a crash must be sealed with its last recorded
    activity, not with "whenever the app happened to restart"."""
    db_path = tmp_path / "sessions.db"
    store = SessionStore(db_path=db_path)
    store.open()
    start = _now() - timedelta(hours=10)
    _add_session(
        store, sid="crashed", start=start, duration_s=None,
        turns=[(5, 85, "are you there", "certainly")],
    )
    last_activity = _ms(start + timedelta(seconds=5 + 85))
    store.close()

    result = bootstrap_sessions(bus=EventBus(), db_path=db_path, retention_days=30)
    try:
        row = result["store"].get_session("crashed")
        assert row is not None
        assert row.hangup_reason == HANGUP_SHUTDOWN
        assert row.ended_ms == last_activity  # NOT ten hours later at boot time
    finally:
        shutdown_sessions(result)


def test_inflated_shutdown_seal_is_repaired_at_boot(tmp_path: Path) -> None:
    """Sessions already sealed with a phantom boot-time end (the observed
    14.8 h ghost) are repaired to their last activity on the next bootstrap.
    Honestly sealed sessions stay untouched."""
    db_path = tmp_path / "sessions.db"
    store = SessionStore(db_path=db_path)
    store.open()
    start = _now() - timedelta(days=2)
    _add_session(
        store, sid="ghost", start=start, duration_s=14 * 3600,
        turns=[(5, 55, "short chat", "indeed")],
        hangup_reason=HANGUP_SHUTDOWN,
    )
    honest_start = _now() - timedelta(days=1)
    _add_session(
        store, sid="honest", start=honest_start, duration_s=90,
        turns=[(5, 55, "quick one", "done")],
        hangup_reason="hotkey",
    )
    store.close()

    result = bootstrap_sessions(bus=EventBus(), db_path=db_path, retention_days=30)
    try:
        ghost = result["store"].get_session("ghost")
        assert ghost is not None
        assert ghost.ended_ms == _ms(start + timedelta(seconds=60))
        honest = result["store"].get_session("honest")
        assert honest is not None
        assert honest.ended_ms == _ms(honest_start + timedelta(seconds=90))
    finally:
        shutdown_sessions(result)


# ----------------------------------------------------------------------
# 3. Streak semantics
# ----------------------------------------------------------------------


def test_streak_survives_a_quiet_today(tmp_path: Path) -> None:
    """Before the first interaction of the day the streak must show the run
    ending yesterday — not reset to 0 every morning."""
    db_path = tmp_path / "board" / "personal.db"
    conn = _mk_board_db(db_path)
    today = _now().date()
    _insert_active_day(conn, (today - timedelta(days=1)).isoformat())
    _insert_active_day(conn, (today - timedelta(days=2)).isoformat())
    conn.close()

    store = BoardStore(db_path=db_path)
    assert store.summary(window_days=30)["streak_days"] == 2


def test_streak_counts_today_and_resets_on_gap(tmp_path: Path) -> None:
    db_path = tmp_path / "board" / "personal.db"
    conn = _mk_board_db(db_path)
    today = _now().date()
    _insert_active_day(conn, today.isoformat())
    _insert_active_day(conn, (today - timedelta(days=1)).isoformat())
    # gap at today-2, older activity must not count into the running streak
    _insert_active_day(conn, (today - timedelta(days=3)).isoformat())
    conn.close()

    store = BoardStore(db_path=db_path)
    assert store.summary(window_days=30)["streak_days"] == 2


def test_streak_zero_when_yesterday_and_today_quiet(tmp_path: Path) -> None:
    db_path = tmp_path / "board" / "personal.db"
    conn = _mk_board_db(db_path)
    today = _now().date()
    _insert_active_day(conn, (today - timedelta(days=2)).isoformat())
    _insert_active_day(conn, (today - timedelta(days=3)).isoformat())
    conn.close()

    store = BoardStore(db_path=db_path)
    assert store.summary(window_days=30)["streak_days"] == 0


def test_longest_streak_spans_month_and_year_boundaries(tmp_path: Path) -> None:
    db_path = tmp_path / "board" / "personal.db"
    conn = _mk_board_db(db_path)
    for day in ("2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02"):
        _insert_active_day(conn, day)
    for day in ("2026-04-29", "2026-04-30", "2026-05-01"):
        _insert_active_day(conn, day)
    conn.close()

    store = BoardStore(db_path=db_path)
    assert store.summary(window_days=30)["longest_streak"] == 4


# ----------------------------------------------------------------------
# 4. Word counts — exact fixture
# ----------------------------------------------------------------------


def test_word_counts_exact_on_fixture(tmp_path: Path) -> None:
    """YOU SAID / NICO SAID are whitespace word counts over the stored turn
    texts — punctuation and repeated whitespace must not change the count."""
    sessions_db = tmp_path / "sessions.db"
    store = SessionStore(db_path=sessions_db)
    store.open()
    start = _now() - timedelta(days=1, hours=2)
    _add_session(
        store, sid="s1", start=start, duration_s=300,
        turns=[
            (1, 5, "Hello   there,  friend!", "Hi."),            # 3 user / 1 jarvis
            (10, 5, "state-of-the-art demo", "works really well"),  # 2 / 3
            (20, 5, "", "counting only real words"),                # 0 / 4
        ],
    )
    store.close()

    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",
        db_path=tmp_path / "board" / "personal.db",
        sessions_db_path=sessions_db,
    )
    agg.run()
    agg.close()

    summary = BoardStore(db_path=tmp_path / "board" / "personal.db").summary()
    assert summary["totals"]["user_words"] == 5
    assert summary["totals"]["jarvis_words"] == 8
    assert summary["totals"]["session_count"] == 1


# ----------------------------------------------------------------------
# 5. Determinism: timezones and file metadata
# ----------------------------------------------------------------------


def test_totals_are_timezone_invariant(tmp_path: Path) -> None:
    """Day *bucketing* legitimately follows the configured timezone, but the
    all-time totals must be identical whether the host thinks it is in UTC,
    Berlin or Auckland — and repeated runs must be byte-stable."""
    sessions_db = tmp_path / "sessions.db"
    store = SessionStore(db_path=sessions_db)
    store.open()
    # 23:30 UTC — lands on different local dates in different zones.
    utc_evening = datetime.now(tz=UTC).replace(
        hour=23, minute=30, second=0, microsecond=0
    ) - timedelta(days=5)
    _add_session(
        store, sid="s1", start=utc_evening, duration_s=600,
        turns=[(1, 5, "spread across zones", "indeed it is")],
    )
    _add_session(
        store, sid="s2", start=utc_evening - timedelta(days=1, hours=12),
        duration_s=300, turns=[(1, 5, "second day", "yes")],
    )
    store.close()

    zones = {
        "utc": UTC,
        "berlin": _tz("Europe/Berlin", 2),
        "auckland": _tz("Pacific/Auckland", 12),
    }
    all_totals: dict[str, dict[str, float]] = {}
    for name, tz in zones.items():
        snapshots = []
        for run in range(2):
            agg = BoardAggregator(
                jsonl_dir=tmp_path / "flight_recorder",
                db_path=tmp_path / f"board-{name}-{run}" / "personal.db",
                sessions_db_path=sessions_db,
                tz=tz,
            )
            agg.run()
            snapshots.append((_snapshot_daily(agg), _totals(agg)))
            agg.close()
        assert snapshots[0] == snapshots[1], f"non-deterministic in {name}"
        all_totals[name] = snapshots[0][1]

    assert all_totals["utc"] == all_totals["berlin"] == all_totals["auckland"]


def test_results_do_not_depend_on_file_mtime(tmp_path: Path) -> None:
    """Everything derives from timestamps stored IN the data. Touching the
    files (backup tools, copies, git operations do this) must not move any
    number."""
    sessions_db = tmp_path / "sessions.db"
    store = SessionStore(db_path=sessions_db)
    store.open()
    _add_session(
        store, sid="s1", start=_now() - timedelta(days=3), duration_s=240,
        turns=[(1, 5, "mtime must not matter", "correct")],
    )
    store.close()

    jsonl_dir = tmp_path / "flight_recorder"
    jsonl_dir.mkdir()
    ts_ns = int((_now() - timedelta(days=2)).timestamp() * 1e9)
    (jsonl_dir / "events.jsonl").write_text(
        json.dumps({
            "ts_ns": ts_ns, "event": "TranscriptFinal",
            "trace_id": "t1", "payload": {},
        }) + "\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "board" / "personal.db"
    agg = BoardAggregator(
        jsonl_dir=jsonl_dir, db_path=db_path, sessions_db_path=sessions_db
    )
    agg.run()
    before = _snapshot_daily(agg)

    ancient = 946_684_800  # 2000-01-01, far away from every stored timestamp
    os.utime(sessions_db, (ancient, ancient))
    os.utime(jsonl_dir / "events.jsonl", (ancient, ancient))

    assert agg.run_if_stale(ttl_s=0) is True
    assert _snapshot_daily(agg) == before
    agg.close()
