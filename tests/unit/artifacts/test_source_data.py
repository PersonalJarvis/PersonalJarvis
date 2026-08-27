"""The facts an artifact about the user's own data is built from.

Pinned: which requests name the inbox / the calendar (and which do not), that
the reads go through the executor with bounded arguments, that every failure
mode — plugin missing, read failed, nothing there, timeout — becomes an
honest section rather than an exception, and that the rendered brief section
tells the worker what it may and may not show. Fakes, not mocks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from jarvis.artifacts import source_data as sd
from jarvis.core.protocols import ToolResult

# -- planning -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        (
            "Ein Morning Briefing aus dem Google Kalender "  # i18n-allow
            "und wichtigen E-Mails aus Gmail",  # i18n-allow
            ("calendar", "inbox"),
        ),
        ("what's in my inbox today, as a page", ("inbox",)),
        ("meine Termine der Woche als Timeline", ("calendar",)),  # i18n-allow
        ("un resumen de mis correos y reuniones", ("calendar", "inbox")),  # i18n-allow
        ("Umsatz pro Monat 2026 als Balken", ()),  # i18n-allow
        ("a landing page for my SaaS", ()),
    ],
)
def test_plan_names_the_sources_the_request_names(text: str, keys: tuple[str, ...]) -> None:
    assert tuple(n.key for n in sd.plan_source_data(text)) == keys


def test_a_sample_request_is_recognised() -> None:
    assert sd.wants_sample_data("build a demo dashboard with sample data") is True
    assert sd.wants_sample_data("eine Vorlage für ein Briefing")  # i18n-allow
    assert sd.wants_sample_data("my real inbox, please") is False


# -- fetching -------------------------------------------------------------------


class _FakeExecutor:
    """Records every call and answers from a per-tool script."""

    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        self.calls.append((tool.name, dict(args)))
        answer = self.script.get(tool.name)
        if callable(answer):
            return await answer(args)
        return answer


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


NOW = datetime(2026, 8, 27, 9, 30, tzinfo=timezone(timedelta(hours=2)))


async def _gmail(args: dict[str, Any]) -> ToolResult:
    if args["action"] == "list_messages":
        return ToolResult(success=True, output={"messages": [{"id": "m1"}, {"id": "m2"}]})
    if args["message_id"] == "m2":
        return ToolResult(success=False, output=None, error="404")
    return ToolResult(
        success=True,
        output={
            "from": "Real Sender <real@example.com>",
            "subject": "Invoice 4711",
            "date": "Thu, 27 Aug 2026 08:42:00 +0200",
            "labelIds": ["INBOX", "UNREAD"],
            "snippet": "Please find attached",
            "body": "x" * 1000,
        },
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_inbox_is_read_through_the_executor_and_slimmed() -> None:
    executor = _FakeExecutor({"gmail": _gmail})
    data = _run(
        sd.fetch_source_data(
            (sd.INBOX,),
            tools={"gmail": _Tool("gmail")},
            executor=executor,
            trace_id=uuid4(),
            utterance="my mail",
            now=NOW,
        )
    )
    (section,) = data.sections
    assert section.status == "ok"
    assert section.note == "1 messages from the last 2 days (1 more could not be read)"
    (item,) = section.items
    assert item["from"] == "Real Sender <real@example.com>"
    assert item["subject"] == "Invoice 4711"
    assert item["unread"] is True
    assert len(item["body"]) == sd.INBOX_BODY_CHARS + 1 and item["body"].endswith("…")
    # list, then one get per id — through the executor, never Tool.execute.
    assert [c[1]["action"] for c in executor.calls] == [
        "list_messages",
        "get_message",
        "get_message",
    ]
    assert executor.calls[0][1]["query"] == "newer_than:2d"
    assert executor.calls[0][1]["max_results"] == sd.INBOX_MAX_MESSAGES


def test_calendar_window_is_today_and_tomorrow_from_local_midnight() -> None:
    events = [{"summary": "Standup", "start": "2026-08-27T09:00:00+02:00"}]
    executor = _FakeExecutor(
        {"google_calendar": ToolResult(success=True, output={"events": events})}
    )
    data = _run(
        sd.fetch_source_data(
            (sd.CALENDAR,),
            tools={"google_calendar": _Tool("google_calendar")},
            executor=executor,
            trace_id=uuid4(),
            utterance="my day",
            now=NOW,
        )
    )
    (section,) = data.sections
    assert section.status == "ok" and section.items == (events[0],)
    args = executor.calls[0][1]
    assert args["action"] == "list_events"
    assert args["time_min"] == "2026-08-27T00:00:00+02:00"
    assert args["time_max"] == "2026-08-29T00:00:00+02:00"


def test_every_dead_end_is_an_honest_section_never_an_exception() -> None:
    async def boom(_: dict[str, Any]) -> ToolResult:
        raise RuntimeError("token store on fire")

    executor = _FakeExecutor(
        {
            "gmail": ToolResult(success=False, output=None, error="Gmail plugin not connected"),
            "google_calendar": boom,
        }
    )
    data = _run(
        sd.fetch_source_data(
            (sd.CALENDAR, sd.INBOX),
            tools={"gmail": _Tool("gmail"), "google_calendar": _Tool("google_calendar")},
            executor=executor,
            trace_id=uuid4(),
            utterance="briefing",
            now=NOW,
        )
    )
    calendar, inbox = data.sections
    assert calendar.status == "unavailable" and "RuntimeError" in calendar.note
    assert inbox.status == "unavailable" and "not connected" in inbox.note


def test_a_missing_tool_and_an_empty_read_are_named_as_such() -> None:
    executor = _FakeExecutor(
        {"google_calendar": ToolResult(success=True, output={"events": []})}
    )
    data = _run(
        sd.fetch_source_data(
            (sd.CALENDAR, sd.INBOX),
            tools={"google_calendar": _Tool("google_calendar")},
            executor=executor,
            trace_id=uuid4(),
            utterance="briefing",
            now=NOW,
        )
    )
    calendar, inbox = data.sections
    assert calendar.status == "empty"
    assert "no events between 2026-08-27 to 2026-08-28" in calendar.note
    assert inbox.status == "unavailable" and "not connected" in inbox.note
    # Only the connected plugin was asked.
    assert [c[0] for c in executor.calls] == ["google_calendar"]


def test_a_hanging_plugin_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sd, "PER_SOURCE_TIMEOUT_S", 0.01)

    async def never(_: dict[str, Any]) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult(success=True, output={})

    data = _run(
        sd.fetch_source_data(
            (sd.INBOX,),
            tools={"gmail": _Tool("gmail")},
            executor=_FakeExecutor({"gmail": never}),
            trace_id=uuid4(),
            utterance="mail",
            now=NOW,
        )
    )
    (section,) = data.sections
    assert section.status == "unavailable" and "did not answer" in section.note


# -- rendering ------------------------------------------------------------------


def test_render_tells_the_worker_what_it_may_show() -> None:
    data = sd.SourceData(
        sections=(
            sd.SourceSection(
                sd.CALENDAR, "ok", items=({"summary": "Standup"},), note="1 events"
            ),
            sd.SourceSection(
                sd.INBOX, "unavailable", note="the Gmail inbox plugin is not connected"
            ),
        ),
        fetched_at=NOW,
    )
    text = sd.render_source_data(data)
    assert text.startswith("## Source data — the only facts about the user's own data")
    assert "2026-08-27T09:30+02:00" in text
    assert "### Google Calendar — available" in text
    assert '"summary": "Standup"' in text
    assert "### Gmail inbox — UNAVAILABLE" in text
    assert "the Gmail inbox plugin is not connected" in text
    assert "never a made-up item in its place" in text
    assert sd.render_source_data(sd.SourceData()) == ""


def test_unavailable_source_data_marks_every_need() -> None:
    data = sd.unavailable_source_data((sd.CALENDAR, sd.INBOX))
    assert [s.status for s in data.sections] == ["unavailable", "unavailable"]
    assert "no access" in data.sections[0].note
