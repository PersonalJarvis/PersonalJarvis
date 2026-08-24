"""Task Extractor — the action items hiding in today's mail, remembered.

Runs once a day, late afternoon. The prompt grounds everything in the Gmail
tool: one ``list_messages`` search over the lookback window (newsletters and
notifications excluded by query), then ``get_message`` for each hit, because
``list_messages`` only returns ids. Every action item must be traceable to a
mail the tool actually returned. The list is then persisted through
``wiki-ingest`` (a ``write`` grant, so the unattended run never blocks on an
approval prompt) as one note headed ``Action items <date>``, and the final
answer IS the list — short, in the configured output language, or the single
sentence "no action items today" when the mail contained none.

Why ``wiki-ingest`` and not ``remember``: ``remember`` is registered as an
entry point but is deliberately NOT a router-tier tool (ADR-0011), so the
brain's live tool dict never contains it and a task grant for it is silently
dropped by ``BrainManager._select_task_tools``. ``wiki-ingest`` IS live,
takes free text, and lands in the long-term wiki the brain recalls from.
Storage is a soft requirement — ``requires`` names only ``gmail``; if the
wiki tool is missing the prompt says so instead of pretending.
"""
from __future__ import annotations

from jarvis.tasks.schema import PluginGrant
from jarvis.tasks.templates import (
    AutomationTemplate,
    LocalizedText,
    TemplateInput,
    TemplateSchedule,
)

_PROMPT = """\
You are extracting the concrete action items from the user's mail of the last \
{lookback_hours} hours. Today's date is in your context; every item below MUST \
come from a message the `gmail` tool actually returned — never invent a task, \
a sender or a deadline.

Step 1 — find the mail. Call `gmail` with action "list_messages", max_results 25 \
and this query (convert the hours into whole days, rounding up: 24 hours = 1d, \
48 hours = 2d):
  in:inbox newer_than:<N>d -category:promotions -category:social -category:forums \
-unsubscribe -"no-reply"
Step 2 — read it. `list_messages` returns only ids: call `gmail` with action \
"get_message" for each id (at most 15 messages, newest first). Read the sender, \
subject, date and body.
Step 3 — extract. An action item is something a real person asks the user to do, \
decide, send, review, pay, confirm or answer, or an obligation with a date \
(invoice due, appointment to confirm, form to return). Skip newsletters, \
marketing, automated notifications, receipts and "FYI" mail with nothing to do. \
For each item capture: who asked (name, no email address), what exactly, and by \
when (the stated date or "no deadline given"). Merge duplicates from the same \
thread. Keep at most 8 items, most urgent first.
Step 4 — store. If at least one item exists, call `wiki-ingest` ONCE with \
`source` "automation:task_extractor" and `text` being one self-contained \
block of plain prose of the form
  Action items <today's date YYYY-MM-DD>: 1) <who> — <what> — <by when>; 2) ...
so the list survives this turn as a wiki note. Do not call it when there are \
no items. If the `wiki-ingest` tool is not available or returns an error, do \
not pretend the list was stored — say so in one short final sentence.
Step 5 — answer. Reply with the numbered list only, one line per item in the \
form "<who>: <what> (by <when>)". If there are no action items, reply with the \
single sentence "no action items today" (translated into the output language).

Rules: no preamble, no closing remark, no emojis, no markdown headings, do not \
describe your process or the tools. If the mail tool returned an error, say that \
in one sentence instead of inventing a list. Write everything in the configured \
output language.
"""

TEMPLATE = AutomationTemplate(
    key="task_extractor",
    category="productivity",
    icon="list-checks",
    name=LocalizedText(
        en="Task Extractor",
        de="Aufgaben-Extraktor",  # i18n-allow
        es="Extractor de tareas",
    ),
    description=LocalizedText(
        en="Pulls the action items out of today's mail and remembers them, so nothing slips.",
        de=(  # i18n-allow
            "Zieht die Aufgaben aus den Mails von heute und merkt sie sich, "
            "damit nichts durchrutscht."
        ),
        es=(
            "Saca las tareas pendientes del correo de hoy y las recuerda "
            "para que nada se escape."
        ),
    ),
    schedule=TemplateSchedule(kind="daily", time="17:00"),
    prompt=_PROMPT,
    plugin_grants=(
        PluginGrant(plugin_id="gmail", scope="read"),
        # write: the unattended run must be allowed to store the list
        # without a human answering an approval prompt.
        PluginGrant(plugin_id="wiki-ingest", scope="write"),
    ),
    requires=("gmail",),
    inputs=(
        TemplateInput(
            key="lookback_hours",
            label=LocalizedText(
                en="Lookback window (hours)",
                de="Zeitfenster (Stunden)",  # i18n-allow
                es="Ventana de tiempo (horas)",
            ),
            placeholder=LocalizedText(
                en="24",
                de="24",
                es="24",
            ),
            default="24",
            required=False,
        ),
    ),
    model_tier="auto",
    tags=("mail", "tasks", "memory"),
)
