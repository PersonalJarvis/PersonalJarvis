"""The terminal text size, and the one property that matters about it: it stays.

The control itself has existed for a long time. What did not exist was memory —
the size lived in the desktop window's browser storage, and that window is an
embedded WebView started with an empty one on every run, so the panes were back
at the default after each restart. To the person using it that is not "a cache
was cleared", it is "the option is gone".

These tests therefore pin the store rather than the widget: a size that was set
is still the size a fresh process reads, nonsense does not break the workspace,
and the bounds the toolbar enforces are the bounds the backend enforces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import ui_prefs


@pytest.fixture
def client() -> TestClient:
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_a_workspace_that_was_never_touched_reads_the_default() -> None:
    """No file, no error — the default size and an honest "nobody chose this"."""
    assert ui_prefs.terminal_font_size() == ui_prefs.FONT_DEFAULT
    assert ui_prefs.has_terminal_font_size() is False


def test_a_chosen_size_is_still_there_afterwards() -> None:
    """The whole feature: set once, read back the same, without setting it again."""
    assert ui_prefs.set_terminal_font_size(17) == 17

    assert ui_prefs.terminal_font_size() == 17
    assert ui_prefs.has_terminal_font_size() is True


def test_the_stored_size_outlives_the_process_that_set_it(_ui_prefs_in_tmp: Path) -> None:
    """Written to disk, not held in a module-level variable.

    Read back through the file rather than through the module, because an
    in-memory cache would pass a plain round-trip test and still lose the size
    on the restart this feature exists for.
    """
    ui_prefs.set_terminal_font_size(18)

    on_disk = json.loads(_ui_prefs_in_tmp.read_text(encoding="utf-8"))

    assert on_disk["terminal_font_size"] == 18


def test_a_size_nobody_can_read_is_clamped_rather_than_refused() -> None:
    """A CLI typo resizes the terminals; it does not fail the request.

    Both directions, and the returned value always says what was really stored
    so no caller has to guess.
    """
    assert ui_prefs.set_terminal_font_size(400) == ui_prefs.FONT_MAX
    assert ui_prefs.set_terminal_font_size(1) == ui_prefs.FONT_MIN
    assert ui_prefs.set_terminal_font_size("not a number") == ui_prefs.FONT_DEFAULT


def test_a_damaged_preference_file_degrades_to_the_default(_ui_prefs_in_tmp: Path) -> None:
    """A half-written or hand-edited file must not take the workspace down."""
    _ui_prefs_in_tmp.parent.mkdir(parents=True, exist_ok=True)
    _ui_prefs_in_tmp.write_text("{ this is not json", encoding="utf-8")

    assert ui_prefs.terminal_font_size() == ui_prefs.FONT_DEFAULT
    assert ui_prefs.has_terminal_font_size() is False
    # And it heals: the next write replaces the damaged file rather than
    # failing against it forever.
    assert ui_prefs.set_terminal_font_size(15) == 15
    assert ui_prefs.terminal_font_size() == 15


def test_a_preference_written_by_a_newer_version_is_kept(_ui_prefs_in_tmp: Path) -> None:
    """Unknown keys survive a write from an older build.

    Two installs sharing one data directory (a synced home, a rollback) must not
    silently drop each other's preferences.
    """
    _ui_prefs_in_tmp.parent.mkdir(parents=True, exist_ok=True)
    _ui_prefs_in_tmp.write_text(json.dumps({"future_preference": "keep me"}), encoding="utf-8")

    ui_prefs.set_terminal_font_size(14)

    on_disk = json.loads(_ui_prefs_in_tmp.read_text(encoding="utf-8"))
    assert on_disk["future_preference"] == "keep me"
    assert on_disk["terminal_font_size"] == 14


def test_the_route_answers_with_the_size_and_its_bounds(client: TestClient) -> None:
    """The CLI-first half: the toolbar is one client of this, a terminal another."""
    body = client.get("/api/agentic-ide/ui-preferences").json()

    assert body["terminal_font_size"] == ui_prefs.FONT_DEFAULT
    assert body["stored"] is False
    assert (body["min"], body["max"]) == (ui_prefs.FONT_MIN, ui_prefs.FONT_MAX)


def test_the_route_remembers_a_size(client: TestClient) -> None:
    """PUT it, and every later reader — UI, CLI, next boot — sees that size."""
    saved = client.put(
        "/api/agentic-ide/ui-preferences", json={"terminal_font_size": 16}
    ).json()

    assert saved["terminal_font_size"] == 16
    assert saved["stored"] is True
    assert client.get("/api/agentic-ide/ui-preferences").json()["terminal_font_size"] == 16


def test_the_route_clamps_instead_of_erroring(client: TestClient) -> None:
    """An out-of-range size is a 200 with the effective value, not a 422."""
    res = client.put("/api/agentic-ide/ui-preferences", json={"terminal_font_size": 99})

    assert res.status_code == 200
    assert res.json()["terminal_font_size"] == ui_prefs.FONT_MAX


def test_the_toolbar_and_the_backend_agree_on_the_bounds() -> None:
    """Anti-drift: the same three numbers live in Python and in the grid.

    The toolbar clamps what its buttons offer and the backend clamps what it
    stores. If those drift apart, the ``+`` button stops at one number while the
    stored value stops at another, and the size silently changes on the next
    restart — the exact class of bug this store was added to end.
    """
    grid = (
        Path(__file__).resolve().parents[3]
        / "jarvis/ui/web/frontend/src/components/agentic/AgenticGrid.tsx"
    ).read_text(encoding="utf-8")

    assert f"const FONT_MIN = {ui_prefs.FONT_MIN};" in grid
    assert f"const FONT_MAX = {ui_prefs.FONT_MAX};" in grid
    # The grid holds the default as a named constant (it is also where
    # Ctrl/Cmd+0 lands), so the parity check pins that definition rather than
    # the inlined literal it used to be.
    assert f"const FONT_DEFAULT = {ui_prefs.FONT_DEFAULT};" in grid
    assert "storedFontSize() ?? FONT_DEFAULT" in grid
