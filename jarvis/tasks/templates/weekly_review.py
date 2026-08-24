"""Weekly Review — wrap the week: what got done, what is still open, and the
three priorities for next week.

Runs once a week, Friday afternoon. The review is grounded in three sources
the automation may or may not have on a given install: this week's calendar
events (``google_calendar``), the notable mail threads (``gmail``) and the
memory notes of the past seven days (``wiki-recall``). None of them is a hard
requirement — with no calendar or mailbox connected the review is written
from memory alone, and the prompt tells the brain to use whatever tools are
available and to state which sources it actually used, so a thin review is
never mistaken for a full one.
"""
from __future__ import annotations

from jarvis.tasks.schema import PluginGrant
from jarvis.tasks.templates import (
    AutomationTemplate,
    LocalizedText,
    TemplateSchedule,
)

_PROMPT = """\
You are writing the user's Weekly Review. Today's date is in your context; \
"this week" means the last 7 days up to and including today.

Gather the material from whichever of these tools you have — use every one \
that is available, and if a tool is missing or fails, carry on without it:
1. `google_calendar` — call it with action "list_events", time_min = 7 days \
ago at 00:00 and time_max = today at 23:59 (RFC3339), max_results 50. \
These events are what happened this week.
2. `gmail` — call it with action "list_messages" and query "newer_than:7d", \
max_results 20. Keep only the threads that carry a decision, a request \
addressed to the user, a deadline or an open reply; ignore newsletters, \
notifications and promotions.
3. `wiki-recall` — run two or three short keyword searches for this week's \
projects, decisions and plans (e.g. the names of the projects and people \
that appear in the calendar and mail results, plus generic words like \
"plan", "decision", "todo"). Treat the hits as the user's own memory notes.

Then write the review. Rules:
- Exactly three sections with these headings, translated into the output \
language: "Done", "Open", "Next week (3 priorities)". Each line under a \
heading is one bullet.
- "Done": concrete things that happened or were completed this week (meetings \
held, decisions made, tasks closed) — who, what, when.
- "Open": items still waiting: unanswered mail, unresolved threads, notes that \
name a pending task, events that need follow-up.
- "Next week (3 priorities)": exactly three bullets, the most consequential \
things to do next week, each derived from an Open item or an upcoming event.
- Every fact MUST come from tool output. Never invent events, mails or notes; \
never fill a section with generic advice. If a section has nothing, write \
one bullet saying so.
- The whole review is at most 15 lines including the headings.
- End with ONE final line, translated into the output language, that names \
the sources actually used, e.g. "Sources: calendar, mail, memory" — list \
only the tools that returned data, and name the ones that were unavailable.
- No preamble, no closing remark, no emojis, no markdown tables. Do not \
describe your process.
- Write the whole review in the configured output language.
"""

TEMPLATE = AutomationTemplate(
    key="weekly_review",
    category="productivity",
    icon="clipboard-list",
    name=LocalizedText(
        en="Weekly Review",
        de="Wochenrückblick",  # i18n-allow
        es="Revisión semanal",
    ),
    description=LocalizedText(
        en="Wrap the week: what you did, what's still open, and next week's three priorities.",
        de=(  # i18n-allow
            "Schließe die Woche ab: was du geschafft hast, was offen ist "
            "und die drei Prioritäten für nächste Woche."
        ),
        es=(
            "Cierra la semana: lo que hiciste, lo que sigue abierto "
            "y las tres prioridades de la próxima."
        ),
    ),
    schedule=TemplateSchedule(kind="weekly", weekday=4, time="16:00"),
    prompt=_PROMPT,
    plugin_grants=(
        PluginGrant(plugin_id="google_calendar", scope="read"),
        PluginGrant(plugin_id="gmail", scope="read"),
        PluginGrant(plugin_id="wiki-recall", scope="read"),
    ),
    # Deliberately empty: the review degrades to memory-only without a
    # calendar or mailbox, so nothing blocks the card on a bare install.
    requires=(),
    inputs=(),
    model_tier="auto",
    tags=("review", "planning", "calendar", "mail", "memory"),
)
