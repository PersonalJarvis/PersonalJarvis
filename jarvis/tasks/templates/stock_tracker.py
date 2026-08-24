"""Daily Stock Tracker — prices, sentiment, and catalysts for a watchlist.

Two ``search_web`` calls per ticker (one for the latest price and day move,
one for news), then one line per ticker with the approximate last price as
the source reported it, the day move, and one catalyst with its source
name. Numbers are never invented: a ticker whose price the searches did not
surface is reported as "price not found".
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
You are compiling today's stock tracker for this watchlist: {watchlist}.

Step 1 — gather, per ticker. For EACH ticker in the watchlist call the \
`search_web` tool exactly twice, with `max_results` 8. Call A (price): \
`query` "<TICKER> stock price today" plus two variants in `queries` such as \
"<TICKER> share price change today" and "<TICKER> stock quote <current \
date>". Call B (news): `query` "<TICKER> stock news today" plus variants \
such as "<TICKER> earnings analyst news this week" and "why is <TICKER> \
stock up or down today". Put a recency cue in every query ("today", the \
current date). Do not skip a ticker and do not stop after the first ticker; \
two tickers means four calls.

Step 2 — extract. The tool returns RAW web hits (title, snippet, url) — \
read figures only from that text. For each ticker take the most recent price \
figure and the day's percentage or point move exactly as a snippet or title \
states them, and name the outlet from the hit's title or domain. Prefer the \
hit with the newest date cue; ignore hits with no date or older than two \
trading days. If no hit states a concrete price, the price is unknown — \
never estimate it from memory or from an older figure. Pick ONE catalyst: \
the single most relevant news item explaining the move or likely to move \
the stock next (earnings, guidance, product, regulation, analyst action, \
macro event).

Step 3 — write. Return exactly one line per ticker, in watchlist order, in \
this shape:
<TICKER> — approx. <price> <currency>, <day move> (per <source name>) — \
<one catalyst sentence> (<source name>)
If the price could not be established, write "price not found" in place of \
the price and move, and still give the catalyst if one was found. If no \
news was found either, say "no notable news found" in place of the catalyst.

Then end with ONE sentence describing the overall market mood for the day \
based only on what the searches returned.

Rules: write in the configured output language (translate the fixed phrases \
accordingly, but keep ticker symbols as they are); no headline, no preamble, \
no closing remark; no emojis; no bold, asterisks, or markdown formatting; \
never invent or round-trip numbers from memory — every figure must come from \
a search result; this is information only, so give no buy, sell, or hold \
advice and no predictions of your own.
"""

TEMPLATE = AutomationTemplate(
    key="stock_tracker",
    category="finance",
    icon="dollar-sign",
    name=LocalizedText(
        en="Daily Stock Tracker",
        de="Täglicher Aktien-Tracker",
        es="Seguimiento diario de acciones",
    ),
    description=LocalizedText(
        en="Prices, sentiment, and catalysts for your watchlist every day.",
        de="Kurse, Stimmung und Kurstreiber für deine Watchlist, jeden Tag.",
        es="Precios, sentimiento y catalizadores de tu lista de seguimiento cada día.",
    ),
    schedule=TemplateSchedule(kind="daily", time="14:00"),
    prompt=_PROMPT,
    plugin_grants=(PluginGrant(plugin_id="search_web", scope="read"),),
    requires=("search_web",),
    inputs=(
        TemplateInput(
            key="watchlist",
            label=LocalizedText(
                en="Watchlist",
                de="Watchlist",
                es="Lista de seguimiento",
            ),
            placeholder=LocalizedText(
                en="NVDA, AAPL, TSLA",
                de="NVDA, AAPL, TSLA",
                es="NVDA, AAPL, TSLA",
            ),
            default="",
            required=True,
        ),
    ),
    model_tier="auto",
    tags=("stocks", "finance", "daily"),
)
