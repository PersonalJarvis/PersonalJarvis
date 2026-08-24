"""Morning Brief — headlines, market mood and (optionally) the weather.

Runs once a day, early. The prompt forces several ``search_web`` calls so the
brief is grounded in live results rather than the brain's memory: one call
per interest area, one for the market mood, and one weather lookup when the
user configured a city (``search_web`` routes a query naming a location plus
a weather word to Open-Meteo). Without a city the brief simply has no
weather line — it must never guess where the user lives.
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
You are compiling a morning brief for {interests}. City for the weather: "{city}".
Today's date is in your context; every fact below MUST come from tool output.

Gather the material with the `search_web` tool — at least three separate calls:
1. One call PER interest area listed above (e.g. "top {interests} headlines today").
   Use the `queries` parameter with 2-3 phrasings in one call to get better coverage.
2. One call for the market mood: "stock market today S&P 500 DAX Nasdaq".
3. If, and only if, the city above is NOT empty: one call whose query is
   "weather <city> today" — the tool answers weather queries directly when the
   query names a location. If the city is empty, skip the weather entirely;
   never pick or guess a city.

Then write the brief. Rules:
- 5 to 7 bullets maximum, most important first. Each bullet is one specific,
  concrete finding (who, what, number, date) and ends with the source name in
  parentheses, e.g. "(Reuters)". The weather, if available, is one of those
  bullets: current conditions and today's high/low.
- After the bullets, ONE sentence starting with "What matters today:" (translated
  into the output language) that connects the most important items.
- Only report what the tool results actually say. If a search returned nothing
  useful for a topic, say so in one short bullet instead of inventing news.
- Do NOT mention that you searched, do NOT describe your process, no preamble,
  no closing remark, no emojis, no markdown headings.
- Write the whole brief in the configured output language.
"""

TEMPLATE = AutomationTemplate(
    key="morning_brief",
    category="news",
    icon="sun",
    name=LocalizedText(
        en="Morning Brief",
        de="Morgen-Briefing",  # i18n-allow
        es="Resumen matutino",
    ),
    description=LocalizedText(
        en="Start the day with headlines, market moves, and your weather.",
        de="Starte den Tag mit Schlagzeilen, Börsenlage und deinem Wetter.",  # i18n-allow
        es="Empieza el día con titulares, la bolsa y el tiempo en tu ciudad.",
    ),
    schedule=TemplateSchedule(kind="daily", time="07:30"),
    prompt=_PROMPT,
    plugin_grants=(PluginGrant(plugin_id="search_web", scope="read"),),
    requires=("search_web",),
    inputs=(
        TemplateInput(
            key="city",
            label=LocalizedText(
                en="City for the weather",
                de="Stadt für das Wetter",  # i18n-allow
                es="Ciudad para el tiempo",
            ),
            placeholder=LocalizedText(
                en="e.g. Berlin (leave empty to skip weather)",
                de="z. B. Berlin (leer = kein Wetter)",  # i18n-allow
                es="p. ej. Madrid (vacío = sin tiempo)",
            ),
            default="",
            required=False,
        ),
        TemplateInput(
            key="interests",
            label=LocalizedText(en="Topics", de="Themen", es="Temas"),  # i18n-allow
            placeholder=LocalizedText(
                en="world news, technology, business",
                de="Weltnachrichten, Technik, Wirtschaft",  # i18n-allow
                es="noticias del mundo, tecnología, negocios",
            ),
            default="world news, technology, business",
            required=False,
        ),
    ),
    model_tier="auto",
    tags=("news", "weather", "markets"),
)
