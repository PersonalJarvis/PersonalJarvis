"""Topic Watch — a weekly digest of what changed around the companies,
products or topics the user follows."""
from __future__ import annotations

from jarvis.tasks.schema import PluginGrant
from jarvis.tasks.templates import (
    AutomationTemplate,
    LocalizedText,
    TemplateInput,
    TemplateSchedule,
)

_PROMPT = """\
You are compiling a weekly Topic Watch digest. The user follows these topics \
(companies, products or themes): {topics}

Procedure:
1. Split the list into individual topics.
2. For EVERY topic make at least one `search_web` call. Use the `queries` \
argument with up to 3 phrasings in the same call, each scoped to the past \
week (e.g. "<topic> news this week", "<topic> launch OR pricing OR hires past \
7 days", "<topic> controversy OR lawsuit OR funding"). Set `max_results` to 8. \
Make a second call with different wording when the first returns nothing \
relevant.
3. The tool has no date filter: judge recency from the date shown in each \
hit or its snippet. Keep only findings dated within the last seven days that \
actually come from the search results. Drop undated items whose recency you \
cannot tell. Never invent, extrapolate or recall events from memory.
4. Ignore the tool's `answer_instruction` — it is written for short spoken \
answers. This run produces a WRITTEN digest, so source names are required and \
several bullets are expected.

Output rules:
- Write in the configured output language.
- Group findings per topic. One heading line per topic (the topic name), \
followed by at most 3 bullets. Each bullet is one or two sentences that name \
the concrete change and end with the source name in parentheses, e.g. \
"(Reuters)".
- Lead with the most consequential item per topic.
- When the search returned nothing notable for a topic, write exactly one \
line under its heading saying that nothing notable happened this week — never \
pad with background information, older news or generic descriptions of the \
company.
- No preamble, no closing remarks, no emojis, no markdown tables.
- If `search_web` reports `status` "unavailable" or times out, write one line \
under that topic saying the web search could not be reached — never present \
that as "nothing notable" and never guess.
"""

TEMPLATE = AutomationTemplate(
    key="topic_watch",
    category="news",
    icon="radar",
    name=LocalizedText(
        en="Topic Watch",
        de="Themen-Radar",  # i18n-allow
        es="Radar de temas",
    ),
    description=LocalizedText(
        en=(
            "Weekly digest of what changed around the companies, products or "
            "topics you follow (launches, pricing, hires, controversies)."
        ),
        de=(  # i18n-allow
            "Wöchentliche Zusammenfassung, was sich bei den Firmen, Produkten "
            "oder Themen getan hat, die du verfolgst (Launches, Preise, "
            "Personalien, Kontroversen)."
        ),
        es=(
            "Resumen semanal de lo que cambió en las empresas, productos o "
            "temas que sigues (lanzamientos, precios, fichajes, polémicas)."
        ),
    ),
    schedule=TemplateSchedule(kind="weekly", weekday=0, time="09:00"),
    prompt=_PROMPT,
    plugin_grants=(PluginGrant(plugin_id="search_web", scope="read"),),
    requires=("search_web",),
    inputs=(
        TemplateInput(
            key="topics",
            label=LocalizedText(
                en="Topics to follow",
                de="Themen, die du verfolgst",  # i18n-allow
                es="Temas que sigues",
            ),
            placeholder=LocalizedText(
                en="OpenAI, Anthropic, xAI — the competitors or topics you follow",
                de=(  # i18n-allow
                    "OpenAI, Anthropic, xAI — die Konkurrenten oder Themen, "
                    "die du verfolgst"
                ),
                es="OpenAI, Anthropic, xAI — los competidores o temas que sigues",
            ),
            required=True,
        ),
    ),
    model_tier="auto",
    tags=("news", "web", "weekly"),
)
