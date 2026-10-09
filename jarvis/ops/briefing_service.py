"""One briefing service for voice, chat and Telegram.

Answers "what is on today?", "my morning briefing", "which appointments do I
have tomorrow?" and "has anything changed in my calendar today?" from the
same facts the morning briefing uses (``briefing.py``, ``calendar_day.py``):
deterministic, no model call, and every appointment change (moved, new,
cancelled) is only reported when the calendar API states it.

Each answer carries both renderings — ``spoken`` (plain sentences for the
voice agent to say) and ``text`` (the written briefing for chat/Telegram) —
and the caller's channel picks which one it uses. Channel detection lives in
the route (``jarvis/ui/web/ops_routes.py``), from the calling turn's origin
headers (``jarvis/core/turn_origin.py``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal

from jarvis.ops.briefing import (
    Briefing,
    BriefingComposer,
    BriefingSection,
    event_line,
    normalize_language,
    phrases,
    source_label,
)
from jarvis.ops.calendar_day import CalendarDay

Focus = Literal["briefing", "appointments", "changes"]
DayName = Literal["today", "tomorrow"]
FOCUSES: Final[tuple[str, ...]] = ("briefing", "appointments", "changes")
DAYS: Final[tuple[str, ...]] = ("today", "tomorrow")
#: Appointments read out one by one; the rest is counted.
SPOKEN_MAX_EVENTS: Final = 6

_SPOKEN: Final[dict[str, dict[str, str]]] = {
    "en": {
        "today": "Today",
        "tomorrow": "Tomorrow",
        "none": "{when} there are no appointments in your calendar.",
        "count_one": "{when} you have one appointment: {list}.",
        "count_many": "{when} you have {n} appointments: {list}.",
        "at": "at {time} {title}",
        "all_day": "{title}, all day",
        "and_more": "and {n} more",
        "moved": "Moved: {title}, now {time}, before {old}.",
        "cancelled": "Cancelled: {title} {time}.",
        "new": "New in your calendar: {title} {time}.",
        "short_notice": " That was a short-notice change.",
        "no_changes": "Nothing has changed in your calendar for today or tomorrow.",
        "not_connected": "Your calendar is not connected.",
        "unavailable": "I could not read your calendar right now.",
        "needs_you_one": "One thing needs you: {list}.",
        "needs_you_many": "{n} things need you: {list}.",
        "focus_one": "Your focus today: {list}.",
        "nothing_open": "Nothing needs you right now.",
        "hidden": "I left out {n} unassigned entries for today's profile.",
        "as_of": ("I cannot read your calendar right now; this is the briefing from {time}."),
        "sources_down": "I could not reach {list} right now.",
    },
    "de": {  # i18n-allow: runtime spoken briefing (paired with en/es/zh)
        "today": "Heute",  # i18n-allow
        "tomorrow": "Morgen",  # i18n-allow
        "none": "{when} stehen keine Termine in deinem Kalender.",  # i18n-allow
        "count_one": "{when} hast du einen Termin: {list}.",  # i18n-allow
        "count_many": "{when} hast du {n} Termine: {list}.",  # i18n-allow
        "at": "um {time} {title}",  # i18n-allow
        "all_day": "{title}, ganztägig",  # i18n-allow
        "and_more": "und {n} weitere",  # i18n-allow
        "moved": "Verschoben: {title}, jetzt {time}, vorher {old}.",  # i18n-allow
        "cancelled": "Abgesagt: {title} {time}.",  # i18n-allow
        "new": "Neu im Kalender: {title} {time}.",  # i18n-allow
        "short_notice": " Das wurde kurzfristig geändert.",  # i18n-allow
        "no_changes": (
            "An deinem Kalender hat sich für heute und morgen nichts geändert."  # i18n-allow
        ),
        "not_connected": "Dein Kalender ist nicht verbunden.",  # i18n-allow
        "unavailable": "Ich konnte deinen Kalender gerade nicht lesen.",  # i18n-allow
        "needs_you_one": "Eine Sache braucht dich: {list}.",  # i18n-allow
        "needs_you_many": "{n} Sachen brauchen dich: {list}.",  # i18n-allow
        "focus_one": "Dein Fokus heute: {list}.",  # i18n-allow
        "nothing_open": "Gerade braucht dich nichts.",  # i18n-allow
        "hidden": (
            "{n} nicht zugeordnete Einträge habe ich für heute ausgelassen."  # i18n-allow
        ),
        "as_of": (
            "Ich kann deinen Kalender gerade nicht lesen; "  # i18n-allow
            "das ist das Briefing von {time}."  # i18n-allow
        ),
        "sources_down": "Gerade nicht erreichbar: {list}.",  # i18n-allow
    },
    "es": {  # i18n-allow: runtime spoken briefing (paired with en/de/zh)
        "today": "Hoy",  # i18n-allow
        "tomorrow": "Mañana",  # i18n-allow
        "none": "{when} no hay citas en tu calendario.",  # i18n-allow
        "count_one": "{when} tienes una cita: {list}.",  # i18n-allow
        "count_many": "{when} tienes {n} citas: {list}.",  # i18n-allow
        "at": "a las {time} {title}",  # i18n-allow
        "all_day": "{title}, todo el día",  # i18n-allow
        "and_more": "y {n} más",  # i18n-allow
        "moved": "Movida: {title}, ahora {time}, antes {old}.",  # i18n-allow
        "cancelled": "Cancelada: {title} {time}.",  # i18n-allow
        "new": "Nueva en tu calendario: {title} {time}.",  # i18n-allow
        "short_notice": " Fue un cambio de último momento.",  # i18n-allow
        "no_changes": "No ha cambiado nada en tu calendario para hoy ni mañana.",  # i18n-allow
        "not_connected": "Tu calendario no está conectado.",  # i18n-allow
        "unavailable": "Ahora no pude leer tu calendario.",  # i18n-allow
        "needs_you_one": "Una cosa te necesita: {list}.",  # i18n-allow
        "needs_you_many": "{n} cosas te necesitan: {list}.",  # i18n-allow
        "focus_one": "Tu enfoque de hoy: {list}.",  # i18n-allow
        "nothing_open": "Ahora nada te necesita.",  # i18n-allow
        "hidden": "Omití {n} entradas sin asignar para el perfil de hoy.",  # i18n-allow
        "as_of": (
            "Ahora no puedo leer tu calendario; este es el resumen de las {time}."  # i18n-allow
        ),
        "sources_down": "Ahora no pude consultar: {list}.",  # i18n-allow
    },
    "zh": {  # i18n-allow: runtime spoken briefing (paired with en/de/es)
        "today": "今天",  # i18n-allow
        "tomorrow": "明天",  # i18n-allow
        "none": "{when}日历里没有安排。",  # i18n-allow
        "count_one": "{when}你有一个安排：{list}。",  # i18n-allow
        "count_many": "{when}你有{n}个安排：{list}。",  # i18n-allow
        "at": "{time} {title}",  # i18n-allow
        "all_day": "{title}，全天",  # i18n-allow
        "and_more": "还有{n}个",  # i18n-allow
        "moved": "已改期：{title}，现在{time}，原来{old}。",  # i18n-allow
        "cancelled": "已取消：{title} {time}。",  # i18n-allow
        "new": "新增日程：{title} {time}。",  # i18n-allow
        "short_notice": "这是临时变更。",  # i18n-allow
        "no_changes": "今天和明天的日历都没有变化。",  # i18n-allow
        "not_connected": "你的日历尚未连接。",  # i18n-allow
        "unavailable": "我现在无法读取你的日历。",  # i18n-allow
        "needs_you_one": "有一件事需要你处理：{list}。",  # i18n-allow
        "needs_you_many": "有{n}件事需要你处理：{list}。",  # i18n-allow
        "focus_one": "你今天的重点：{list}。",  # i18n-allow
        "nothing_open": "目前没有需要你处理的事。",  # i18n-allow
        "hidden": "按今天的设置，我省略了{n}个未分类的条目。",  # i18n-allow
        "as_of": "我现在无法读取你的日历；这是{time}的简报。",  # i18n-allow
        "sources_down": "我现在无法访问：{list}。",  # i18n-allow
    },
}


@dataclass(frozen=True, slots=True)
class BriefingAnswer:
    day: date
    focus: str
    language: str
    spoken: str
    text: str
    sections: tuple[BriefingSection, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "focus": self.focus,
            "language": self.language,
            "spoken": self.spoken,
            "text": self.text,
            "sections": [s.to_dict() for s in self.sections],
        }


def spoken_table(language: str) -> Mapping[str, str]:
    return _SPOKEN.get(language, _SPOKEN["en"])


def _when(event: Mapping[str, Any], table: Mapping[str, str]) -> str:
    return "" if event.get("all_day") else str(event.get("time") or "")


def _event_phrase(event: Mapping[str, Any], table: Mapping[str, str]) -> str:
    title = str(event.get("title") or "")
    if event.get("all_day"):
        return table["all_day"].format(title=title)
    return table["at"].format(time=event.get("time") or "", title=title)


def _list(items: Sequence[str], table: Mapping[str, str]) -> str:
    shown = list(items[:SPOKEN_MAX_EVENTS])
    rest = len(items) - len(shown)
    if rest > 0:
        shown.append(table["and_more"].format(n=rest))
    return ", ".join(shown)


def _status_sentence(calendar: CalendarDay, table: Mapping[str, str]) -> str | None:
    if calendar.status == "not_connected":
        return table["not_connected"]
    if calendar.status == "unavailable":
        return table["unavailable"]
    return None


def spoken_appointments(
    calendar: CalendarDay, *, which: str, table: Mapping[str, str]
) -> list[str]:
    """Sentences for one day's appointments: upcoming, then moved, cancelled."""
    status = _status_sentence(calendar, table)
    if status is not None:
        return [status]
    when = table[which]
    unchanged = [e for e in calendar.events if not e.get("moved_from") and not e.get("new")]
    sentences: list[str] = []
    if unchanged:
        key = "count_one" if len(unchanged) == 1 else "count_many"
        sentences.append(
            table[key].format(
                when=when,
                n=len(unchanged),
                list=_list([_event_phrase(e, table) for e in unchanged], table),
            )
        )
    elif not calendar.events and not calendar.cancelled:
        sentences.append(table["none"].format(when=when))
    sentences += spoken_changes(calendar, table=table, include_new=True)
    return sentences


def spoken_changes(
    calendar: CalendarDay, *, table: Mapping[str, str], include_new: bool
) -> list[str]:
    sentences: list[str] = []
    for event in calendar.events:
        moved = event.get("moved_from")
        if isinstance(moved, Mapping):
            sentence = table["moved"].format(
                title=event.get("title"),
                time=_when(event, table),
                old=moved.get("time") or moved.get("day") or "",
            )
            sentences.append(
                sentence + (table["short_notice"] if event.get("short_notice") else "")
            )
        elif include_new and event.get("new"):
            sentences.append(
                table["new"]
                .format(title=event.get("title"), time=_when(event, table))
                .replace("  ", " ")
            )
    for event in calendar.cancelled:
        sentence = table["cancelled"].format(title=event.get("title"), time=_when(event, table))
        sentences.append(
            sentence.replace(" .", ".")
            + (table["short_notice"] if event.get("short_notice") else "")
        )
    return sentences


def _calendar_from(briefing: Briefing) -> CalendarDay:
    upcoming = briefing.section("calendar")
    new = briefing.section("calendar_new")
    moved = briefing.section("calendar_moved")
    cancelled = briefing.section("calendar_cancelled")
    return CalendarDay(upcoming.status, upcoming.items + new.items + moved.items, cancelled.items)


def spoken_briefing(briefing: Briefing, table: Mapping[str, str]) -> str:
    sentences: list[str] = []
    needs = briefing.section("needs_you")
    if needs.count:
        key = "needs_you_one" if needs.count == 1 else "needs_you_many"
        sentences.append(
            table[key].format(
                n=needs.count, list=_list([str(i["title"]) for i in needs.items], table)
            )
        )
    focus = briefing.section("focus")
    if focus.count:
        sentences.append(
            table["focus_one"].format(list=_list([str(i["title"]) for i in focus.items], table))
        )
    if not needs.count and not focus.count:
        sentences.append(table["nothing_open"])
    sentences += spoken_appointments(_calendar_from(briefing), which="today", table=table)
    hidden = briefing.section("hidden_unassigned").count
    if hidden:
        sentences.append(table["hidden"].format(n=hidden))
    down = _down_sentence(briefing.unavailable, briefing.language, table)
    if down:
        sentences.append(down)
    return " ".join(s.strip() for s in sentences if s.strip())


def _down_sentence(names: Sequence[str], language: str, table: Mapping[str, str]) -> str | None:
    """ "I could not reach …" for sources that failed; None when all answered."""
    if not names:
        return None
    labels = [source_label(n, phrases(language)) for n in names]
    return table["sources_down"].format(list=_list(labels, table))


def _written_appointments(calendar: CalendarDay, day: date, language: str) -> str:
    table = phrases(language)
    moved = [e for e in calendar.events if e.get("moved_from")]
    new = [e for e in calendar.events if e.get("new") and not e.get("moved_from")]
    unchanged = [e for e in calendar.events if e not in moved and e not in new]
    lines = [f"{table['calendar']} ({day.isoformat()}):"]
    status = {"not_connected": "cal_not_connected", "unavailable": "cal_unavailable"}.get(
        calendar.status
    )
    if status:
        return "\n".join([*lines, table[status]]) + "\n"
    lines += [event_line(e, table, day=day) for e in unchanged] or [table["cal_empty"]]
    if new:
        lines += ["", f"{table['calendar_new']} ({len(new)}):"]
        lines += [event_line(e, table, day=day) for e in new]
    if moved:
        lines += ["", f"{table['calendar_moved']} ({len(moved)}):"]
        lines += [event_line(e, table, day=day) for e in moved]
    if calendar.cancelled:
        lines += ["", f"{table['calendar_cancelled']} ({len(calendar.cancelled)}):"]
        lines += [event_line(e, table, day=day) for e in calendar.cancelled]
    return "\n".join(lines).strip() + "\n"


def _only_changes(calendar: CalendarDay) -> CalendarDay:
    return CalendarDay(
        calendar.status,
        tuple(e for e in calendar.events if e.get("moved_from") or e.get("new")),
        calendar.cancelled,
    )


async def answer(
    *,
    composer: BriefingComposer,
    now: datetime,
    day: str = "today",
    focus: str = "briefing",
    language: str = "en",
) -> BriefingAnswer:
    """The answer to one briefing question, spoken and written.

    Every day is read through the composer, so the day profile (which
    categories that weekday shows) applies to "today", "tomorrow" and
    "changes" alike — also for a spontaneous voice question.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if day not in DAYS or focus not in FOCUSES:
        raise ValueError("unknown day or focus")
    language = normalize_language(language)
    table = spoken_table(language)
    today = now.date()
    target = today + timedelta(days=1 if day == "tomorrow" else 0)

    if focus == "briefing" and day == "today":
        briefing = await composer.compose(now=now, language=language)
        return BriefingAnswer(
            today,
            focus,
            language,
            spoken_briefing(briefing, table),
            briefing.text,
            briefing.sections,
        )

    if focus == "changes":
        days = (today, today + timedelta(days=1))
        reads = [await composer.calendar_for(d, now) for d in days]
        changed = [_only_changes(r) for r, _hidden, _down in reads]
        hidden_total = sum(h for _r, h, _down in reads)
        down = tuple(dict.fromkeys(n for _r, _h, names in reads for n in names))
        sentences: list[str] = []
        texts: list[str] = []
        for d, c in zip(days, changed, strict=True):
            status = _status_sentence(c, table)
            if status:
                sentences = [status]
                break
            sentences += spoken_changes(c, table=table, include_new=True)
            if c.events or c.cancelled:
                texts.append(_written_appointments(c, d, language))
        spoken = " ".join(sentences) or table["no_changes"]
        if hidden_total:
            spoken = f"{spoken} {table['hidden'].format(n=hidden_total)}"
        text = "\n".join(texts) or table["no_changes"] + "\n"
        down_sentence = _down_sentence(down, language, table)
        if down_sentence:
            spoken = f"{spoken} {down_sentence}"
            text = f"{text}\n{down_sentence}\n"
        return BriefingAnswer(target, focus, language, spoken, text)

    # Appointments of one day (also "briefing" for tomorrow: the calendar).
    cal, hidden, down = await composer.calendar_for(target, now)
    sentences = spoken_appointments(cal, which=day, table=table)
    if hidden:
        sentences.append(table["hidden"].format(n=hidden))
    text = _written_appointments(cal, target, language)
    down_sentence = _down_sentence(down, language, table)
    if down_sentence:
        sentences.append(down_sentence)
        text = f"{text}\n{down_sentence}\n"
    spoken = " ".join(sentences)
    return BriefingAnswer(target, focus, language, spoken, text)


__all__ = [
    "DAYS",
    "FOCUSES",
    "SPOKEN_MAX_EVENTS",
    "BriefingAnswer",
    "answer",
    "spoken_appointments",
    "spoken_briefing",
    "spoken_changes",
    "spoken_table",
]
