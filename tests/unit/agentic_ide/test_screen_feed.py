"""`GET /api/agentic-ide/screens` — the office monitors' read-only screen feed.

Pinned here: panes are found by exact key (never a fuzzy call-sign match that
could put another agent's screen on a monitor), unknown panes are omitted, the
caps hold, and reading a screen never touches the pane's geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import screen_feed
from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.transcript import Transcript
from jarvis.ui.web import agentic_ide_routes

# --------------------------------------------------------------------- fakes


@dataclass
class _FakeTerm:
    key: str
    name: str
    transcript: Transcript = field(default_factory=Transcript)
    last_output_at: float | None = None
    started_at: float | None = None


@dataclass
class _FakeSession:
    id: str
    terminals: list[_FakeTerm]


@dataclass
class _FakeRegistry:
    sessions: dict[str, _FakeSession]
    asked: list[str | None] = field(default_factory=list)

    def get(self, workspace_id: str | None) -> _FakeSession | None:
        self.asked.append(workspace_id)
        return self.sessions.get(workspace_id or "")


# ---------------------------------------------------------------- unit tests


def test_parse_drops_garbage_dedups_and_caps() -> None:
    refs = screen_feed.parse_pane_refs(
        ["ws1:t1", "ws1:t1", "nocolon", ":t2", "ws1:", *[f"ws2:t{i}" for i in range(20)]]
    )
    assert refs[0] == ("ws1", "t1")
    assert len(refs) == screen_feed.MAX_PANES
    assert ("ws1", "") not in refs


def test_screen_rows_cursor_and_timestamp() -> None:
    term = _FakeTerm(key="t1", name="T1", last_output_at=1234.5)
    term.transcript.resize(40, 10)
    term.transcript.feed("hello\r\nworld   ")

    shot = screen_feed.pane_screen("ws1", term)

    assert shot["lines"] == ["hello", "world"]
    assert shot["cols"] == 40 and shot["rows"] == 10
    assert shot["cursor"] == [1, 8]
    assert shot["at"] == 1234.5
    assert (shot["workspace_id"], shot["key"], shot["name"]) == ("ws1", "t1", "T1")


def test_caps_keep_the_bottom_rows_and_shift_the_cursor() -> None:
    term = _FakeTerm(key="t1", name="T1")
    term.transcript.resize(300, 80)
    term.transcript.feed("\r\n".join(f"row{i}".ljust(290, "x") for i in range(80)))

    shot = screen_feed.pane_screen("ws1", term)

    assert len(shot["lines"]) == screen_feed.MAX_ROWS
    assert shot["lines"][-1].startswith("row79")
    assert all(len(line) <= screen_feed.MAX_COLS for line in shot["lines"])
    assert shot["cols"] == screen_feed.MAX_COLS and shot["rows"] == screen_feed.MAX_ROWS
    # The cursor sits past column 240 on the last row: no cell to draw it in.
    assert shot["cursor"] is None


def test_reading_never_changes_the_geometry() -> None:
    term = _FakeTerm(key="t1", name="T1")
    term.transcript.resize(50, 12)
    term.transcript.feed("x")
    before = (term.transcript.screen.cols, term.transcript.screen.rows)

    screen_feed.pane_screen("ws1", term)

    assert (term.transcript.screen.cols, term.transcript.screen.rows) == before


def test_collect_matches_exactly_and_omits_unknown() -> None:
    t1 = _FakeTerm(key="t1", name="Frontend")
    t2 = _FakeTerm(key="t2", name="T2")
    registry = _FakeRegistry({"ws1": _FakeSession("ws1", [t1, t2])})

    shots = screen_feed.collect_screens(
        registry,
        [("ws1", "t2"), ("ws1", "frontend"), ("ws1", "terminal two"), ("gone", "t1")],
    )

    assert [(s["key"], s["name"]) for s in shots] == [("t2", "T2"), ("t1", "Frontend")]
    # One lookup per workspace, never the implicit "active" one.
    assert registry.asked == ["ws1", "gone"]


# ---------------------------------------------------------------- route test


@pytest.fixture
def client() -> TestClient:
    ide.reset_registry()
    app = FastAPI()
    app.include_router(agentic_ide_routes.router)
    with TestClient(app) as test_client:
        yield test_client
    ide.reset_registry()


def test_route_serves_the_contract_shape(client, tmp_path) -> None:
    registry = ide.get_registry()
    term = ide.Terminal(key="t1", name="T1", agent="claude", display_name="Claude Code", index=0)
    term.transcript.resize(30, 6)
    term.transcript.feed("$ pytest\r\nok")
    session = ide.Session(
        id="ide_test",
        folder=str(tmp_path),
        name="Test",
        profile=ide.probe_project(tmp_path),
        terminals=[term],
        created_at=0.0,
    )
    registry._sessions[session.id] = session  # noqa: SLF001 - no spawn in a unit test

    body = client.get(
        "/api/agentic-ide/screens", params=[("pane", "ide_test:t1"), ("pane", "ide_test:t9")]
    ).json()

    assert list(body) == ["screens"]
    (shot,) = body["screens"]
    assert set(shot) == {"workspace_id", "key", "name", "cols", "rows", "lines", "cursor", "at"}
    assert shot["lines"] == ["$ pytest", "ok"]
    assert shot["cursor"] == [1, 2]


def test_route_without_panes_is_empty(client) -> None:
    assert client.get("/api/agentic-ide/screens").json() == {"screens": []}
