"""Memory Recap — a weekly look at what Jarvis learned about the user and
their projects, drawn from the local wiki/memory only.

This is the one catalogue template that works fully offline: every grant
is a read-only local memory tool, no external key or network is needed.
"""
from __future__ import annotations

from jarvis.tasks.schema import PluginGrant
from jarvis.tasks.templates import (
    AutomationTemplate,
    LocalizedText,
    TemplateSchedule,
)

_PROMPT = """\
You are compiling the weekly Memory Recap: what Jarvis learned about the \
user and their projects during the last 7 days, and the open threads worth \
picking up. Everything comes from the user's LOCAL wiki/memory — do not use \
the web, and never fill gaps from general knowledge.

Procedure:
1. Call `wiki-list` once to see every page that actually exists (path, size, \
title). The listing carries no modification dates. A page flagged as a \
system file is the vault's editing contract (`schema.md`) — never treat its \
example layout as content. Exception: a self-description page such as \
`memory.md` ("My Memory") may be flagged too, yet it holds a "Recently \
updated" list and live status — read it FIRST with `wiki-page-read` when it \
exists and use its list as the shortlist of pages that changed recently.
2. Work out today's date and the dates of the last 7 days from the \
timestamp context you have. Run `wiki-recall` with 2-3 of those dates as \
plain ISO strings (for example "2026-08-24") — the vault's activity log \
records dated entries and page frontmatter carries `updated:` dates, so the \
hits reveal which pages and events belong to this week. The log page itself \
can be too large to read; rely on the recall snippets for it.
3. Call `wiki-page-read` on the pages most likely to have changed this week \
(at most 8: the "Recently updated" shortlist, then hits from step 2). On \
each page look at frontmatter `updated:`, facts marked "(as of <date>)", \
"Current Status" and "Open Threads" sections — those are the source for the \
themes and the open items.
4. Then run `wiki-recall` with 2-3 short keyword queries built from the \
names and projects you found, plus one for open items (e.g. "open threads", \
"blocked", "next steps"). Follow at most 3 promising hits with \
`wiki-page-read`.
5. Keep only facts that appear in the tool output. Never invent people, \
projects, dates or decisions. If you cannot tell whether a note is from this \
week, treat it as recent background rather than claiming a date.

Output rules:
- Write in the configured output language.
- Cluster the findings into 3 to 5 themes (projects, people, decisions, \
habits, tools). One theme per block: a heading line with the theme name, \
then one line with what was learned (concrete names and facts from the \
notes), then one line beginning with "Open:" that names ONE open question or \
next step for that theme. Never leave the "Open:" line empty; when the notes \
contain no open item, propose the most natural next step for that theme.
- At most 12 lines in total. Short, specific sentences.
- No preamble, no closing remarks, no emojis, no markdown tables.
- When `wiki-list` reports that the vault is empty or does not exist, or \
when no page carries any usable content, reply with exactly one line saying \
plainly that the memory is empty and there is nothing to recap yet. Do not \
add advice.
- If a memory tool itself fails, say so in one line instead of guessing.
"""

TEMPLATE = AutomationTemplate(
    key="memory_recap",
    category="research",
    icon="brain",
    name=LocalizedText(
        en="Memory Recap",
        de="Gedächtnis-Rückblick",  # i18n-allow
        es="Resumen de memoria",
    ),
    description=LocalizedText(
        en=(
            "What Jarvis learned about you and your projects this week, and "
            "the open threads worth picking up. Runs fully offline from your "
            "local memory — no external key needed."
        ),
        de=(  # i18n-allow
            "Was Jarvis diese Woche über dich und deine Projekte gelernt hat, "
            "und welche offenen Fäden sich lohnen. Läuft komplett offline aus "
            "deinem lokalen Gedächtnis — kein externer Schlüssel nötig."
        ),
        es=(
            "Lo que Jarvis aprendió esta semana sobre ti y tus proyectos, y los "
            "hilos abiertos que vale la pena retomar. Funciona totalmente sin "
            "conexión desde tu memoria local — no necesita ninguna clave externa."
        ),
    ),
    schedule=TemplateSchedule(kind="weekly", weekday=6, time="18:00"),
    prompt=_PROMPT,
    plugin_grants=(
        PluginGrant(plugin_id="wiki-recall", scope="read"),
        PluginGrant(plugin_id="wiki-list", scope="read"),
        PluginGrant(plugin_id="wiki-page-read", scope="read"),
    ),
    requires=("wiki-recall",),
    inputs=(),
    model_tier="auto",
    tags=("research", "memory", "offline", "weekly"),
)
