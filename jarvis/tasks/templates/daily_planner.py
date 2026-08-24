"""Daily Planner — a realistic plan for today: the meetings, the free focus
blocks between them, and three quick wins."""
from __future__ import annotations

from jarvis.tasks.schema import PluginGrant
from jarvis.tasks.templates import (
    AutomationTemplate,
    LocalizedText,
    TemplateInput,
    TemplateSchedule,
)

_PROMPT = """\
You are compiling the user's Daily Planner for today. The user's work hours \
are {work_hours} (local time).

Procedure:
1. Call `google_calendar` with action `list_events`, `time_min` = today at \
00:00 and `time_max` = today at 23:59 as RFC3339 datetimes in the local time \
zone (e.g. 2026-08-24T00:00:00+02:00), `max_results` 50. Use the current \
date from your context; never guess a different day.
2. From the returned events keep only those on today's date. Sort them by \
start time. All-day events count as context, not as meetings.
3. Compute the free blocks: the gaps between the timed events inside the \
work hours {work_hours}. Merge gaps shorter than 30 minutes into the \
neighbouring meeting; keep at most the 3 longest gaps.
4. Call `gmail` with action `list_messages`, `query` "is:unread \
newer_than:2d", `max_results` 15. Read only subjects and senders. Note \
anything that clearly has to be handled today (a deadline, a direct \
question, a confirmation someone is waiting for). Do not open or reply to \
messages.

Output rules:
- Write in the configured output language. At most 12 lines in total. No \
preamble, no closing remarks, no emojis, no markdown headings or tables.
- Line block 1 — meetings: one line per timed meeting, "HH:MM–HH:MM Title". \
If there are no timed events today, write exactly one line saying the \
calendar is empty today.
- Line block 2 — focus blocks: 1 to 3 lines, "HH:MM–HH:MM Focus: <one \
concrete suggestion>". Base the suggestion on the mail findings or the \
meetings (prepare for X, answer Y, finish Z); when neither gives a hint, \
suggest deep work on the day's most important project. If the calendar is \
empty, the whole work window is one focus block.
- Line block 3 — quick wins: exactly 3 lines, each starting with "Quick win:" \
followed by one small task of 15 minutes or less, taken from the unread \
mail when possible (name the sender or subject), otherwise a sensible \
generic one (confirm tomorrow's meetings, clear the inbox to zero, prepare \
the next agenda).
- Use only facts the tools returned. Never invent events, senders or \
deadlines. If a tool call fails, say so in one line and continue with the \
rest.
"""

TEMPLATE = AutomationTemplate(
    key="daily_planner",
    category="productivity",
    icon="calendar-check",
    name=LocalizedText(
        en="Daily Planner",
        de="Tagesplaner",  # i18n-allow
        es="Planificador diario",
    ),
    description=LocalizedText(
        en=(
            "A realistic plan for today: your meetings, the free focus blocks "
            "between them, and three quick wins."
        ),
        de=(  # i18n-allow
            "Ein realistischer Plan für heute: deine Termine, die freien "
            "Fokusblöcke dazwischen und drei schnelle Erfolge."
        ),
        es=(
            "Un plan realista para hoy: tus reuniones, los bloques de "
            "concentración libres entre ellas y tres logros rápidos."
        ),
    ),
    schedule=TemplateSchedule(kind="daily", time="08:30"),
    prompt=_PROMPT,
    plugin_grants=(
        PluginGrant(plugin_id="google_calendar", scope="read"),
        PluginGrant(plugin_id="gmail", scope="read"),
    ),
    requires=("google_calendar",),
    inputs=(
        TemplateInput(
            key="work_hours",
            label=LocalizedText(
                en="Work hours",
                de="Arbeitszeit",  # i18n-allow
                es="Horario laboral",
            ),
            placeholder=LocalizedText(
                en="09:00-18:00",
                de="09:00-18:00",
                es="09:00-18:00",
            ),
            default="09:00-18:00",
            required=False,
        ),
    ),
    model_tier="auto",
    tags=("productivity", "calendar", "mail", "daily"),
)
