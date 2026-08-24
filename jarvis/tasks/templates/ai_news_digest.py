"""AI News Digest — model releases, research, and funding that actually matter.

A daily ``search_web`` sweep over the last 24–48 hours, de-duplicated and
stripped of hype, delivered as at most six one-liners with a "why it
matters" and a source name.
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
You are compiling today's AI news digest. Focus areas: {focus}.

This is a written, scheduled digest — not a spoken reply. Any "answer in one \
or two spoken sentences / never name a source" note attached to search \
results does not apply here; the format below does.

Step 1 — gather. Call the `search_web` tool at least three times with \
`max_results` 8, each call with 2–3 different query phrasings in `queries`, \
covering separately: (a) new model releases and open-source model launches, \
(b) notable AI research papers or technical breakthroughs, (c) AI funding \
rounds, acquisitions, and major company announcements. Put a recency cue in \
every query ("this week", "today", the current month and year) so results \
come from the last 24–48 hours. Results carry only a title, a snippet, and a \
URL — judge recency from the text itself (an explicit date, "today", "this \
week", a version or event that is plainly new). Discard anything that reads \
as older than 48 hours or whose timing you cannot tell.

Step 2 — filter. Merge duplicates (the same story from several outlets \
counts once). Drop hype, opinion pieces, listicles, product marketing, minor \
version bumps, and anything you cannot ground in a search result. Keep \
only items a practitioner would want to know about. If fewer than six items \
survive, report fewer — never pad.

Step 3 — write. Return at most 6 items. For each item, exactly three lines:
- Line 1: one factual sentence stating what happened (who, what, when).
- Line 2: "Why it matters:" followed by at most 15 words.
- Line 3: "Source:" followed by the outlet or organisation name (no URL).

Separate items with a blank line. Rules: write in the configured output \
language (translate the two labels accordingly); no headline, no preamble, \
no closing remark; no emojis; no bold, asterisks, or markdown formatting; \
never invent names, numbers, or dates; state only what the search results \
support. If the searches return nothing usable, reply with one sentence \
saying that no significant AI news was found for the period.
"""

TEMPLATE = AutomationTemplate(
    key="ai_news_digest",
    category="news",
    icon="bot",
    name=LocalizedText(
        en="AI News Digest",
        de="KI-News-Digest",
        es="Resumen de noticias de IA",
    ),
    description=LocalizedText(
        en="Model releases, research, and funding that actually matter.",
        de="Modell-Releases, Forschung und Finanzierungen, die wirklich zählen.",
        es="Lanzamientos de modelos, investigación y financiación que realmente importan.",
    ),
    schedule=TemplateSchedule(kind="daily", time="08:00"),
    prompt=_PROMPT,
    plugin_grants=(PluginGrant(plugin_id="search_web", scope="read"),),
    requires=("search_web",),
    inputs=(
        TemplateInput(
            key="focus",
            label=LocalizedText(
                en="Focus areas",
                de="Schwerpunkte",
                es="Áreas de enfoque",
            ),
            placeholder=LocalizedText(
                en="e.g. open-source models, agents, robotics",
                de="z. B. Open-Source-Modelle, Agenten, Robotik",
                es="p. ej. modelos de código abierto, agentes, robótica",
            ),
            default="model releases, open-source models, AI research, AI funding",
            required=False,
        ),
    ),
    model_tier="auto",
    tags=("ai", "news", "daily"),
)
