"""Per-turn relevance gate for plugin tools. Keyword-only, no LLM, no IO (AP-9).

A plugin tool is namespaced "<plugin_id>/<tool>". We keep a plugin's tools when
its usage-card keywords match the utterance. Non-namespaced tools (the native
router tools) are never touched.

Gating triggers on the TOTAL number of plugin tools, not the plugin count: a
single heavy plugin (e.g. GitHub exposes ~37 tools) already bloats the surface,
while a couple of light plugins do not. So we skip gating only while the total
plugin-tool count is <= ``max_unfiltered_tools`` (over-offering a handful beats
wrongly dropping a relevant one); above that we keep only the plugins whose
usage-card keywords match the utterance. Gating is plugin-granular: a matched
plugin contributes all its tools, an unmatched one contributes none.
"""
from __future__ import annotations

from typing import Any

from jarvis.marketplace.usage_cards.loader import load_usage_card

# Above this many plugin tools in the turn's surface, gate by keywords. Chosen
# so a single connected GitHub/Linear-class plugin (35-37 tools) trips the gate
# while a couple of light plugins (a few tools each) stay fully visible.
_DEFAULT_MAX_UNFILTERED_TOOLS = 12


def _plugin_id_of(tool_name: str) -> str | None:
    pid, sep, _ = tool_name.partition("/")
    return pid if sep else None


def filter_plugin_tools(
    user_text: str,
    tools: list[Any],
    *,
    max_unfiltered_tools: int = _DEFAULT_MAX_UNFILTERED_TOOLS,
) -> list[Any]:
    plugin_tools = [t for t in tools if _plugin_id_of(t.name)]
    if len(plugin_tools) <= max_unfiltered_tools:
        # Small surface — keep everything, no bloat and no wrong-drop risk.
        return list(tools)

    plugin_ids = {pid for t in plugin_tools if (pid := _plugin_id_of(t.name))}
    relevant: set[str] = set()
    for pid in plugin_ids:
        card = load_usage_card(pid)
        # No card => can't gate it => keep it (conservative).
        if card is None or card.matches(user_text):
            relevant.add(pid)

    kept: list[Any] = []
    for t in tools:
        pid = _plugin_id_of(t.name)
        if pid is None or pid in relevant:
            kept.append(t)
    return kept
