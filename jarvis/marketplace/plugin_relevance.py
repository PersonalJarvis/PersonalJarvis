"""Per-turn relevance gate for plugin tools. Keyword-only, no LLM, no IO (AP-9).

A plugin tool is namespaced ``<plugin_id>/<tool>``. We keep a plugin's tools
only when the turn actually SIGNALS that plugin; a connected-but-unmentioned
plugin/MCP server is dropped so it is never offered to the brain just because it
happens to be connected. Non-namespaced tools (the native router tools) are
never touched.

A plugin is relevant this turn when EITHER of these holds:

  * the user explicitly NAMES it — its id, or a separator-stripped human form, so
    "NotebookLM" / "notebook lm" / "notebook-lm" all match the id
    ``notebooklm-mcp``; or
  * it has a usage card whose keywords match the utterance.

A card-less plugin/MCP that the user did NOT name is NOT relevant — its only
cheap signal is its own name, so when the name is absent it stays hidden.
(Authoring a usage card adds richer keywords; the explicit-name path is the
always-available escape hatch.)

This replaces the old fail-open behavior — keep-if-no-card plus a small-surface
bypass that skipped the gate entirely for tiny surfaces. Together those let a
connected MCP server with no card (e.g. ``notebooklm-mcp``) survive on an
unrelated turn: a plain flight question reflexively fired
``notebooklm-mcp/chat_configure``, wasting ~35s before timing out. We now run the
per-plugin relevance decision for EVERY connected plugin regardless of surface
size — a tiny surface of carded plugins is still gated correctly by their cards,
and a tiny surface of card-less MCP servers is exactly what must be gated.

We deliberately derive the card-less signal from the plugin's NAME only — not
from its tool names or descriptions, which are generic verbs ("create",
"configure", "chat", "report") that would re-introduce the exact over-trigger
this gate removes. Richer keywords belong in a hand-authored usage card.
"""
from __future__ import annotations

import re
from typing import Any

from jarvis.marketplace.usage_cards.loader import load_usage_card

# Common MCP-server id suffixes that are not part of the spoken product name —
# stripped before deriving the human-readable form ("notebooklm-mcp" -> the name
# "notebooklm", not "notebooklm mcp").
_ID_SUFFIXES = ("-mcp", "_mcp", "-server", "_server")

# Minimum length of a plugin's normalized name token before we accept a bare
# substring match against the (also normalized) utterance — guards a 2-3 char id
# from matching random letters inside an unrelated word.
_MIN_NAME_TOKEN = 4

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _plugin_id_of(tool_name: str) -> str | None:
    pid, sep, _ = tool_name.partition("/")
    return pid if sep else None


def _normalize(text: str) -> str:
    """Lowercase and drop every non-alphanumeric char, so spacing/punctuation
    variants of a plugin name collapse to one token ("Notebook LM" ->
    "notebooklm")."""
    return _NON_ALNUM.sub("", (text or "").lower())


def _name_tokens(plugin_id: str) -> set[str]:
    """Normalized name tokens the user could utter to NAME this plugin: the full
    id (separators removed) plus the same with a trailing server-suffix stripped.

    Short tokens (< ``_MIN_NAME_TOKEN``) are dropped to avoid spurious substring
    hits inside unrelated words. Keyword-only, no IO.
    """
    low = plugin_id.lower()
    candidates = {low}
    for suffix in _ID_SUFFIXES:
        if low.endswith(suffix):
            candidates.add(low[: -len(suffix)])
    return {tok for c in candidates if len(tok := _normalize(c)) >= _MIN_NAME_TOKEN}


def _utterance_names_plugin(plugin_id: str, user_norm: str) -> bool:
    if not user_norm:
        return False
    return any(tok in user_norm for tok in _name_tokens(plugin_id))


def _plugin_is_relevant(plugin_id: str, user_text: str, user_norm: str) -> bool:
    # 1. The user said the plugin's name -> always relevant (works even without a
    #    card, and even if a card's keywords happen to miss the name itself).
    if _utterance_names_plugin(plugin_id, user_norm):
        return True
    # 2. A usage card gates by its curated keywords.
    card = load_usage_card(plugin_id)
    if card is not None:
        return card.matches(user_text)
    # 3. Card-less and un-named -> not signaled this turn. No fail-open: a
    #    connected MCP server must EARN its place on the turn, not ride along.
    return False


def filter_plugin_tools(user_text: str, tools: list[Any]) -> list[Any]:
    """Drop namespaced plugin tools the turn does not signal; keep native tools.

    Defensive: a malformed tool name simply yields no plugin id and is treated as
    native (kept). Callers additionally wrap this in try/except so a gate fault
    can never blind the brain on the voice path.
    """
    plugin_ids = {pid for t in tools if (pid := _plugin_id_of(t.name))}
    if not plugin_ids:
        return list(tools)  # nothing namespaced to gate

    user_norm = _normalize(user_text)
    relevant = {
        pid
        for pid in plugin_ids
        if _plugin_is_relevant(pid, user_text, user_norm)
    }

    kept: list[Any] = []
    for t in tools:
        pid = _plugin_id_of(t.name)
        if pid is None or pid in relevant:
            kept.append(t)
    return kept
