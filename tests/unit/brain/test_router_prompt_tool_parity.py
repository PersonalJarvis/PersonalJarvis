"""Regression guard: the router prompt must not advertise tools it cannot call.

Live repro 2026-05-25 (voice "oeffne den Editor"): the router SYSTEM_PROMPT
told the model to use ``open_app`` / ``search_web`` / ``remember`` as direct
tools, but those direct-action tools were deliberately NOT in ROUTER_TOOLS
(Persona-Mandat Phase 3 / ADR-0011). With no function declaration for them,
Gemini still emitted ``open_app{app_name: ...}`` because the prose told it to;
the tool-use loop found no such tool, and the turn ended with EMPTY text ->
the user heard silence on every action command while plain chit-chat (no
tool) worked.

The invariant is PARITY, in both directions:
- a tool the prompt advertises as callable must exist in ROUTER_TOOLS;
- a tool that stays out of ROUTER_TOOLS must not be advertised as callable.

Update 2026-06-10 (user mandate, ADR-0011 amendment "Inline web search"):
``search-web`` moved from the phantom list INTO ROUTER_TOOLS so the router
answers news/knowledge/research questions inline instead of spawning a
multi-minute worker mission for a single lookup. The parity invariant is
unchanged — search_web is now pinned on the "real tool, must be advertised"
side instead of the phantom side.
"""
from __future__ import annotations

import re

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.brain.router import SYSTEM_PROMPT

# Direct-action tools that must stay OUT of the router (delegated, not dispatched).
_PHANTOM_DIRECT_TOOLS = ("open_app", "remember")
# Their entry-point (hyphen) form, which must also be absent from ROUTER_TOOLS.
_PHANTOM_EP_NAMES = ("open-app", "remember")


def test_phantom_direct_tools_stay_out_of_router_tools() -> None:
    """Architecture contract: pure-dispatcher router must not own these."""
    present = [ep for ep in _PHANTOM_EP_NAMES if ep in ROUTER_TOOLS]
    assert not present, (
        f"{present} are in ROUTER_TOOLS — the router is a pure dispatcher; "
        f"direct-action tools belong behind the Jarvis-Agents bridge (ADR-0011)."
    )


def test_prompt_does_not_advertise_phantom_tools_as_callable() -> None:
    """The prompt must not tell the model to CALL a tool it cannot resolve.

    We forbid the old ``(open_app)`` / ``(remember)`` advertisement pattern
    that made the model emit phantom tool calls.
    """
    for name in _PHANTOM_DIRECT_TOOLS:
        assert f"({name})" not in SYSTEM_PROMPT, (
            f"router prompt still advertises '({name})' as a callable tool — "
            f"the model will emit a phantom call and the turn ends in silence."
        )


def test_prompt_routes_app_actions_to_computer_use() -> None:
    """The app-open / PC-operation path must point at a real ROUTER_TOOL.

    Since the 2026-05-29 Computer-Use amendment the desktop path is the
    dedicated ``computer_use`` tool (the dispatch_to_harness indirection was
    never picked by the model for desktop actions — its description doesn't
    mention the desktop). The prompt must advertise computer_use for PC
    actions and still explicitly forbid the phantom open_app.
    """
    assert "computer-use" in ROUTER_TOOLS
    assert "computer_use" in SYSTEM_PROMPT, (
        "router prompt no longer routes PC actions to computer_use."
    )
    # And it explicitly tells the model NOT to use the phantom open_app.
    assert re.search(r"NICHT\s+open_app", SYSTEM_PROMPT), (  # i18n-allow
        "router prompt should explicitly forbid open_app so the model uses "
        "computer_use for opening apps."
    )


def test_search_web_is_a_real_router_tool_and_advertised() -> None:
    """search_web is a REAL router tool now — and the prompt must say so.

    Both halves of the parity invariant: the entry-point is registered in
    ROUTER_TOOLS (otherwise the prompt advertisement would be a phantom call
    -> silence), and the prompt actually advertises it (otherwise the model
    keeps spawning a worker mission for every news/knowledge question — the
    2026-06-10 user complaint).
    """
    assert "search-web" in ROUTER_TOOLS, (
        "search-web missing from ROUTER_TOOLS — the prompt's research "
        "doctrine would advertise a phantom tool (silence bug class)."
    )
    assert "search_web" in SYSTEM_PROMPT, (
        "router prompt does not advertise search_web — the model has no "
        "visible inline path for news/knowledge questions and will fall "
        "back to spawning a worker mission for a single lookup."
    )
    # The old phantom-era warning ("Es gibt KEIN search_web") must be gone —  # i18n-allow
    # it would directly contradict the real function declaration.
    assert not re.search(r"KEIN\s+search_web", SYSTEM_PROMPT), (  # i18n-allow
        "router prompt still claims search_web does not exist while the "
        "tool IS registered — contradictory instructions."
    )
