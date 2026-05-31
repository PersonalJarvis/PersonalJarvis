"""Smoke-Tests fuer ``BoardAggregator`` — Phase A.

Entsprechen 1:1 den drei im Plan-Abschnitt §5-A geforderten Tests plus
einen ``test_no_network``, der dem Done-Criterion ``pytest -k test_no_network``
entspricht (Plan §5-A „Done-Criteria: Keine externe Netzwerk-Call").
"""
from __future__ import annotations

import json
import socket
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jarvis.board.aggregator import BoardAggregator


# ----------------------------------------------------------------------
# Helpers / Fixtures
# ----------------------------------------------------------------------

def _ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1e9)


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e))
            fh.write("\n")


@pytest.fixture
def synthetic_jsonl(tmp_path: Path) -> Path:
    """Ein paar Tage Events quer durch Haupt-Event-Typen."""
    jsonl_dir = tmp_path / "flight_recorder"
    day1 = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    day0 = day1 - timedelta(days=1)

    events = [
        # Tag 0 — 2 erfolgreiche Tools, 1 failed task, 2 voice commands
        {
            "ts_ns": _ns(day0),
            "trace_id": "a" * 32,
            "event": "TranscriptFinal",
            "layer": "speech.stt",
            "payload": {"transcript": {"text": "<redacted>"}},
        },
        {
            "ts_ns": _ns(day0 + timedelta(seconds=15)),
            "trace_id": "a" * 32,
            "event": "ActionExecuted",
            "layer": "orchestrator",
            "payload": {"tool_name": "bash", "success": True, "duration_ms": 120},
        },
        {
            "ts_ns": _ns(day0 + timedelta(minutes=1)),
            "trace_id": "b" * 32,
            "event": "TranscriptFinal",
            "layer": "speech.stt",
            "payload": {"transcript": {"text": "<redacted>"}},
        },
        {
            "ts_ns": _ns(day0 + timedelta(minutes=1, seconds=5)),
            "trace_id": "b" * 32,
            "event": "ActionExecuted",
            "layer": "orchestrator",
            "payload": {"tool_name": "search_web", "success": True, "duration_ms": 250},
        },
        {
            "ts_ns": _ns(day0 + timedelta(minutes=2)),
            "trace_id": "c" * 32,
            "event": "TaskFailed",
            "layer": "tasks",
            "payload": {"task_id": "t1", "error": "timeout"},
        },
        # Tag 1 — 1 successful sub-jarvis, 1 erfolgreiche Task
        {
            "ts_ns": _ns(day1),
            "trace_id": "d" * 32,
            "event": "TaskCompleted",
            "layer": "tasks",
            "payload": {"task_id": "t2", "duration_ms": 9000},
        },
        {
            "ts_ns": _ns(day1 + timedelta(minutes=5)),
            "trace_id": "e" * 32,
            "event": "OpenClawTaskCompleted",
            "layer": "agents",
            "payload": {"success": True, "duration_s": 1800.0, "summary": "<redacted>"},
        },
    ]
    _write_jsonl(jsonl_dir / "day.jsonl", events)
    return jsonl_dir


@pytest.fixture
def synthetic_jsonl_with_retries(tmp_path: Path) -> Path:
    """Voice-Commands mit offensichtlichen Retries (delta < 8 s)."""
    jsonl_dir = tmp_path / "flight_recorder"
    base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)

    events: list[dict] = []
    # 5 commands, davon 2 innerhalb <8s nach dem vorherigen → 2 retries.
    times_s = [0, 3, 15, 18, 60]
    for i, dt_s in enumerate(times_s):
        events.append({
            "ts_ns": _ns(base + timedelta(seconds=dt_s)),
            "trace_id": f"{i:032x}",
            "event": "TranscriptFinal",
            "layer": "speech.stt",
            "payload": {"transcript": {"text": "<redacted>"}},
        })
    _write_jsonl(jsonl_dir / "day.jsonl", events)
    return jsonl_dir


@pytest.fixture
def real_jsonl_with_voice_text(tmp_path: Path) -> Path:
    """Events mit bewusst eingebettetem geheimen Text — darf nach Aggregation
    im Federation-Export NICHT mehr auffindbar sein.
    """
    jsonl_dir = tmp_path / "flight_recorder"
    base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)

    events = [
        {
            "ts_ns": _ns(base),
            "trace_id": "a" * 32,
            "event": "TranscriptFinal",
            "layer": "speech.stt",
            "payload": {"transcript": {"text": "Mein passwort ist hunter2"}},
        },
        {
            "ts_ns": _ns(base + timedelta(seconds=5)),
            "trace_id": "a" * 32,
            "event": "MessageSent",
            "layer": "ui.web.ws",
            "payload": {
                "thread_id": "t1",
                "role": "user",
                "text": "credit-card: 4111-1111-1111-1111",
            },
        },
        {
            "ts_ns": _ns(base + timedelta(seconds=10)),
            "trace_id": "a" * 32,
            "event": "OpenClawTaskCompleted",
            "layer": "agents",
            "payload": {
                "success": True,
                "duration_s": 42.0,
                "summary": "Wrote notes about Marc's personal stuff",
            },
        },
        {
            "ts_ns": _ns(base + timedelta(seconds=12)),
            "trace_id": "a" * 32,
            "event": "ActionExecuted",
            "layer": "orchestrator",
            "payload": {"tool_name": "bash", "success": True, "duration_ms": 5},
        },
    ]
    _write_jsonl(jsonl_dir / "day.jsonl", events)
    return jsonl_dir


# ----------------------------------------------------------------------
# Smoke Tests (aus Plan §5-A)
# ----------------------------------------------------------------------

def test_aggregator_groups_events_by_day(synthetic_jsonl: Path) -> None:
    agg = BoardAggregator(jsonl_dir=synthetic_jsonl, db_path=synthetic_jsonl.parent / "board" / "personal.db")
    agg.run()
    rows = list(agg.db.execute("SELECT date, tasks_completed FROM daily_stats"))
    assert len(rows) >= 2, "Aggregator muss mindestens zwei Tage erzeugen"
    assert any(r["tasks_completed"] >= 1 for r in rows)


def test_voice_first_try_rate_excludes_retries(synthetic_jsonl_with_retries: Path) -> None:
    agg = BoardAggregator(
        jsonl_dir=synthetic_jsonl_with_retries,
        db_path=synthetic_jsonl_with_retries.parent / "board" / "personal.db",
    )
    agg.run()
    row = agg.db.execute(
        "SELECT voice_commands_count, voice_first_try_rate "
        "FROM daily_stats ORDER BY date DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["voice_commands_count"] == 5
    assert row["voice_first_try_rate"] is not None
    assert 0 <= row["voice_first_try_rate"] <= 1
    # 2 Retries von 5 Kommandos → 3/5 = 0.6
    assert abs(row["voice_first_try_rate"] - 0.6) < 1e-6


def test_no_pii_in_aggregated_stats(real_jsonl_with_voice_text: Path) -> None:
    agg = BoardAggregator(
        jsonl_dir=real_jsonl_with_voice_text,
        db_path=real_jsonl_with_voice_text.parent / "board" / "personal.db",
    )
    agg.run()
    serialized = json.dumps(agg.export_all_for_federation())
    forbidden_phrases = [
        "passwort", "hunter2",
        "credit-card", "4111",
        "Marc", "personal stuff",
    ]
    for phrase in forbidden_phrases:
        assert phrase.lower() not in serialized.lower(), f"PII leak: {phrase!r}"


# ----------------------------------------------------------------------
# Done-Criterion aus Plan §5-A: Keine externen Netzwerk-Calls
# ----------------------------------------------------------------------

def test_no_network(monkeypatch: pytest.MonkeyPatch, synthetic_jsonl: Path) -> None:
    """Der Aggregator darf NICHT ins Netz. Jeder Socket-Versuch fliegt raus."""

    orig_socket = socket.socket

    def _blocked_socket(*args, **kwargs):  # pragma: no cover
        raise AssertionError(
            "BoardAggregator hat einen Socket geoeffnet — verboten "
            "(Plan §5-A Done-Criteria)"
        )

    # socket (httpx/requests/urllib bauen darauf auf)
    monkeypatch.setattr(socket, "socket", _blocked_socket)

    # Falls httpx / requests schon importiert sind, deren .get/.post bewusst
    # auch sperren. Das ist defensiv — wenn sie nicht geladen sind, skippen wir.
    for mod_name in ("httpx", "requests", "urllib.request", "urllib3"):
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for attr in ("get", "post", "put", "delete", "request", "urlopen"):
            if hasattr(mod, attr):
                monkeypatch.setattr(
                    mod, attr,
                    lambda *a, **k: (_ for _ in ()).throw(
                        AssertionError(f"{mod_name}.{attr} aufgerufen — verboten")
                    ),
                    raising=False,
                )

    agg = BoardAggregator(
        jsonl_dir=synthetic_jsonl,
        db_path=synthetic_jsonl.parent / "board" / "personal.db",
    )
    agg.run()
    # Wenn wir hier sind, hat der Aggregator keinen Socket angefasst.
    assert agg.db.execute("SELECT COUNT(*) FROM daily_stats").fetchone()[0] >= 1

    # socket wieder freigeben fuer andere Tests in derselben Session
    monkeypatch.setattr(socket, "socket", orig_socket)


# ----------------------------------------------------------------------
# Zusatz-Smoke — Personal Records werden gesetzt
# ----------------------------------------------------------------------

def test_personal_records_populated(synthetic_jsonl: Path) -> None:
    agg = BoardAggregator(
        jsonl_dir=synthetic_jsonl,
        db_path=synthetic_jsonl.parent / "board" / "personal.db",
    )
    agg.run()
    metrics = [
        row["metric"] for row in
        agg.db.execute("SELECT metric FROM personal_records").fetchall()
    ]
    assert "most_tasks_in_a_day" in metrics
    assert "most_unique_tools_in_a_day" in metrics
    assert "most_voice_commands_in_a_day" in metrics
    assert "most_active_events_in_a_day" in metrics


def test_activity_includes_chat_and_conversation_time(tmp_path: Path) -> None:
    jsonl_dir = tmp_path / "flight_recorder"
    base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    events = [
        {
            "ts_ns": _ns(base),
            "trace_id": "a" * 32,
            "event": "MessageSent",
            "layer": "chat",
            "payload": {"thread_id": "t1", "role": "user", "text": "<redacted>"},
        },
        {
            "ts_ns": _ns(base + timedelta(minutes=12)),
            "trace_id": "a" * 32,
            "event": "ResponseGenerated",
            "layer": "brain",
            "payload": {"text": "<redacted>"},
        },
    ]
    _write_jsonl(jsonl_dir / "day.jsonl", events)

    agg = BoardAggregator(
        jsonl_dir=jsonl_dir,
        db_path=tmp_path / "board" / "personal.db",
    )
    agg.run()
    row = agg.db.execute(
        "SELECT tasks_completed, active_events_count, conversation_seconds_estimate "
        "FROM daily_stats LIMIT 1"
    ).fetchone()

    assert row["tasks_completed"] == 0
    assert row["active_events_count"] == 2
    assert row["conversation_seconds_estimate"] == pytest.approx(12 * 60)


# ----------------------------------------------------------------------
# Zusatz — Aggregator haengt nicht an Broken-Lines
# ----------------------------------------------------------------------

def test_aggregator_skips_broken_lines(tmp_path: Path) -> None:
    jsonl_dir = tmp_path / "flight_recorder"
    jsonl_dir.mkdir(parents=True)
    (jsonl_dir / "mix.jsonl").write_text(
        "\n".join([
            "not-json-at-all",
            json.dumps({
                "ts_ns": int(time.time_ns()),
                "trace_id": "a" * 32,
                "event": "TaskCompleted",
                "payload": {"task_id": "ok", "duration_ms": 100},
            }),
            "{incomplete",
            "",
        ]),
        encoding="utf-8",
    )
    agg = BoardAggregator(
        jsonl_dir=jsonl_dir,
        db_path=tmp_path / "board" / "personal.db",
    )
    agg.run()  # darf nicht werfen
    total = agg.db.execute(
        "SELECT SUM(tasks_completed) AS s FROM daily_stats"
    ).fetchone()["s"]
    assert total == 1
