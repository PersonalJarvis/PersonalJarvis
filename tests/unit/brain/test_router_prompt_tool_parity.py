"""Regression guard: the router prompt must not advertise tools it cannot call.

Live repro 2026-05-25 (voice "oeffne den Editor"): the router SYSTEM_PROMPT
told the model to use ``open_app`` / ``search_web`` / ``remember`` as direct
tools, but the router is a PURE DISPATCHER — those direct-action tools are
deliberately NOT in ROUTER_TOOLS (Persona-Mandat Phase 3 / ADR-0011: they
belong behind the OpenClaw bridge, not the router). With no function
declaration for them, Gemini still emitted ``open_app{app_name: ...}`` because
the prose told it to; the tool-use loop found no such tool, and the turn ended
with EMPTY text -> the user heard silence on every action command while plain
chit-chat (no tool) worked.

Fix: the prompt no longer advertises those phantom tools; app-opening / PC
operation is routed to ``dispatch_to_harness(harness="computer-use")`` (a real
ROUTER_TOOL). These tests pin both invariants so the class cannot recur.
"""
from __future__ import annotations

import re

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.brain.router import SYSTEM_PROMPT

# Direct-action tools that must stay OUT of the router (delegated, not dispatched).
_PHANTOM_DIRECT_TOOLS = ("open_app", "search_web", "remember")
# Their entry-point (hyphen) form, which must also be absent from ROUTER_TOOLS.
_PHANTOM_EP_NAMES = ("open-app", "search-web", "remember")


def test_phantom_direct_tools_stay_out_of_router_tools() -> None:
    """Architecture contract: pure-dispatcher router must not own these."""
    present = [ep for ep in _PHANTOM_EP_NAMES if ep in ROUTER_TOOLS]
    assert not present, (
        f"{present} are in ROUTER_TOOLS — the router is a pure dispatcher; "
        f"direct-action tools belong behind the OpenClaw bridge (ADR-0011)."
    )


def test_prompt_does_not_advertise_phantom_tools_as_callable() -> None:
    """The prompt must not tell the model to CALL a tool it cannot resolve.

    We forbid the old ``(open_app)`` / ``(search_web)`` / ``(remember)``
    advertisement pattern that made the model emit phantom tool calls.
    """
    for name in _PHANTOM_DIRECT_TOOLS:
        assert f"({name})" not in SYSTEM_PROMPT, (
            f"router prompt still advertises '({name})' as a callable tool — "
            f"the model will emit a phantom call and the turn ends in silence."
        )


def test_prompt_routes_app_actions_to_real_dispatch_tool() -> None:
    """The app-open / PC-operation path must point at a real ROUTER_TOOL."""
    assert "dispatch-to-harness" in ROUTER_TOOLS
    assert "dispatch_to_harness" in SYSTEM_PROMPT, (
        "router prompt no longer routes PC actions to dispatch_to_harness."
    )
    # And it explicitly tells the model NOT to use the phantom open_app.
    assert re.search(r"NICHT\s+open_app", SYSTEM_PROMPT), (
        "router prompt should explicitly forbid open_app so the model uses "
        "dispatch_to_harness for opening apps."
    )
