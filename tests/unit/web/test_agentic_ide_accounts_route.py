"""The endpoint behind the workspace's account switcher.

Route wiring only — which registry call a request makes and what the reply
carries. The behaviour underneath (what a switch does to the next pane, and what
it must NOT do to a running one) is pinned where it lives, in
``tests/unit/agentic_ide/test_account_spawn.py``, against the real registry and
a temporary account store.

The reply is checked for two things a client cannot work without: the label,
because an account id is not something anybody can read back, and the whole
workspace state, so a switch never needs a second round-trip to be visible.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide.session import SessionError
from jarvis.ui.web import agentic_ide_routes as routes


class FakeRegistry:
    """Registry stand-in: records the switch, returns a scripted account."""

    def __init__(self, *, refuse: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._refuse = refuse

    async def set_active_account(self, agent: str, account_id: str):
        if self._refuse:
            raise SessionError(self._refuse)
        self.calls.append((agent, account_id))
        return SimpleNamespace(id=account_id, label="Work seat", platform=agent)

    def active_accounts(self) -> list[dict]:
        return [
            {
                "agent": "claude",
                "display_name": "Claude Code",
                "active_account": "claude:abc123",
                "active_label": "Work seat",
                "account_count": 2,
            }
        ]

    def state(self) -> dict:
        return {"active": True, "accounts": self.active_accounts()}


async def test_the_switch_reaches_the_registry_and_names_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = FakeRegistry()
    monkeypatch.setattr(routes, "get_registry", lambda: registry)

    result = await routes.set_active_account(
        routes.ActiveAccountRequest(agent="claude", account_id="claude:abc123")
    )

    assert registry.calls == [("claude", "claude:abc123")]
    assert result["active_account"] == "claude:abc123"
    assert result["active_label"] == "Work seat"
    # Said in the CLI's display name, not its slug — this sentence is spoken.
    assert result["message"] == "New Claude Code terminals will use Work seat."
    # The whole state comes back, so the open workspace never has to re-read.
    assert result["state"]["accounts"][0]["active_label"] == "Work seat"


async def test_an_account_that_is_gone_answers_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 carrying the sentence to show — never a silent no-op on the old plan."""
    monkeypatch.setattr(
        routes,
        "get_registry",
        lambda: FakeRegistry(refuse="Claude Code has no account with id 'ghost'."),
    )

    with pytest.raises(routes.HTTPException) as caught:
        await routes.set_active_account(
            routes.ActiveAccountRequest(agent="claude", account_id="ghost")
        )

    assert caught.value.status_code == 404
    assert "no account" in caught.value.detail


async def test_reading_the_active_accounts_needs_no_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wizard asks this before anything is open, so it must answer there."""
    monkeypatch.setattr(routes, "get_registry", lambda: FakeRegistry())

    result = await routes.get_accounts()

    assert result["ok"] is True
    assert result["accounts"][0]["display_name"] == "Claude Code"
    assert result["accounts"][0]["account_count"] == 2
