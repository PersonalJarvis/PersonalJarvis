"""BriefingComposer: facts only, deterministic, and phrasing never on a key.

Every collaborator is a fake (agenda inputs, calendar tool, executor, brain);
no provider, calendar or model is called.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain import background_policy
from jarvis.brain.background_policy import BackgroundDeferred
from jarvis.core.protocols import ToolResult
from jarvis.ops import briefing as briefing_module
from jarvis.ops.briefing import (
    SECTION_KEYS,
    BriefingComposer,
    CalendarDay,
    ToolCalendarReader,
    phrase_briefing,
    render_text,
)
from jarvis.ops.calendar_day import classify_day
from jarvis.ops.ledger import SourceReport, WorkItem, WorkSnapshot
from jarvis.ops.priority import PriorityMark

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 7, 7, 30, tzinfo=TZ)
TODAY = NOW.date()
NOW_MS = int(NOW.timestamp() * 1000)
HOUR_MS = 3_600_000


def _item(item_id: str, status: str, **kw: Any) -> WorkItem:
    base: dict[str, Any] = {
        "source": "task",
        "id": item_id,
        "title": f"Title {item_id}",
        "status": status,
        "source_state": status,
        "updated_ms": NOW_MS - HOUR_MS,
    }
    base.update(kw)
    return WorkItem(**base)


ITEMS = (
    _item(
        "parked",
        "waiting_capacity",
        source="mission",
        needs_attention=True,
        attention_reason="capacity_decision",
    ),
    _item("run", "running"),
    _item("due", "scheduled", due_ms=NOW_MS + 2 * HOUR_MS),
    _item("late", "scheduled", due_ms=NOW_MS - HOUR_MS),
    _item("tomorrow", "scheduled", due_ms=NOW_MS + 30 * HOUR_MS),
    _item("hot", "queued"),
    _item("focus", "queued"),
    _item("paused", "paused"),
    _item("broke", "failed", updated_ms=NOW_MS - 2 * HOUR_MS),
    _item("old-broke", "failed", updated_ms=NOW_MS - 50 * HOUR_MS),
    _item("done", "done"),
)
MARKS = (
    PriorityMark("task", "hot", "urgent"),
    PriorityMark("task", "focus", None, TODAY.isoformat()),
)
EVENTS = (
    {
        "summary": "Standup",
        "start": "2026-10-07T09:00:00+02:00",
        "end": "2026-10-07T09:15:00+02:00",
    },
    {"summary": "Holiday", "start": "2026-10-07", "end": "2026-10-08"},
    {"summary": "Trip", "start": "2026-10-06", "end": "2026-10-09"},
    {"summary": "Yesterday", "start": "2026-10-06", "end": "2026-10-07"},
    {
        "summary": "Tomorrow",
        "start": "2026-10-08T10:00:00+02:00",
        "end": "2026-10-08T11:00:00+02:00",
    },
    {
        "summary": "Dropped call",
        "start": "2026-10-07T12:00:00+02:00",
        "end": "2026-10-07T13:00:00+02:00",
        "status": "cancelled",
    },
    {"summary": "Late UTC", "start": "2026-10-07T20:00:00Z", "end": "2026-10-07T21:00:00Z"},
)


def _cal_day(events: Any = EVENTS) -> CalendarDay:
    upcoming, cancelled = classify_day(TODAY, events, tz=TZ, now=NOW)
    return CalendarDay("ok", upcoming, cancelled)


class FakeCalendar:
    def __init__(self, day: CalendarDay | Exception) -> None:
        self.day = day
        self.calls = 0

    async def read_day(self, day: date, now: datetime) -> CalendarDay:
        self.calls += 1
        if isinstance(self.day, Exception):
            raise self.day
        return self.day


def _composer(calendar: Any = None, address: str | None = "Dennis") -> BriefingComposer:
    async def _snapshot() -> WorkSnapshot:
        return WorkSnapshot(ITEMS, (SourceReport("task", "ok", len(ITEMS)),))

    async def _marks() -> list[PriorityMark]:
        return list(MARKS)

    return BriefingComposer(
        snapshot=_snapshot,
        marks=_marks,
        calendar=calendar,
        address=(lambda: address),
    )


def _ids(section: Any) -> list[str]:
    return [entry["id"] for entry in section.items]


# --- Calendar day ---------------------------------------------------------------


def test_the_day_keeps_only_todays_events_in_order() -> None:
    events, cancelled = classify_day(TODAY, EVENTS, tz=TZ, now=NOW)
    assert [e["title"] for e in events] == ["Trip", "Holiday", "Standup", "Late UTC"]
    assert [e["title"] for e in cancelled] == ["Dropped call"]
    assert [e["all_day"] for e in events] == [True, True, False, False]
    assert events[2]["time"] == "09:00"
    assert events[3]["time"] == "22:00"  # 20:00 UTC in the person's zone


# --- Sections -------------------------------------------------------------------


async def test_sections_group_the_facts_without_double_listing() -> None:
    briefing = await _composer(FakeCalendar(_cal_day())).compose(now=NOW, language="en")
    assert [s.key for s in briefing.sections] == list(SECTION_KEYS)
    sec = briefing.section
    assert _ids(sec("needs_you")) == ["parked"]
    assert sec("needs_you").items[0]["attention_reason"] == "capacity_decision"
    assert _ids(sec("focus")) == ["focus"]
    assert _ids(sec("running")) == ["run"]
    assert set(_ids(sec("due_today"))) == {"due", "late"}  # overdue counts, tomorrow not
    assert _ids(sec("priorities")) == ["hot"]
    assert _ids(sec("blocked")) == ["paused"]  # the parked mission sits under needs_you
    assert _ids(sec("failed_recently")) == ["broke"]
    assert sec("calendar").status == "ok" and sec("calendar").count == 4
    assert [e["title"] for e in sec("calendar_cancelled").items] == ["Dropped call"]
    calendar_keys = ("calendar", "calendar_cancelled")
    listed = [i for s in briefing.sections if s.key not in calendar_keys for i in _ids(s)]
    assert len(listed) == len(set(listed))
    assert "done" not in listed and "tomorrow" not in listed


async def test_the_briefing_is_deterministic() -> None:
    cal = _cal_day()
    first = await _composer(FakeCalendar(cal)).compose(now=NOW, language="de")
    second = await _composer(FakeCalendar(cal)).compose(now=NOW, language="de")
    assert first.to_dict() == second.to_dict()


async def test_the_text_states_only_the_facts() -> None:
    briefing = await _composer(FakeCalendar(_cal_day())).compose(now=NOW, language="en")
    text = briefing.text
    assert text.startswith("Briefing for Dennis, 2026-10-07")
    assert "Needs you (1):" in text
    assert "Title parked (mission, waiting for subscription capacity" in text
    assert "- [!!] Title hot (task, queued)" in text
    assert "- 09:00 Standup" in text and "- all day Holiday" in text
    upcoming = text.split("Upcoming appointments:")[1].split("Cancelled appointments")[0]
    assert "Dropped call" not in upcoming  # a cancelled event is never upcoming
    assert "Cancelled appointments (1):\n- 12:00 Dropped call" in text
    assert "Title done" not in text


@pytest.mark.parametrize(
    "day,expected",
    [
        (CalendarDay("not_connected"), "Calendar not connected."),
        (CalendarDay("unavailable"), "Calendar could not be read."),
        (CalendarDay("empty"), "No upcoming appointments today."),
    ],
)
async def test_calendar_states_are_said_plainly(day: CalendarDay, expected: str) -> None:
    briefing = await _composer(FakeCalendar(day)).compose(now=NOW, language="en")
    assert expected in briefing.text


async def test_a_broken_or_missing_calendar_never_sinks_the_briefing() -> None:
    broken = await _composer(FakeCalendar(RuntimeError("token store exploded"))).compose(now=NOW)
    assert broken.section("calendar").status == "unavailable"
    assert "exploded" not in broken.text
    missing = await _composer(None).compose(now=NOW)
    assert missing.section("calendar").status == "not_connected"
    skipped_cal = FakeCalendar(CalendarDay("ok"))
    skipped = await _composer(skipped_cal).compose(now=NOW, include_calendar=False)
    assert skipped.section("calendar").status == "skipped" and skipped_cal.calls == 0


async def test_an_empty_day_says_so() -> None:
    async def _empty() -> WorkSnapshot:
        return WorkSnapshot(())

    async def _no_marks() -> list[PriorityMark]:
        return []

    composer = BriefingComposer(snapshot=_empty, marks=_no_marks)
    briefing = await composer.compose(now=NOW, language="en")
    assert "Nothing needs you right now" in briefing.text
    assert briefing.text.startswith("Briefing for 2026-10-07")


async def test_now_must_carry_the_persons_timezone() -> None:
    with pytest.raises(ValueError):
        await _composer().compose(now=datetime(2026, 10, 7, 7, 30))  # noqa: DTZ001


# --- Languages ------------------------------------------------------------------


def test_every_language_has_every_phrase() -> None:
    tables = briefing_module._PHRASES
    keys = set(tables["en"])
    for language in briefing_module.LANGUAGES:
        assert set(tables[language]) == keys, language


@pytest.mark.parametrize("language", ["en", "de", "es", "zh"])
async def test_every_language_renders(language: str) -> None:
    briefing = await _composer(FakeCalendar(CalendarDay("empty"))).compose(
        now=NOW, language=language
    )
    assert briefing.language == language
    table = briefing_module._PHRASES[language]
    assert table["needs_you"] in briefing.text
    assert table["cal_empty"] in briefing.text


async def test_an_unknown_language_falls_back_to_english() -> None:
    briefing = await _composer().compose(now=NOW, language="fr-FR")
    assert briefing.language == "en"


def test_render_never_reads_more_than_it_is_given() -> None:
    text = render_text((), day=TODAY, language="de", address=None)
    assert text.splitlines()[0] == "Briefing für 2026-10-07"  # i18n-allow: asserts German output


# --- Calendar through the ToolExecutor (AP-3) ----------------------------------


class FakeCalendarTool:
    name = "google_calendar"


class FakeExecutor:
    def __init__(self, result: ToolResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        self.calls.append((tool.name, dict(args)))
        return self.result


async def test_the_calendar_is_read_through_the_executor_with_list_events_only() -> None:
    executor = FakeExecutor(ToolResult(True, {"events": list(EVENTS)}))
    reader = ToolCalendarReader(
        tools=lambda: {"google_calendar": FakeCalendarTool()}, executor=lambda: executor
    )
    day = await reader.read_day(TODAY, NOW)
    assert day.status == "ok"
    assert [e["title"] for e in day.events] == ["Trip", "Holiday", "Standup", "Late UTC"]
    assert [e["title"] for e in day.cancelled] == ["Dropped call"]
    assert [(name, args["action"], args["show_deleted"]) for name, args in executor.calls] == [
        ("google_calendar", "list_events", True)
    ]


async def test_a_calendar_error_never_carries_the_provider_text() -> None:
    body = "Calendar API 403: Request had insufficient authentication scopes. user@example.com"
    executor = FakeExecutor(ToolResult(False, None, body))
    reader = ToolCalendarReader(
        tools=lambda: {"google_calendar": FakeCalendarTool()}, executor=lambda: executor
    )
    day = await reader.read_day(TODAY, NOW)
    assert day == CalendarDay("unavailable")
    briefing = await _composer(reader).compose(now=NOW)
    assert "403" not in str(briefing.to_dict()) and "example.com" not in str(briefing.to_dict())


async def test_no_calendar_tool_means_not_connected() -> None:
    executor = FakeExecutor(ToolResult(True, {"events": []}))
    reader = ToolCalendarReader(tools=lambda: {}, executor=lambda: executor)
    assert (await reader.read_day(TODAY, NOW)).status == "not_connected"
    assert executor.calls == []


# --- Phrasing: never a per-token key --------------------------------------------


class FakeBrain:
    def __init__(self, reply: Any = "Good morning. One mission waits for capacity.") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def run_task(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return str(self.reply)


def _config(provider: str = "") -> Any:
    worker = SimpleNamespace(provider=provider, model="m", reasoning_effort="")
    return SimpleNamespace(brain=SimpleNamespace(worker=worker))


async def _no_seat(_provider: str) -> None:
    return None


async def _seat(_provider: str) -> tuple[str, str]:
    return ("claude-api", "claude-cli")


@pytest.fixture
async def base_briefing() -> Any:
    return await _composer(FakeCalendar(CalendarDay("empty"))).compose(now=NOW, language="en")


async def test_without_a_subscription_nothing_is_phrased(base_briefing: Any) -> None:
    brain = FakeBrain()
    result = await phrase_briefing(base_briefing, brain=brain, config=_config())
    assert (result.status, result.reason, result.text) == ("not_allowed", "no_subscription", None)
    assert brain.calls == []


@pytest.mark.parametrize("provider", ["openai", "gemini", "openrouter", "claude-api"])
async def test_a_selected_api_agent_is_never_used(
    base_briefing: Any, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    monkeypatch.setattr("jarvis.core.task_agent.subscription_seat_off_loop", _no_seat)
    background_policy.note_connected("codex")  # even with a subscription connected
    brain = FakeBrain()
    result = await phrase_briefing(base_briefing, brain=brain, config=_config(provider))
    assert (result.status, result.reason) == ("not_allowed", "selected_agent_bills_api")
    assert brain.calls == []


@pytest.mark.parametrize("login", [None, False])
async def test_a_codex_seat_without_a_confirmed_login_is_not_used(
    base_briefing: Any, monkeypatch: pytest.MonkeyPatch, login: bool | None
) -> None:
    """Codex can run on an API key: only a confirmed subscription login counts."""
    monkeypatch.setattr(background_policy, "_probe_override", lambda _p: login)
    brain = FakeBrain()
    result = await phrase_briefing(base_briefing, brain=brain, config=_config("codex"))
    assert result.status == "not_allowed" and brain.calls == []


@pytest.mark.parametrize(
    "provider,setup",
    [
        ("codex", "login"),
        ("claude-api", "seat"),
        ("ollama", "none"),
    ],
)
async def test_a_subscription_seat_or_local_model_phrases(
    base_briefing: Any, monkeypatch: pytest.MonkeyPatch, provider: str, setup: str
) -> None:
    if setup == "login":
        monkeypatch.setattr(background_policy, "_probe_override", lambda p: p == "codex")
    if setup == "seat":
        monkeypatch.setattr("jarvis.core.task_agent.subscription_seat_off_loop", _seat)
    brain = FakeBrain()
    result = await phrase_briefing(base_briefing, brain=brain, config=_config(provider))
    assert result.status == "phrased" and result.text
    call = brain.calls[0]
    assert call["allowed_tools"] == ()
    assert base_briefing.text in call["prompt"]
    assert "DATA" in call["prompt"]


async def test_with_a_connected_subscription_the_unattended_chain_is_used(
    base_briefing: Any,
) -> None:
    background_policy.note_connected("codex")
    brain = FakeBrain()
    result = await phrase_briefing(base_briefing, brain=brain, config=_config())
    assert (result.status, result.reason) == ("phrased", "subscription_connected")


async def test_a_deferred_or_failed_turn_leaves_the_deterministic_text(base_briefing: Any) -> None:
    background_policy.note_connected("codex")
    deferred = await phrase_briefing(
        base_briefing, brain=FakeBrain(BackgroundDeferred("no capacity")), config=_config()
    )
    assert deferred.status == "no_capacity" and deferred.text is None
    failed = await phrase_briefing(
        base_briefing, brain=FakeBrain(RuntimeError("401 bad key sk-x")), config=_config()
    )
    assert failed.to_dict() == {"status": "failed", "reason": "model_error", "text": None}
    assert base_briefing.text  # the deterministic briefing stands


async def test_phrasing_through_the_real_brain_defers_instead_of_billing(
    base_briefing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end with the real BrainManager: a connected but spent
    subscription and only key providers left — the Phase-1 filter defers,
    no provider is ever built."""
    from jarvis.brain.manager import BrainManager
    from jarvis.core.bus import EventBus
    from jarvis.core.config import JarvisConfig

    class _Executor:
        async def execute(self, *a: Any, **kw: Any) -> ToolResult:
            return ToolResult(True, "ok")

    mgr = BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={},
        tool_executor=_Executor(),  # type: ignore[arg-type]
    )
    built: list[str] = []
    monkeypatch.setattr(mgr, "_get_brain", lambda name, _m=None: built.append(name))
    monkeypatch.setattr(
        mgr, "_task_provider_chain", lambda _i: [("openai", None), ("gemini", None)]
    )
    monkeypatch.setattr("jarvis.core.task_agent.subscription_seat_off_loop", _no_seat)
    monkeypatch.setattr(background_policy, "_probe_override", lambda _p: False)
    background_policy.note_connected("codex")

    result = await phrase_briefing(base_briefing, brain=mgr, config=JarvisConfig())
    assert result.status == "no_capacity"
    assert built == []


def test_the_prompt_only_carries_the_briefing() -> None:
    from jarvis.ops.briefing import Briefing, phrasing_prompt

    briefing = Briefing(TODAY, "de", (), "Briefing für 2026-10-07\n")  # i18n-allow: test input
    prompt = phrasing_prompt(briefing)
    assert "German" in prompt and "<briefing>" in prompt
    assert re.search(r"add nothing", prompt)
