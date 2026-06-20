"""Regression guard: the router answers evergreen knowledge DIRECTLY and reserves
``search_web`` for genuinely fresh / volatile facts (or an explicit search
request) — the "Frische-Grenze" mandate.

Live forensic 2026-06-20 (voice session e0898d6e, 16:12): a plain knowledge
question — "Kannst du mir sagen, was ich alles beachten muss, wenn ich nach
Melbourne auswandern moechte?" — made the router LLM fire ``search_web`` THREE
times (proven from sessions.db voice_events: 3x ``ActionProposed search_web``,
``jarvis_text`` empty) instead of just answering. The Run-Inspector labels a
``search_web`` call "Recherche"; the user read that as an unwanted
sub-agent / research spawn for a trivial question.

The deterministic force-spawn gate behaved correctly (it did NOT spawn — that
path is covered by ``test_routing.py``). The over-eager choice came from the
router prompt + the ``search_web`` tool description, which both told the model
to search for ANY "Wissensfrage / was ist X / erklaer mir X".

Mandate ("Frische-Grenze"): evergreen / general knowledge is answered from the
model's own knowledge; ``search_web`` fires only for fresh / volatile facts
(news, prices, weather, "aktuell / heute / neueste") or an explicit search
request.
"""
from __future__ import annotations

from jarvis.brain.router import SYSTEM_PROMPT
from jarvis.plugins.tool.search_web import SearchWebTool


def test_search_web_description_reserved_for_fresh_facts() -> None:
    """The tool's own function declaration must scope it to fresh facts.

    This is the highest-leverage lever — the LLM sees ``SearchWebTool.description``
    on every turn as the search_web function declaration, independent of which
    system-prompt path (capability block vs. fallback) is active.
    """
    desc = SearchWebTool.description.lower()
    # The fresh / volatile doctrine must be present.
    assert "aktuell" in desc, (
        "search_web description must restrict the tool to current/fresh facts"
    )
    # The evergreen carve-out must be present so the model answers general
    # knowledge directly instead of searching it.
    assert "evergreen" in desc or "allgemeinwissen" in desc, (
        "search_web description must tell the model NOT to search evergreen "
        "knowledge it can answer directly"
    )
    # The old over-eager evergreen triggers must be gone — these turned a plain
    # "explain X" / "what is X" question into a web search (Melbourne forensic).
    assert "erklär mir x" not in desc and "erklaer mir x" not in desc, (
        "search_web description still advertises evergreen 'explain X' as a "
        "primary trigger — that is the over-eager research bug"
    )


def test_router_prompt_answers_evergreen_knowledge_directly() -> None:
    """The router doctrine must answer evergreen knowledge directly."""
    low = SYSTEM_PROMPT.lower()
    assert "evergreen" in low, (
        "router prompt is missing the evergreen-knowledge-answered-directly "
        "doctrine (Frische-Grenze, 2026-06-20)"
    )
    assert "frische" in low, (
        "router prompt must reserve search_web for fresh / volatile facts"
    )
    # search_web must STILL be advertised for genuinely fresh queries (news) —
    # never regress to the 'spawn a worker / never search' extreme.
    assert "search_web" in low and "news" in low


def test_router_prompt_pins_the_emigration_forensic_as_direct_answer() -> None:
    """Anchor the exact forensic: an emigration-basics question is evergreen
    knowledge answered directly, NOT a search_web case.

    This is the established pinning style in this codebase (the prompt pins
    'einstein' / 'hauptstadt' / 'news' as category examples). Pinning the
    Melbourne case keeps the doctrine from silently regressing.
    """
    low = SYSTEM_PROMPT.lower()
    assert "auswander" in low, (
        "pin the Melbourne forensic: an emigration-basics question is "
        "evergreen knowledge answered directly, not searched"
    )
