"""Per-turn relevance gate for plugin tools. Keyword-only, no LLM, no IO (AP-9).

A plugin tool is namespaced ``<plugin_id>/<tool>``. We keep a plugin's tools
only when the turn actually SIGNALS that plugin; a connected-but-unsignaled
plugin/MCP server is dropped so it is never offered to the brain just because it
happens to be connected. Non-namespaced tools (the native router tools) are
never touched.

A plugin is relevant this turn when ANY of these holds:

  * the user explicitly NAMES it — its id, or a separator-stripped human form, so
    "NotebookLM" / "notebook lm" / "notebook-lm" all match the id
    ``notebooklm-mcp``; or
  * it has a usage card whose curated keywords match the utterance; or
  * a DISTINCTIVE noun auto-derived from the plugin's OWN tools (their names and
    descriptions) matches the utterance.

The third (smart) stage is what lets a deliberately-added card-less MCP fire on
a topical word it actually serves: a "weather" MCP whose tools carry the noun
``weather``/``forecast`` fires on "what's the weather", and a NotebookLM MCP
fires on "make some flashcards" / "build a mind map" / "make a podcast". The
nouns are mined from each tool's name (``flashcards_create`` -> ``flashcards``)
and description, MINUS a generic stoplist of MCP-shared verbs/structural words
(``create`` / ``list`` / ``configure`` / ``chat`` / ``report`` / ...) and common
English function words. That stoplist is the guard that keeps the smart stage
from re-introducing the original over-trigger: a plain flight question signals
NotebookLM in none of the three ways, so it stays dropped (it once reflexively
fired ``notebooklm-mcp/chat_configure``, wasting ~35s before timing out).

The relevance decision runs for EVERY connected plugin regardless of surface
size — there is no small-surface bypass that could let a card-less server leak
onto an unrelated turn.

Two public entry points are shared by the router gate (``filter_plugin_tools``)
and a sibling worker-export path, so the keyword/relevance logic has ONE home:

  * ``derive_plugin_keywords(plugin_id, tools)`` — the distinctive keyword set.
  * ``plugin_is_relevant(text, plugin_id, tools)`` — the full per-turn decision.
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

# Minimum length of an auto-derived tool noun, same rationale (a 1-3 char token
# carries no topical signal and risks spurious substring hits).
_MIN_NOUN = 4

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_NAME_SPLIT = re.compile(r"[/_\-]+")
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")

# Generic action verbs every MCP shares — mirrored from the MCP adapter's
# ``_KNOWN_VERBS`` (jarvis/mcp/adapter.py). Reimplemented here (not imported) to
# keep this gate dependency-light and free of private cross-module coupling.
_KNOWN_VERBS = (
    "send", "create", "delete", "update", "list", "get", "fetch",
    "read", "write", "search", "query", "insert", "execute", "run",
    "upload", "download", "publish", "schedule", "post", "set",
    "add", "remove", "edit", "modify", "retrieve", "find",
)

# The generic stoplist: the shared verbs above plus structural words present in
# almost every MCP tool name/description. A keyword in this set never makes a
# plugin relevant — this is the guard that stops a generic verb like
# "create"/"list"/"configure" from re-introducing the over-trigger.
_GENERIC_STOPLIST = frozenset(_KNOWN_VERBS) | {
    "chat", "configure", "report", "tool", "server", "mcp",
    "action", "system", "operation", "general",
}

# Common English function/filler words (length >= 4, so length alone does not
# already drop them). Mined from a tool DESCRIPTION's prose, these would leak as
# bogus "topical" keywords ("from your sources", "with the given input") and
# re-introduce the over-trigger — so they are stoplisted too. Descriptions are
# English by project policy, so an English set suffices.
_FUNCTION_WORDS = frozenset({
    "this", "that", "these", "those", "with", "from", "into", "onto", "upon",
    "your", "yours", "their", "them", "they", "theirs", "what", "which",
    "when", "then", "than", "here", "there", "where", "while", "will",
    "would", "could", "should", "shall", "must", "have", "having", "been",
    "being", "does", "done", "about", "over", "under", "again", "once",
    "only", "also", "such", "each", "every", "both", "some", "more", "most",
    "many", "much", "very", "just", "like", "else", "none", "between",
    "within", "without", "before", "after", "given", "using", "uses", "used",
    "based", "make", "made", "want", "need", "please", "thing", "things",
    # structural schema-noise nouns that are not topical signals.
    "name", "names", "type", "types", "field", "fields", "value", "values",
    "input", "output", "result", "results", "response", "request", "data",
    "item", "items", "user", "users", "default", "optional", "param", "params",
    "object", "objects", "string", "number", "boolean", "array",
})

# The full stoplist applied to auto-derived nouns.
_STOPLIST = _GENERIC_STOPLIST | _FUNCTION_WORDS


def _attr(obj: Any, key: str) -> str:
    """Read ``name``/``description`` from a tool given as an object (``.attr``)
    or a plain dict (``["key"]``). Defensive: returns ``""`` for anything else."""
    val = getattr(obj, key, None)
    if val is None and isinstance(obj, dict):
        val = obj.get(key)
    return val if isinstance(val, str) else ""


def _plugin_id_of(tool_name: str) -> str | None:
    pid, sep, _ = tool_name.partition("/")
    return pid if sep else None


def _normalize(text: str) -> str:
    """Lowercase and drop every non-alphanumeric char, so spacing/punctuation
    variants of a name/keyword collapse to one token ("Notebook LM" ->
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


def _derive_tool_nouns(plugin_id: str, tools: list[Any]) -> set[str]:
    """Distinctive nouns mined from THIS plugin's own tools (namespace-scoped).

    For every tool whose name belongs to ``plugin_id`` we take the tokens of the
    tool name (after the ``<plugin_id>/`` prefix, split on ``/ _ -``) plus the
    tokens of its description, keep those of length >= ``_MIN_NOUN``, and drop the
    generic verb/structural/function-word stoplist. Keyword-only, no IO; defensive
    against malformed tool objects.
    """
    nouns: set[str] = set()
    for t in tools:
        name = _attr(t, "name")
        if _plugin_id_of(name) != plugin_id:
            continue  # only this plugin's own tools contribute (no leakage)
        tool_part = name.partition("/")[2] or name
        for tok in _NAME_SPLIT.split(tool_part.lower()):
            if len(tok) >= _MIN_NOUN:
                nouns.add(tok)
        for tok in _WORD_SPLIT.split(_attr(t, "description").lower()):
            if len(tok) >= _MIN_NOUN:
                nouns.add(tok)
    return {n for n in nouns if n not in _STOPLIST}


def derive_plugin_keywords(plugin_id: str, tools: list[Any]) -> set[str]:
    """Distinctive keyword set for a plugin/MCP server.

    The UNION of three sources: the plugin id's normalized name tokens (so naming
    the plugin matches), its usage-card keywords if a card exists, and topical
    nouns auto-derived from the plugin's OWN tool names + descriptions (minus the
    generic verb/structural/function-word stoplist). Shared by the router gate and
    the worker-export path so the keyword logic has ONE definition.

    Defensive: never raises — a fault yields whatever was gathered so far (the gate
    must never blind the brain on the voice path).
    """
    keywords: set[str] = set()
    try:
        keywords |= _name_tokens(plugin_id)
        card = load_usage_card(plugin_id)
        if card is not None:
            keywords |= {kw.lower() for kw in card.keywords if kw}
        keywords |= _derive_tool_nouns(plugin_id, tools)
    except Exception:  # noqa: BLE001, S110 — gate must never blind the brain
        pass
    return keywords


def plugin_is_relevant(text: str, plugin_id: str, tools: list[Any]) -> bool:
    """Full per-turn relevance decision for one plugin.

    Relevant iff the user NAMES it, OR its usage card matches, OR any
    auto-derived tool noun matches the utterance. Card matching keeps the card's
    own substring semantics (so a multi-word keyword like "pull request" works);
    name/noun matching is substring on the normalized (separator-stripped) text.
    Defensive: any fault returns ``False`` (drop), never raises.
    """
    try:
        user_norm = _normalize(text)
        if user_norm and any(tok in user_norm for tok in _name_tokens(plugin_id)):
            return True
        card = load_usage_card(plugin_id)
        if card is not None and card.matches(text):
            return True
        nouns = _derive_tool_nouns(plugin_id, tools)
        return any(noun in user_norm for noun in nouns)
    except Exception:  # noqa: BLE001 — gate must never blind the brain
        return False


def filter_plugin_tools(user_text: str, tools: list[Any]) -> list[Any]:
    """Drop namespaced plugin tools the turn does not signal; keep native tools.

    Defensive: a malformed tool name simply yields no plugin id and is treated as
    native (kept). Callers additionally wrap this in try/except so a gate fault
    can never blind the brain on the voice path.
    """
    plugin_ids = {pid for t in tools if (pid := _plugin_id_of(_attr(t, "name")))}
    if not plugin_ids:
        return list(tools)  # nothing namespaced to gate

    relevant = {pid for pid in plugin_ids if plugin_is_relevant(user_text, pid, tools)}

    kept: list[Any] = []
    for t in tools:
        pid = _plugin_id_of(_attr(t, "name"))
        if pid is None or pid in relevant:
            kept.append(t)
    return kept
