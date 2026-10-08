"""OwnerNotifier: opt-in, once, short notice first, safe retries, no secrets.

Only the simulated Telegram transport exists; these tests also prove it opens
no connection. Every briefing input is a fake.
"""

from __future__ import annotations

import ast
import logging
import random
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from jarvis.ops import notify as notify_module
from jarvis.ops.briefing import BriefingComposer
from jarvis.ops.calendar_day import CalendarDay, classify_day
from jarvis.ops.ledger import WorkItem, WorkSnapshot
from jarvis.ops.notify import (
    MAX_TOTAL_ATTEMPTS,
    TELEGRAM_MAX_CHARS,
    Notification,
    NotifyStore,
    OwnerNotifier,
    SimulatedTelegramTransport,
    TransportError,
    notifications_from_briefing,
    split_text,
)
from jarvis.ops.priority import PriorityMark

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 7, 7, 30, tzinfo=TZ)
TODAY = NOW.date()
FAKE_SECRET = "123456:FAKE-BOT-TOKEN-not-real"  # noqa: S105 - a fake, never a real token
RETRY = TransportError("rate_limited", retryable=True)
FATAL = TransportError("chat_not_found", retryable=False)


@pytest.fixture
def store(tmp_path: Path) -> NotifyStore:
    return NotifyStore(tmp_path / "ops.sqlite")


@pytest.fixture
async def enabled(store: NotifyStore) -> NotifyStore:
    await store.save_settings(enabled=True, kinds=notify_module.NOTIFICATION_KINDS)
    return store


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _notifier(store: NotifyStore, transport: Any, **kw: Any) -> OwnerNotifier:
    kw.setdefault("sleep", Sleeps())
    kw.setdefault("rng", random.Random(1))  # noqa: S311 - deterministic jitter in tests
    return OwnerNotifier(store, transport, **kw)


def _note(
    key: str, priority: str = "normal", kind: str = "important_task", text: str = ""
) -> Notification:
    return Notification(kind, key, text or f"text {key}", priority)  # type: ignore[arg-type]


# --- Opt-in ---------------------------------------------------------------------


async def test_off_by_default_nothing_is_sent_or_recorded(store: NotifyStore) -> None:
    assert (await store.settings()).enabled is False
    transport = SimulatedTelegramTransport()
    report = await _notifier(store, transport).deliver([_note("a"), _note("b", "high")])
    assert [o.status for o in report.outcomes] == ["disabled", "disabled"]
    assert transport.sent == []
    assert await store.outbox() == []


async def test_opt_in_then_opt_out(store: NotifyStore) -> None:
    await store.save_settings(enabled=True, kinds=["daily_briefing"])
    settings = await store.settings()
    assert settings.enabled and settings.kinds == frozenset({"daily_briefing"})
    await store.save_settings(enabled=False, kinds=["daily_briefing"])
    transport = SimulatedTelegramTransport()
    await _notifier(store, transport).deliver([_note("x", kind="daily_briefing")])
    assert transport.sent == []


async def test_unknown_kinds_are_refused(store: NotifyStore) -> None:
    with pytest.raises(ValueError):
        await store.save_settings(enabled=True, kinds=["sms"])
    assert (await store.settings()).enabled is False


async def test_kinds_the_person_turned_off_are_skipped(store: NotifyStore) -> None:
    await store.save_settings(enabled=True, kinds=["appointment_cancelled"])
    transport = SimulatedTelegramTransport()
    report = await _notifier(store, transport).deliver(
        [_note("c", kind="appointment_cancelled"), _note("t", kind="important_task")]
    )
    assert [o.status for o in report.outcomes] == ["simulated", "kind_off"]
    assert transport.sent == ["text c"]


# --- Once -----------------------------------------------------------------------


async def test_a_notification_is_sent_once(enabled: NotifyStore) -> None:
    transport = SimulatedTelegramTransport()
    notifier = _notifier(enabled, transport)
    first = await notifier.deliver([_note("a"), _note("a")])
    second = await notifier.deliver([_note("a")])
    assert [o.status for o in first.outcomes] == ["simulated", "duplicate"]
    assert [o.status for o in second.outcomes] == ["duplicate"]
    assert transport.sent == ["text a"]
    [row] = await enabled.outbox()
    assert (row.status, row.transport, row.attempts) == ("simulated", "telegram-simulated", 1)


# --- Priority and the per-run cap -------------------------------------------------


async def test_short_notice_goes_first_and_the_cap_holds_the_rest(enabled: NotifyStore) -> None:
    transport = SimulatedTelegramTransport()
    notifier = _notifier(enabled, transport, max_per_run=2)
    notes = [_note("n1"), _note("n2"), _note("urgent-change", "high"), _note("n3")]
    report = await notifier.deliver(notes)
    assert [o.status for o in report.outcomes] == [
        "simulated",
        "rate_limited",
        "simulated",
        "rate_limited",
    ]
    assert transport.sent == ["text urgent-change", "text n1"]
    later = await notifier.deliver(notes)
    assert [o.status for o in later.outcomes] == [
        "duplicate",
        "simulated",
        "duplicate",
        "simulated",
    ]


# --- Retries ----------------------------------------------------------------------


async def test_a_retryable_error_is_retried_with_jittered_backoff(enabled: NotifyStore) -> None:
    sleeps = Sleeps()
    transport = SimulatedTelegramTransport(fail=[RETRY, RETRY])
    report = await _notifier(enabled, transport, sleep=sleeps).deliver([_note("a")])
    assert report.outcomes[0].status == "simulated"
    assert transport.sent == ["text a"]
    assert len(sleeps.calls) == 2
    assert 0.5 <= sleeps.calls[0] <= 1.5 and 2.0 <= sleeps.calls[1] <= 6.0
    assert (await enabled.get("a")).attempts == 3  # type: ignore[union-attr]


async def test_a_permanent_error_is_not_retried(enabled: NotifyStore) -> None:
    sleeps = Sleeps()
    transport = SimulatedTelegramTransport(fail=[FATAL])
    notifier = _notifier(enabled, transport, sleep=sleeps)
    report = await notifier.deliver([_note("a")])
    assert report.outcomes[0].to_dict()["status"] == "gave_up"
    assert report.outcomes[0].error == "chat_not_found"
    assert sleeps.calls == [] and transport.sent == []
    again = await notifier.deliver([_note("a")])
    assert again.outcomes[0].status == "gave_up"  # never hammered again


async def test_failures_are_retried_on_later_runs_up_to_a_limit(enabled: NotifyStore) -> None:
    transport = SimulatedTelegramTransport(fail=[RETRY] * 20)
    notifier = _notifier(enabled, transport)
    first = await notifier.deliver([_note("a")])
    assert first.outcomes[0].status == "failed"
    second = await notifier.deliver([_note("a")])
    row = await enabled.get("a")
    assert second.outcomes[0].status == "gave_up"
    assert row is not None and row.attempts == MAX_TOTAL_ATTEMPTS and row.status == "gave_up"
    assert transport.sent == []


async def test_a_split_message_resumes_at_the_first_unsent_part(enabled: NotifyStore) -> None:
    text = "\n".join(["A" * 3000, "B" * 3000])
    transport = SimulatedTelegramTransport(fail=[None, RETRY, RETRY, RETRY])
    notifier = _notifier(enabled, transport)
    first = await notifier.deliver([_note("long", text=text)])
    assert first.outcomes[0].status == "failed"
    row = await enabled.get("long")
    assert row is not None and (row.parts_sent, row.parts_total) == (1, 2)
    second = await notifier.deliver([_note("long", text=text)])
    assert second.outcomes[0].status == "simulated"
    assert transport.sent == ["A" * 3000, "B" * 3000]  # part one exactly once


async def test_an_unexpected_transport_crash_is_a_failure_not_a_crash(enabled: NotifyStore) -> None:
    class Broken(SimulatedTelegramTransport):
        async def send(self, text: str) -> None:
            raise RuntimeError(f"socket closed {FAKE_SECRET}")

    report = await _notifier(enabled, Broken()).deliver([_note("a")])
    assert (report.outcomes[0].status, report.outcomes[0].error) == ("failed", "RuntimeError")
    row = await enabled.get("a")
    assert row is not None and FAKE_SECRET not in row.error


# --- No secrets in logs -----------------------------------------------------------


async def test_logs_never_carry_the_text_or_a_token(
    enabled: NotifyStore, caplog: pytest.LogCaptureFixture
) -> None:
    secret_text = f"Board meeting with token {FAKE_SECRET} chat 987654321"
    transport = SimulatedTelegramTransport(fail=[RETRY])
    with caplog.at_level(logging.DEBUG, logger="jarvis.ops"):
        await _notifier(enabled, transport).deliver([_note("k", text=secret_text)])
    logged = caplog.text
    assert caplog.records  # it did log
    assert FAKE_SECRET not in logged and "987654321" not in logged and "Board" not in logged
    assert "k" not in [r.getMessage() for r in caplog.records]
    assert notify_module.key_hash("k") in logged


# --- Simulated only: no network ---------------------------------------------------


def test_the_notifier_module_imports_no_network_or_telegram_code() -> None:
    tree = ast.parse(Path(notify_module.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    banned = ("httpx", "aiohttp", "requests", "urllib", "socket", "telegram", "jarvis.channels")
    assert not [m for m in imported if m.startswith(banned)]
    assert "api.telegram.org" not in Path(notify_module.__file__).read_text(encoding="utf-8")


async def test_simulated_delivery_opens_no_socket(
    enabled: NotifyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_network(*_a: Any, **_k: Any) -> None:
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    transport = SimulatedTelegramTransport()
    report = await _notifier(enabled, transport).deliver([_note("a")])
    assert report.outcomes[0].status == "simulated"


def test_split_text_respects_the_telegram_limit() -> None:
    assert split_text("") == ()
    assert split_text("short") == ("short",)
    parts = split_text("\n".join(["x" * 4000, "y" * 200, "z" * 5000]))
    assert all(0 < len(p) <= TELEGRAM_MAX_CHARS for p in parts)
    assert "".join(parts).replace("\n", "") == "x" * 4000 + "y" * 200 + "z" * 5000


# --- From the briefing ------------------------------------------------------------

EVENTS = (
    {
        "id": "standup",
        "summary": "Standup",
        "start": "2026-10-07T10:00:00+02:00",
        "end": "2026-10-07T10:15:00+02:00",
        "original_start": "2026-10-07T09:00:00+02:00",
        "updated": "2026-10-07T05:00:00Z",
    },
    {
        "id": "review",
        "summary": "Review",
        "start": "2026-10-07T16:00:00+02:00",
        "original_start": "2026-10-06T16:00:00+02:00",
        "updated": "2026-09-30T10:00:00Z",
    },
    {
        "id": "call",
        "summary": "Client call",
        "start": "2026-10-07T12:00:00+02:00",
        "status": "cancelled",
        "updated": "2026-10-07T05:00:00Z",
    },
    {"id": "lunch", "summary": "Lunch", "start": "2026-10-07T12:30:00+02:00"},
)


class _Cal:
    async def read_day(self, day: Any, now: Any) -> CalendarDay:
        upcoming, cancelled = classify_day(TODAY, EVENTS, tz=TZ, now=NOW)
        return CalendarDay("ok", upcoming, cancelled)


async def _briefing(language: str = "en") -> Any:
    items = (
        WorkItem(
            "mission",
            "m1",
            "Write the report",
            "waiting_capacity",
            "WAITING_CAPACITY",
            needs_attention=True,
            attention_reason="capacity_decision",
        ),
        WorkItem("task", "t1", "Pay invoice", "queued", "pending"),
        WorkItem("task", "t2", "Water plants", "queued", "pending"),
    )

    async def _snapshot() -> WorkSnapshot:
        return WorkSnapshot(items)

    async def _marks() -> list[PriorityMark]:
        return [PriorityMark("task", "t1", "urgent")]

    return await BriefingComposer(snapshot=_snapshot, marks=_marks, calendar=_Cal()).compose(
        now=NOW, language=language
    )


async def test_notifications_follow_the_briefing_facts() -> None:
    notes = notifications_from_briefing(await _briefing())
    summary = [(n.kind, n.dedup_key, n.priority) for n in notes]
    assert summary == [
        ("appointment_cancelled", "cancelled:call:2026-10-07T12:00:00+02:00", "high"),
        ("appointment_moved", "moved:standup:2026-10-07T10:00:00+02:00", "high"),
        ("appointment_moved", "moved:review:2026-10-07T16:00:00+02:00", "normal"),
        ("important_task", "task:mission:m1:waiting_capacity", "high"),
        ("important_task", "task:task:t1:queued", "normal"),
        ("daily_briefing", "briefing:2026-10-07", "normal"),
    ]
    texts = {n.dedup_key: n.text for n in notes}
    assert texts["cancelled:call:2026-10-07T12:00:00+02:00"] == (
        "Appointment cancelled: (!) 12:00 Client call (short-notice change)"
    )
    assert "moved from 09:00" in texts["moved:standup:2026-10-07T10:00:00+02:00"]
    assert "moved from 2026-10-06 16:00" in texts["moved:review:2026-10-07T16:00:00+02:00"]
    assert texts["task:task:t1:queued"].startswith("Urgent: [!!] Pay invoice")
    assert not any("Lunch" in n.text for n in notes if n.kind != "daily_briefing")
    assert not any("Water plants" in n.text for n in notes if n.kind != "daily_briefing")


async def test_notifications_are_localised_and_stable() -> None:
    first = notifications_from_briefing(await _briefing("de"))
    second = notifications_from_briefing(await _briefing("de"))
    assert first == second
    assert first[0].text.startswith("Termin abgesagt:")  # i18n-allow: asserts German output


def test_every_language_has_every_notification_text() -> None:
    keys = set(notify_module._TEXTS["en"])
    for language in ("en", "de", "es", "zh"):
        assert set(notify_module._TEXTS[language]) == keys


async def test_end_to_end_short_notice_first_and_once(enabled: NotifyStore) -> None:
    notes = notifications_from_briefing(await _briefing())
    transport = SimulatedTelegramTransport()
    notifier = _notifier(enabled, transport, max_per_run=3)
    await notifier.deliver(notes)
    assert transport.sent[0].startswith("Appointment cancelled: (!)")
    assert transport.sent[1].startswith("Appointment moved: (!)")
    assert transport.sent[2].startswith("Needs you:")
    await notifier.deliver(notes)
    await notifier.deliver(notes)
    assert len(transport.sent) == len(notes)  # everything once, nothing twice
