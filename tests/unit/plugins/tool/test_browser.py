"""Contract pins for the browser tool.

Two of these are structural rather than behavioural, and they are the reason
this tool exists at all (docs/plans/autonomous-missions.md C6/C12):

* it must be reachable by the router, otherwise a browser task keeps falling
  back to guessing click points from a screenshot;
* it must NOT be on the mission-worker denylist, because a sub-agent that
  cannot open a web page cannot finish any real-world errand. The desktop
  tools are banned there for a concrete reason — they are the one physical
  pointer and foreground window (AP-5/AP-14) — and CDP shares none of it.

A future edit that "tidies" the browser tool onto the denylist alongside the
other action tools would silently delete autonomous browser work, so it is
pinned here with the rationale attached.
"""
from __future__ import annotations

import pytest

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.missions.workers.worker_tool_broker import _FORBIDDEN_EXACT
from jarvis.plugins.tool.browser import _TIMEOUT_S, BrowserTool


def test_tool_identity_and_risk_tier() -> None:
    assert BrowserTool.name == "browser"
    # Reading and navigating the web is logged, not confirmed — a nag on every
    # page load would make autonomous work impossible.
    assert BrowserTool.risk_tier == "monitor"


def test_router_can_reach_the_browser() -> None:
    assert "browser" in ROUTER_TOOLS


@pytest.mark.parametrize("name", ["browser", "browser-use", "browser_use"])
def test_browser_stays_available_to_mission_workers(name: str) -> None:
    """The whole point of C12: a sub-agent works invisibly, in a browser.

    Computer-Use is denied to workers because it IS the user's mouse; CDP needs
    neither pointer nor focus, so the ban must not be extended to it.
    """
    assert name not in _FORBIDDEN_EXACT


def test_timeout_is_bounded_but_generous() -> None:
    """C1 ("done means done") lives at the mission layer, never inside one call.

    A single script still needs a ceiling so a wedged CDP call surfaces as a
    diagnosable failure instead of holding a mission forever.
    """
    assert 60.0 <= _TIMEOUT_S <= 600.0


@pytest.mark.parametrize(
    "goal",
    [
        "complete the checkout for the flight",
        "buy the ticket",
        "enter the credit card details",
        "den Kauf abschliessen",  # i18n-allow: input vocab
        "jetzt zahlungspflichtig bestellen",  # i18n-allow: input vocab
        "die Kreditkarte eintragen",  # i18n-allow: input vocab
        "comprar el billete",
        "pagar con tarjeta",
    ],
)
def test_purchase_goals_escalate_to_confirmation(goal: str) -> None:
    """C11 — the money brake. Spending stops for a human yes."""
    tool = BrowserTool()
    assert tool.risk_tier_for_args({"goal": goal, "script": "pass"}) == "ask"


@pytest.mark.parametrize(
    "goal",
    [
        "read the flight results for Nice on 12 September",
        "open the inbox and read the confirmation code",
        "die Öffnungszeiten von der Seite lesen",  # i18n-allow: input vocab
        "check whether the page loaded",
    ],
)
def test_ordinary_browsing_is_not_escalated(goal: str) -> None:
    """Hard negative: if routine reading asked for confirmation, autonomy dies."""
    tool = BrowserTool()
    assert tool.risk_tier_for_args({"goal": goal, "script": "print(page_info())"}) is None


def test_purchase_vocabulary_in_the_script_also_escalates() -> None:
    """The goal is model-written prose and can be phrased around; the script
    is what actually runs. Both are read, so a mislabelled goal cannot walk a
    checkout past the brake."""
    tool = BrowserTool()
    args = {"goal": "finish the last step", "script": "click_at_xy(*find('Place order'))"}
    assert tool.risk_tier_for_args(args) == "ask"


@pytest.mark.asyncio
async def test_empty_script_fails_clearly() -> None:
    tool = BrowserTool()
    ctx = ExecutionContext(
        trace_id=__import__("uuid").uuid4(),
        user_utterance="",
        config={},
        memory_read=None,
    )
    result = await tool.execute({"goal": "do something", "script": "  "}, ctx)
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_missing_harness_degrades_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    """§3: an arbitrary downloader without browser-harness gets a usable
    sentence naming the one thing that fixes it — never a blank failure."""
    monkeypatch.setattr("jarvis.plugins.tool.browser._resolve_binary", lambda: None)
    tool = BrowserTool()
    ctx = ExecutionContext(
        trace_id=__import__("uuid").uuid4(),
        user_utterance="",
        config={},
        memory_read=None,
    )
    result = await tool.execute({"goal": "open a page", "script": "print(1)"}, ctx)
    assert result.success is False
    assert result.error is not None
    assert "browser-harness" in result.error
