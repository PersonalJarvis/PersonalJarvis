"""A running pane takes a pick through the CLI's own command, or says why not.

The chat stage's composer shows a pane's model, effort and permission pills;
a pick there is typed into the pane as the CLI's command for it (Claude
Code's ``/effort max``), the pane's own fields follow, and a pick the CLI
has no command for is declined with the sentence the composer shows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry, SessionError
from jarvis.workspace import launch_picks
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    from jarvis.ui.web import agentic_ide_routes

    monkeypatch.setattr(agentic_ide_routes, "get_registry", lambda: reg)
    return reg


@pytest.fixture
def client(registry: Registry) -> TestClient:
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _live_pane(
    registry: Registry, folder: Path, monkeypatch: pytest.MonkeyPatch, agent: str = "claude"
) -> tuple[str, list[str]]:
    """A live pane whose keystroke path records every line typed into it."""
    await registry.start(str(folder), [{"agent": agent}])
    assert registry.session is not None
    term = registry.session.terminals[0]
    await registry.attach(term.name, 100, 30, _noop, _noop_exit)
    typed: list[str] = []

    async def accepted(_term: object, payload: str, *_rest: object) -> bool:
        typed.append(payload)
        return True

    monkeypatch.setattr(registry, "_write_and_confirm", accepted)
    return term.name, typed


def test_claude_code_declares_the_commands_its_binary_takes_while_running() -> None:
    picks = launch_picks.runtime_picks_for("claude")
    assert picks.offers() == {"model": True, "effort": True, "permission_mode": False}
    assert launch_picks.runtime_command("claude", "effort", "max") == "/effort max"
    model = launch_picks.runtime_command("claude", "model", "claude-opus-5")
    assert model == "/model claude-opus-5"
    # No command for the stance: Shift+Tab cycles it, and a cycle is not a pick.
    assert launch_picks.runtime_command("claude", "permission_mode", "plan") == ""
    # A level off the ladder is folded like a launch value — a word the ladder
    # does not know lands on its floor — so the raw string never reaches the PTY.
    assert launch_picks.runtime_command("claude", "effort", "ultra") == "/effort max"
    assert launch_picks.runtime_command("claude", "effort", "rm -rf") == "/effort low"
    assert launch_picks.runtime_command("claude", "model", "rm -rf") == ""


def test_an_entry_that_declares_nothing_offers_nothing_at_runtime() -> None:
    assert launch_picks.runtime_picks_for("codex").offers() == {
        "model": False,
        "effort": False,
        "permission_mode": False,
    }
    assert launch_picks.runtime_command("codex", "effort", "high") == ""
    assert launch_picks.runtime_picks_for("no-such-agent").offers()["effort"] is False


@pytest.mark.asyncio
async def test_a_pick_is_typed_as_the_command_and_the_pane_follows(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, typed = await _live_pane(registry, tmp_path, monkeypatch)
    result = await registry.apply_picks(name, effort="max", permission_mode="plan")
    assert typed == ["/effort max"]
    assert result["applied"] == {"effort": "max"}
    assert "only when it starts" in result["declined"]["permission_mode"]
    term = registry.session.terminals[0]  # type: ignore[union-attr]
    assert term.effort == "max"
    assert term.picked_at["effort"] > 0
    # A command is not a message: nothing lands in the prompt history.
    assert term.prompts_sent == 0
    assert term.prompt_records == []


@pytest.mark.asyncio
async def test_a_pane_that_keeps_the_command_in_its_box_declines_the_pick(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, _typed = await _live_pane(registry, tmp_path, monkeypatch)

    async def refused(*_args: object) -> bool:
        return False

    monkeypatch.setattr(registry, "_write_and_confirm", refused)
    result = await registry.apply_picks(name, effort="low")
    assert result["applied"] == {}
    assert "kept `/effort low`" in result["declined"]["effort"]
    assert registry.session.terminals[0].effort == ""  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_pane_that_is_not_running_raises(registry: Registry, tmp_path: Path) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    name = registry.session.terminals[0].name  # type: ignore[union-attr]
    with pytest.raises(SessionError, match="not running"):
        await registry.apply_picks(name, effort="max")


@pytest.mark.asyncio
async def test_the_route_answers_per_pick_and_refuses_a_pick_that_cannot_be_taken(
    registry: Registry, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, typed = await _live_pane(registry, tmp_path, monkeypatch)
    res = client.post(f"/api/agentic-ide/terminals/{name}/picks", json={"effort": "high"})
    assert res.status_code == 200, res.text
    assert res.json()["applied"] == {"effort": "high"}
    assert typed == ["/effort high"]

    res = client.post(f"/api/agentic-ide/terminals/{name}/picks", json={"permission_mode": "plan"})
    assert res.status_code == 409
    assert "only when it starts" in res.json()["detail"]

    res = client.post(f"/api/agentic-ide/terminals/{name}/picks", json={})
    assert res.status_code == 422

    res = client.post("/api/agentic-ide/terminals/Nobody/picks", json={"effort": "high"})
    assert res.status_code == 404

    # The timeline says which picks the CLI takes while it runs, and reports
    # the pick just taken until the record has written something newer.
    res = client.get(f"/api/agentic-ide/terminals/{name}/timeline")
    assert res.status_code == 200
    body = res.json()
    assert body["runtime_picks"] == {"model": True, "effort": True, "permission_mode": False}
    assert body["effort"] == "high"
