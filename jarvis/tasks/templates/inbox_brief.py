"""``inbox_brief`` — unread mail sorted by urgency, once a day.

The automation only READS mail: the ``gmail`` grant stays at ``read`` scope
because ``list_messages`` / ``get_message`` are ``safe``-tier actions
(``jarvis/plugins/tool/gmail_rest.py``), so an unattended run never hits the
approval gate and never sends anything.
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
You are compiling the user's daily inbox brief. Use the `gmail` tool — you \
never send, modify or delete anything.

Steps:
1. Call `gmail` with action `list_messages`, query \
`is:unread newer_than:{lookback_hours}h in:inbox -category:promotions \
-category:social` and max_results 15. The result contains message ids only — \
no subjects, no senders — so you cannot judge anything from it yet.
2. Call `gmail` with action `get_message` for EVERY id returned (one call per \
id). Only then do you know sender, subject, date and a short body.
3. Skip newsletters, marketing, social notifications, automated receipts and \
system alerts — unless one is clearly urgent (payment failed, account locked, \
security warning, deadline today). Keep mail where a person wants something \
from the user: a question, a request, a deadline, an invoice, a confirmation.
4. Sort what remains into three groups, most urgent first:
   - "Needs reply today": a person is waiting for an answer, or something is due today.
   - "FYI": worth knowing, no action needed.
   - "Can wait": low priority, can be handled later.

Output rules — follow them exactly:
- Write in the configured output language.
- No emojis, no preamble, no closing remark, no markdown headings.
- Three short group labels (translate them), each followed by its items.
- One line per mail: the sender's name (person or company, never the address), \
then a comma, then ONE clause saying what they want or what it is about. \
Mention a deadline or amount only when the mail states it.
- Maximum 8 lines in total including the group labels; drop the least \
important items first, and drop an empty group entirely.
- Only report what the tool actually returned. Never invent senders, subjects \
or deadlines. Do not describe how you worked.
- If there is no unread or recent mail worth mentioning, reply with exactly \
one sentence saying the inbox is clear.
- If the `gmail` tool reports that Gmail is not connected, say so in one \
sentence and stop.
"""

TEMPLATE = AutomationTemplate(
    key="inbox_brief",
    category="productivity",
    icon="mail",
    name=LocalizedText(
        en="Inbox Brief",
        de="Posteingang-Briefing",  # i18n-allow
        es="Resumen de bandeja",
    ),
    description=LocalizedText(
        en=(
            "Unread mail sorted by urgency, with who wants what, and which "
            "ones need a reply today."
        ),
        de=(  # i18n-allow
            "Ungelesene Mails nach Dringlichkeit sortiert: wer will was, und "
            "welche brauchen heute eine Antwort."
        ),
        es=(
            "Correo sin leer ordenado por urgencia: quién quiere qué y cuáles "
            "necesitan respuesta hoy."
        ),
    ),
    schedule=TemplateSchedule(kind="daily", time="09:00"),
    prompt=_PROMPT,
    plugin_grants=(PluginGrant(plugin_id="gmail", scope="read"),),
    requires=("gmail",),
    inputs=(
        TemplateInput(
            key="lookback_hours",
            label=LocalizedText(
                en="Look back (hours)",
                de="Zeitraum (Stunden)",  # i18n-allow
                es="Periodo (horas)",
            ),
            placeholder=LocalizedText(en="24", de="24", es="24"),
            default="24",
            required=False,
        ),
    ),
    model_tier="auto",
    tags=("mail", "daily"),
)
