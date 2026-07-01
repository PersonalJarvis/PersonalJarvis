"""Routing logic — pure, fast, no I/O, no await.

``classify(command)`` returns the RouteKind for one user utterance.
``ack_for(command, route)`` returns the spoken German acknowledgement text.

Classification order (mirrors BrainManager._should_force_openclaw in production):

  1. match_tool(command) returns a DUMB tool  → RouteKind.DUMB_TOOL
  2. match_tool(command) returns a SMART tool → RouteKind.SMART_TOOL
  3. a SMALLTALK_TRIGGERS substring is present AND no ACTION_VERBS substring
                                               → RouteKind.SMALLTALK
  4. any ACTION_VERBS substring present        → RouteKind.SMART_TOOL
     (unknown action command → delegate to worker)
  5. default                                   → RouteKind.SMALLTALK

Performance target: < 150 ms worst-case per call (AD-OE2 latency budget).
The implementation is pure string scanning — no regex compile, no I/O — so it
runs in well under 1 ms in practice.
"""
from __future__ import annotations

from optimistic.events import RouteKind
from optimistic.registry import (
    ACTION_VERBS,
    SMALLTALK_TRIGGERS,
    match_tool,
)

# ---------------------------------------------------------------------------
# Acknowledgement templates — butler-tone, correct German umlauts (ä ö ü ß)
# ---------------------------------------------------------------------------

_ACK_SMART: str = "Geht klar, ich kümmere mich drum."  # i18n-allow: product voice output DE
_ACK_DUMB: str = "Mach ich."  # i18n-allow: product voice output DE
_ACK_SMALLTALK: str = "Mir geht's gut, danke der Nachfrage!"  # i18n-allow: product voice output DE


def classify(command: str) -> RouteKind:
    """Return the RouteKind for *command*.

    Pure, fast, deterministic — no I/O, no await, no side-effects.  Safe to
    call from the voice critical path.

    Parameters
    ----------
    command:
        The raw user utterance (any case, any language mix).

    Returns
    -------
    RouteKind
        DUMB_TOOL, SMART_TOOL, or SMALLTALK.
    """
    low = command.lower()

    # Step 1 + 2: tool registry scan (dumb before smart — AD-OE3)
    tool = match_tool(command)
    if tool is not None:
        return tool.kind  # RouteKind.DUMB_TOOL or RouteKind.SMART_TOOL

    # Step 3: smalltalk allowlist wins if NO action verb is present
    has_smalltalk = any(trigger in low for trigger in SMALLTALK_TRIGGERS)
    has_action = any(verb in low for verb in ACTION_VERBS)

    if has_smalltalk and not has_action:
        return RouteKind.SMALLTALK

    # Step 4: unknown action command → delegate to heavy worker
    if has_action:
        return RouteKind.SMART_TOOL

    # Step 5: default — nothing matched, treat as friendly smalltalk
    return RouteKind.SMALLTALK


def ack_for(command: str, route: RouteKind) -> str:  # noqa: ARG001
    """Return the German spoken acknowledgement for *route*.

    Always returns a non-empty string.  The *command* parameter is accepted for
    future personalisation (e.g. echoing the action back) but is unused in this
    prototype implementation.

    Parameters
    ----------
    command:
        The user utterance (unused in this prototype; present for API parity).
    route:
        The RouteKind returned by ``classify(command)``.

    Returns
    -------
    str
        A non-empty German butler-tone acknowledgement.
    """
    if route == RouteKind.SMART_TOOL:
        return _ACK_SMART
    if route == RouteKind.DUMB_TOOL:
        return _ACK_DUMB
    # RouteKind.SMALLTALK (and any future additions — exhaustive default)
    return _ACK_SMALLTALK
