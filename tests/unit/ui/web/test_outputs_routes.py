"""REST-route tests for the Outputs view (`/api/outputs`).

Focus: H1 bug — `_mission_status_lookup` previously used the names of
``tasks/<task_id>/`` subdirectories as the ``WHERE id LIKE 'prefix%'``
input. Those names are ``Step.task_id`` UUIDs (a fresh UUIDv7 minted per
step by the Kontrollierer), not the ``mission_id`` the
Mission-Manager persists in ``missions.id``. The lookup therefore never
matched and every ``mission_<short>/`` directory rendered as
``status="unknown"`` in the UI.

The fix derives the mission-id prefix from the dir-name itself
(``mission_<mission_id[:13]>``). These tests cover both regression
guards: that ``mission_<short>`` dirs resolve to the right status from
the DB, and that worktree slug-style dirs stay un-enriched (their
embedded random ``hex[:8]`` is decoupled from any mission UUID by
design).
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.outputs_routes import (
    _is_deliverable_relpath,
    _mission_id_prefix_for_dir,
    _parse_slug,
)
from jarvis.ui.web.outputs_routes import (
    router as outputs_router,
)


@pytest.fixture(autouse=True)
def _reset_openers_cache_between_tests():
    """The opener list is memoized per process; clear it around every test so
    the editor-detection cases don't see each other's cached result."""
    from jarvis.ui.web import outputs_routes

    outputs_routes._reset_openers_cache()
    yield
    outputs_routes._reset_openers_cache()


# --- Stubs -------------------------------------------------------------------


class _StubStore:
    """Mimics ``MissionEventStore`` enough for ``_mission_status_lookup``.

    The route only touches ``mgr.store.conn.execute(...)`` so we only need
    a connection with a missions table populated to fake-DB shape.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn


class _StubManager:
    def __init__(self, store: _StubStore) -> None:
        self.store = store


@pytest_asyncio.fixture
async def db_conn() -> AsyncIterator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    await conn.execute(
        "CREATE TABLE missions ("
        "id TEXT PRIMARY KEY, prompt TEXT NOT NULL, state TEXT NOT NULL, "
        "language TEXT NOT NULL DEFAULT 'de', created_ms INTEGER NOT NULL, "
        "updated_ms INTEGER NOT NULL, iteration INTEGER NOT NULL DEFAULT 0, "
        "cost_usd REAL NOT NULL DEFAULT 0.0)"
    )
    # The continuation-link lookup reads MissionDispatched events; mirror the
    # subset of the real `mission_events` schema the route helper touches.
    await conn.execute(
        "CREATE TABLE mission_events ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL, "
        "event_type TEXT NOT NULL, payload_json TEXT NOT NULL)"
    )
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def app(tmp_path: Path, db_conn: aiosqlite.Connection) -> FastAPI:
    app = FastAPI()
    app.include_router(outputs_router)
    app.state.outputs_root = tmp_path
    app.state.mission_manager = _StubManager(_StubStore(db_conn))
    return app


# --- Helpers -----------------------------------------------------------------


def _make_mission_dir(root: Path, mission_id: str) -> Path:
    """Create a ``mission_<mission_id[:13]>`` dir as the Kontrollierer does."""
    d = root / f"mission_{mission_id[:13]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_worktree_dir(
    root: Path, utterance: str = "test-task", short: str | None = None
) -> Path:
    """Create a worktree slug-style dir as ``WorktreeManager.create`` does."""
    short = short or "deadbeef"
    ts = time.strftime("%Y%m%dT%H%M%S")
    d = root / f"{ts}__{utterance}__{short}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _seed(path: Path, content: str = "x") -> Path:
    """Write ``content`` to ``path``, creating parent dirs (``mkdir -p``).

    ``Path.write_text`` does NOT create missing parents — it raises
    ``FileNotFoundError``. The artifact-filter tests lay down deeply nested
    mission scaffolding (``tasks/<id>/artifacts/files/...``, ``claude_config/
    projects/...``), so they all go through this helper.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


async def _insert_mission(
    conn: aiosqlite.Connection,
    *,
    mission_id: str,
    state: str,
    prompt: str = "test prompt",
    created_ms: int | None = None,
    updated_ms: int | None = None,
) -> None:
    created_ms = created_ms or int(time.time() * 1000) - 5000
    updated_ms = updated_ms or int(time.time() * 1000)
    await conn.execute(
        "INSERT INTO missions (id, prompt, state, created_ms, updated_ms) "
        "VALUES (?, ?, ?, ?, ?)",
        (mission_id, prompt, state, created_ms, updated_ms),
    )


async def _insert_dispatch_event(
    conn: aiosqlite.Connection,
    *,
    child_id: str,
    parent_id: str,
) -> None:
    """Record the child's ``MissionDispatched`` event carrying its parent link.

    Mirrors what ``mgr.dispatch(parent_mission_id=...)`` persists on a re-run:
    the parent→child edge lives only in the dispatch event payload (the
    ``missions`` header has no parent column).
    """
    import json

    payload = json.dumps(
        {
            "event_type": "MissionDispatched",
            "prompt": "task",
            "parent_mission_id": parent_id,
            "priority": 0,
            "language": "en",
        }
    )
    await conn.execute(
        "INSERT INTO mission_events (mission_id, event_type, payload_json) "
        "VALUES (?, ?, ?)",
        (child_id, "MissionDispatched", payload),
    )


# --- _parse_slug + _mission_id_prefix_for_dir unit tests ---------------------


def test_parse_slug_mission_dir() -> None:
    parsed = _parse_slug("mission_019e3600-a84e")
    assert parsed["short"] == "019e3600-a84e"
    assert parsed["utterance"] is None
    assert parsed["started_at"] is None


def test_parse_slug_worktree_dir() -> None:
    parsed = _parse_slug("20260518T120000__refactor-router__deadbeef")
    assert parsed["short"] == "deadbeef"
    assert parsed["utterance"] == "Refactor router"
    assert parsed["started_at"] is not None


def test_parse_slug_unknown_returns_nones() -> None:
    parsed = _parse_slug("random-dir-name")
    assert parsed == {"started_at": None, "utterance": None, "short": None}


def test_mission_id_prefix_for_persistent_dir() -> None:
    """The mission_<short> dir-name yields its short as the LIKE-prefix."""
    assert (
        _mission_id_prefix_for_dir("mission_019e3600-a84e") == "019e3600-a84e"
    )


def test_mission_id_prefix_for_worktree_dir_is_none() -> None:
    """Worktree slug dirs embed a random hex[:8] — no mission-id mapping."""
    assert (
        _mission_id_prefix_for_dir(
            "20260518T120000__refactor-router__deadbeef"
        )
        is None
    )


def test_mission_id_prefix_for_random_dir_is_none() -> None:
    assert _mission_id_prefix_for_dir("unrelated-folder") is None


def test_mission_id_prefix_rejects_too_short_prefix() -> None:
    """Six char floor matches the SQL LIKE guard in _mission_status_lookup."""
    assert _mission_id_prefix_for_dir("mission_abcde") is None  # 5 chars
    assert _mission_id_prefix_for_dir("mission_abcdef") == "abcdef"  # 6 OK


# --- /api/outputs route tests ------------------------------------------------


@pytest.mark.asyncio
async def test_list_outputs_empty_root(app: FastAPI) -> None:
    with TestClient(app) as client:
        r = client.get("/api/outputs")
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


@pytest.mark.asyncio
async def test_list_outputs_mission_dir_resolves_status_from_db(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """H1 regression: mission_<short>/ dirs must NOT render as unknown."""
    mission_id = "019e3600-a84e-7000-8000-000000000001"
    _make_mission_dir(tmp_path, mission_id)
    await _insert_mission(db_conn, mission_id=mission_id, state="APPROVED")

    with TestClient(app) as client:
        r = client.get("/api/outputs")

    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    sess = sessions[0]
    assert sess["slug"] == f"mission_{mission_id[:13]}"
    assert sess["status"] == "success", (
        f"H1 regression: mission_<short>/ dir should resolve to its DB "
        f"row (APPROVED→success), got status={sess['status']!r}"
    )
    assert sess["utterance"] == "test prompt"


@pytest.mark.asyncio
async def test_list_outputs_mission_dir_running_state(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    mission_id = "019e3600-b000-7000-8000-000000000002"
    _make_mission_dir(tmp_path, mission_id)
    await _insert_mission(db_conn, mission_id=mission_id, state="RUNNING")

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = r.json()["sessions"]
    assert sessions[0]["status"] == "running"


@pytest.mark.asyncio
async def test_list_outputs_mission_dir_failed_state(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    mission_id = "019e3600-c000-7000-8000-000000000003"
    _make_mission_dir(tmp_path, mission_id)
    await _insert_mission(db_conn, mission_id=mission_id, state="FAILED")

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = r.json()["sessions"]
    assert sessions[0]["status"] == "error"


@pytest.mark.asyncio
async def test_list_outputs_running_card_carries_mission_id(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """The card must expose the full mission id so the UI can POST
    ``/api/missions/{id}/cancel`` for the hold-to-abort button."""
    mission_id = "019e3600-f000-7000-8000-000000000042"
    _make_mission_dir(tmp_path, mission_id)
    await _insert_mission(db_conn, mission_id=mission_id, state="RUNNING")

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = r.json()["sessions"]
    assert sessions[0]["mission_id"] == mission_id


@pytest.mark.asyncio
async def test_list_outputs_unenriched_card_has_null_mission_id(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """A mission dir without a DB row still renders — with mission_id None
    (the UI then simply offers no abort affordance)."""
    _make_mission_dir(tmp_path, "019e3600-f200-7000-8000-000000000044")

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["mission_id"] is None


@pytest.mark.asyncio
async def test_list_outputs_cancelled_maps_to_cancelled_status(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """CANCELLED is a deliberate user action, not a failure — the card
    gets its own badge instead of lying with ``error``."""
    mission_id = "019e3600-f100-7000-8000-000000000043"
    _make_mission_dir(tmp_path, mission_id)
    await _insert_mission(db_conn, mission_id=mission_id, state="CANCELLED")

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = r.json()["sessions"]
    assert sessions[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_list_outputs_cancelled_with_live_child_exposes_continuation(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """A CANCELLED card whose re-run child is still running must say so.

    Forensic 2026-06-28 (missions 019f0fa6 → 019f0fac): the user clicked
    "Continue" on a cancelled mission; that spawned a linked child which ran
    on (CRITIQUING), but the cancelled card kept showing a "Continue" button
    with no hint that its work was already live — two visually identical
    cards, one cancelled + continuable, one running. The list now resolves the
    live continuation (parent_mission_id in the child's MissionDispatched
    event) and exposes it so the UI can replace "Continue" with a "running"
    indicator pointing at the child.
    """
    parent = "019f0fa6-4ff6-7bd9-87fd-a32e201b32c9"
    child = "019f0fac-26a3-7c59-bfa3-385fb1e426fc"
    _make_mission_dir(tmp_path, parent)
    _make_mission_dir(tmp_path, child)
    await _insert_mission(db_conn, mission_id=parent, state="CANCELLED")
    await _insert_mission(db_conn, mission_id=child, state="CRITIQUING")
    await _insert_dispatch_event(db_conn, child_id=child, parent_id=parent)

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = {s["slug"]: s for s in r.json()["sessions"]}

    psum = sessions[f"mission_{parent[:13]}"]
    assert psum["status"] == "cancelled"
    assert psum["active_child_id"] == child, (
        "cancelled card must expose its live re-run child so the UI can stop "
        f"lying with a 'Continue' button, got {psum.get('active_child_id')!r}"
    )
    assert psum["active_child_slug"] == f"mission_{child[:13]}"

    # The live child itself is just RUNNING — it has no continuation of its own.
    csum = sessions[f"mission_{child[:13]}"]
    assert csum["status"] == "running"
    assert csum["active_child_id"] is None


@pytest.mark.asyncio
async def test_list_outputs_cancelled_with_terminal_child_keeps_continue(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """A re-run child that has itself finished must NOT suppress Continue.

    The guard is a *liveness* signal, not a permanent lock: once the child
    reaches a terminal state the parent is freely re-runnable again, so the
    list must report no active continuation (active_child_id stays None) and
    the UI shows "Continue" once more.
    """
    parent = "019f0fb0-1111-7000-8000-000000000001"
    child = "019f0fb1-2222-7000-8000-000000000002"
    _make_mission_dir(tmp_path, parent)
    _make_mission_dir(tmp_path, child)
    await _insert_mission(db_conn, mission_id=parent, state="CANCELLED")
    await _insert_mission(db_conn, mission_id=child, state="FAILED")
    await _insert_dispatch_event(db_conn, child_id=child, parent_id=parent)

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = {s["slug"]: s for s in r.json()["sessions"]}
    assert sessions[f"mission_{parent[:13]}"]["active_child_id"] is None


@pytest.mark.asyncio
async def test_list_outputs_running_mission_duration_ticks_from_created(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """A RUNNING mission shows now-minus-created, not updated-minus-created.

    Live bug (mission 019eae15-5a31, 2026-06-09): right after dispatch
    created_ms == updated_ms, so the card rendered "RUNNING 0.0s" and
    froze there until the next mission event (potentially 20 minutes
    later). For non-terminal states the duration must be wall-clock
    elapsed since created_ms — the frontend polls every 3 s, so the
    badge ticks automatically.
    """
    mission_id = "019e3600-d000-7000-8000-000000000004"
    _make_mission_dir(tmp_path, mission_id)
    now_ms = int(time.time() * 1000)
    await _insert_mission(
        db_conn,
        mission_id=mission_id,
        state="RUNNING",
        created_ms=now_ms - 120_000,
        updated_ms=now_ms - 120_000,  # fresh dispatch: no events yet
    )

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sess = r.json()["sessions"][0]
    assert sess["duration_s"] is not None
    assert sess["duration_s"] >= 110.0, (
        f"running mission must tick from created_ms, got {sess['duration_s']}"
    )
    # A still-running mission has no completion timestamp.
    assert sess["completed_at"] is None


@pytest.mark.asyncio
async def test_list_outputs_terminal_mission_duration_frozen(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """Terminal missions keep updated-minus-created (frozen, not ticking)."""
    mission_id = "019e3600-e000-7000-8000-000000000005"
    _make_mission_dir(tmp_path, mission_id)
    now_ms = int(time.time() * 1000)
    await _insert_mission(
        db_conn,
        mission_id=mission_id,
        state="FAILED",
        created_ms=now_ms - 300_000,
        updated_ms=now_ms - 200_000,
    )

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sess = r.json()["sessions"][0]
    assert sess["duration_s"] == pytest.approx(100.0, abs=5.0)
    assert sess["completed_at"] == pytest.approx(
        (now_ms - 200_000) / 1000.0, abs=5.0
    )


@pytest.mark.asyncio
async def test_list_outputs_mission_dir_no_db_row_falls_back_to_unknown(
    app: FastAPI, tmp_path: Path
) -> None:
    """When the dir exists but no DB row matches the prefix, status is unknown."""
    mission_id = "019e9999-d000-7000-8000-000000000004"
    _make_mission_dir(tmp_path, mission_id)

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["status"] == "unknown"


@pytest.mark.asyncio
async def test_list_outputs_hides_worktree_slug_dirs(
    app: FastAPI, tmp_path: Path
) -> None:
    """Worktree slug dirs (``<ts>__<utterance>__<short>``) are temporary scaffolding
    created and torn down by ``WorktreeManager``. They have no mission-id mapping
    so their status would always render as ``unknown`` — together with the
    canonical ``mission_<short>`` dir for the same task this produced TWO cards
    per single mission (live regression 2026-05-26: user perceived "two
    sub-agents for one task"). The Outputs list now hides them so each mission
    is represented by exactly one card. Power users can still address the
    worktree dir directly via ``/api/outputs/{slug}/artifacts``.
    """
    _make_worktree_dir(tmp_path, utterance="test-fix", short="cafebabe")

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = r.json()["sessions"]
    assert sessions == [], (
        f"worktree slug dir must not appear in the Outputs list, got {sessions!r}"
    )


@pytest.mark.asyncio
async def test_list_outputs_worktree_dir_random_short_does_not_collide(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """Worktree short must not accidentally LIKE-match a real mission_id.

    Regression guard: if we ever revert to "use the dir's parsed short as
    a LIKE-prefix", a worktree dir whose random hex[:8] happens to be a
    UUID prefix could resolve to the wrong mission. After the 2026-05-26
    dedup change worktree-only dirs are hidden from the list — but the
    regression guard is preserved: even if a worktree slug's random short
    collides with a real mission's prefix, the worktree must NOT inherit
    that mission's row. Concretely the list stays empty (the worktree dir
    is hidden) instead of falsely-resolved.
    """
    mission_id = "deadbeef-0000-7000-8000-000000000005"
    _make_worktree_dir(tmp_path, short="deadbeef")
    await _insert_mission(db_conn, mission_id=mission_id, state="APPROVED")

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = r.json()["sessions"]
    assert sessions == [], (
        "worktree slug must remain hidden even when its random short collides "
        f"with a real mission_id prefix, got {sessions!r}"
    )


@pytest.mark.asyncio
async def test_list_outputs_multiple_mission_dirs(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """Each mission_<short>/ dir resolves independently to its own DB row."""
    mid_a = "019e3600-aaaa-7000-8000-000000000010"
    mid_b = "019e3600-bbbb-7000-8000-000000000011"
    _make_mission_dir(tmp_path, mid_a)
    _make_mission_dir(tmp_path, mid_b)
    await _insert_mission(
        db_conn, mission_id=mid_a, state="APPROVED", prompt="task A"
    )
    await _insert_mission(
        db_conn, mission_id=mid_b, state="FAILED", prompt="task B"
    )

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = {s["slug"]: s for s in r.json()["sessions"]}
    assert sessions[f"mission_{mid_a[:13]}"]["status"] == "success"
    assert sessions[f"mission_{mid_a[:13]}"]["utterance"] == "task A"
    assert sessions[f"mission_{mid_b[:13]}"]["status"] == "error"
    assert sessions[f"mission_{mid_b[:13]}"]["utterance"] == "task B"


@pytest.mark.asyncio
async def test_list_outputs_mission_dir_ignores_task_subdir_names(
    app: FastAPI, tmp_path: Path, db_conn: aiosqlite.Connection
) -> None:
    """H1 root cause: the lookup must NOT use tasks/<task_id>/ subdirs.

    Pre-fix, the route walked ``mission_<short>/tasks/<task_id>/`` and
    used the task_id (a FRESH UUIDv7, disjoint from mission_id) as the
    DB-prefix. If the fix regressed, this test would either render the
    status as unknown (good — no false match) OR render the status of a
    *different* mission whose UUID happens to share the task_id prefix.
    By inserting a row whose ``id`` matches the task_id prefix but NOT
    the mission-dir prefix, we prove the lookup uses the dir-name only.
    """
    mission_id = "019e3600-d000-7000-8000-000000000020"
    mission_dir = _make_mission_dir(tmp_path, mission_id)
    # Mimic the Kontrollierer task tree: ``tasks/<task_id[:13]>/``. The
    # task_id is intentionally a completely different UUID prefix.
    task_id_prefix = "feedface-cafe"
    (mission_dir / "tasks" / task_id_prefix).mkdir(parents=True)
    # Insert a row keyed off the *task_id* prefix to bait the old lookup.
    bait_full_id = f"{task_id_prefix}-7000-8000-000000000099"
    await _insert_mission(
        db_conn,
        mission_id=bait_full_id,
        state="FAILED",
        prompt="WRONG ROW — should not appear",
    )
    # And the correct mission row.
    await _insert_mission(
        db_conn, mission_id=mission_id, state="APPROVED", prompt="correct row"
    )

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    sess = sessions[0]
    assert sess["status"] == "success", (
        "H1 regression: lookup picked up the task_id-prefixed row "
        "(state=FAILED) instead of the mission-dir's own mission_id row"
    )
    assert sess["utterance"] == "correct row"


@pytest.mark.asyncio
async def test_list_outputs_without_mission_manager_stays_functional(
    tmp_path: Path,
) -> None:
    """When Phase-6 is not booted (no mission_manager), the view still works."""
    app = FastAPI()
    app.include_router(outputs_router)
    app.state.outputs_root = tmp_path
    # NB: no mission_manager set
    _make_mission_dir(tmp_path, "019e3600-aaaa-7000-8000-000000000030")

    with TestClient(app) as client:
        r = client.get("/api/outputs")
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["status"] == "unknown"


# --- Plan endpoint stub (UI-MED: Plan-UI-Stub) -------------------------------


@pytest.mark.asyncio
async def test_get_output_plan_returns_empty_stub(
    app: FastAPI, tmp_path: Path
) -> None:
    """The Plan tab is intentionally a placeholder (Welle-4 not plumbed).

    Confirmed as out-of-scope per the B1 brief: returns ``{plan: null,
    steps: []}`` rather than 404. The UI renders "Single-Shot-Run — kein
    strukturierter Plan" for this shape.
    """
    _make_mission_dir(tmp_path, "019e3600-eeee-7000-8000-000000000040")
    with TestClient(app) as client:
        r = client.get(
            "/api/outputs/mission_019e3600-eeee/plan"
        )
    assert r.status_code == 200
    assert r.json() == {"plan": None, "steps": []}


@pytest.mark.asyncio
async def test_get_output_plan_404_for_unknown_slug(app: FastAPI) -> None:
    with TestClient(app) as client:
        r = client.get("/api/outputs/nonexistent-slug/plan")
    assert r.status_code == 404


# --- /artifacts deliverable-only filter (live report 2026-05-30) -------------
# A trivial "make an HTML file" mission rendered 10+ ``claude_config/*`` rows
# (sessions, policy-limits, settings, projects/*.jsonl, .claude.json, backups)
# plus the forensic ``diff.patch`` in the Outputs "Results" list — burying the
# one file the user actually wanted (HelloBot.html). The list endpoint now
# surfaces ONLY genuine deliverables under ``tasks/<id>/artifacts/files/`` —
# the exact subtree ``deliver_to_user_folder`` mirrors to the user's Downloads.


def test_is_deliverable_relpath() -> None:
    """The allowlist predicate: only ``tasks/<id>/artifacts/files/<rel>`` wins."""
    assert _is_deliverable_relpath(
        ("tasks", "019e0000", "artifacts", "files", "out.html")
    )
    assert _is_deliverable_relpath(
        ("tasks", "019e0000", "artifacts", "files", "sub", "deep.css")
    )
    # Forensic + scaffolding paths are all rejected.
    assert not _is_deliverable_relpath(
        ("tasks", "019e0000", "artifacts", "diff.patch")
    )
    assert not _is_deliverable_relpath(("claude_config", "settings.json"))
    assert not _is_deliverable_relpath(
        ("claude_config", "sessions", "45552.json")
    )
    assert not _is_deliverable_relpath((".codex", "auth.json"))
    assert not _is_deliverable_relpath(("openclaw_state", "openclaw.json"))
    assert not _is_deliverable_relpath(("reflections.md",))
    assert not _is_deliverable_relpath(("tasks", "019e0000", "logs", "x.jsonl"))


def test_is_deliverable_relpath_excludes_browser_scratch() -> None:
    """Defence-in-depth (2026-06-21, mission_019eeb34-bb67): a browser/QA
    worker's gitignored Chrome user-data profiles re-imported by the archive's
    --ignored union must NOT show in Outputs even though they live under the
    allowlisted ``tasks/<id>/artifacts/files/`` subtree. The real deliverable
    inside the same qa-artifacts/ dir must still pass."""
    base = ("tasks", "019eeb34-bc50", "artifacts", "files")
    # Browser scratch — rejected.
    assert not _is_deliverable_relpath(
        (*base, "qa-artifacts", "chrome-profile-dd6355b8", "GrShaderCache", "data_2")
    )
    assert not _is_deliverable_relpath(
        (*base, "qa-artifacts", "chrome-profile-dd6355b8", "Last Browser")
    )
    assert not _is_deliverable_relpath(
        (*base, "qa-artifacts", "chrome-profile-dd6355b8", "Default",
         "Shared Dictionary", "db-journal")
    )
    # Genuine deliverables — including one INSIDE qa-artifacts/ next to junk.
    assert _is_deliverable_relpath((*base, "index.html"))
    assert _is_deliverable_relpath((*base, "qa-artifacts", "melbourne-plan-render.png"))


@pytest.mark.asyncio
async def test_list_artifacts_lists_only_deliverables(
    app: FastAPI, tmp_path: Path
) -> None:
    """Only genuine deliverables are listed; the forensic ``diff.patch`` one
    level up in ``artifacts/`` is excluded (contract change 2026-05-30)."""
    d = _make_mission_dir(tmp_path, "019e3288abcd")
    _seed(d / "tasks" / "019e0000" / "artifacts" / "files" / "out.txt", "hello")
    _seed(d / "tasks" / "019e0000" / "artifacts" / "diff.patch", "diff --git")
    with TestClient(app) as client:
        r = client.get(f"/api/outputs/{d.name}/artifacts")
    assert r.status_code == 200
    paths = {f["path"] for f in r.json()["files"]}
    assert "tasks/019e0000/artifacts/files/out.txt" in paths
    assert "tasks/019e0000/artifacts/diff.patch" not in paths


@pytest.mark.asyncio
async def test_list_artifacts_skips_claude_config_scaffolding(
    app: FastAPI, tmp_path: Path
) -> None:
    """The isolated ``CLAUDE_CONFIG_DIR`` (``run_dir/claude_config/``) seeded by
    ``build_worker_env`` must never leak into the Outputs view."""
    d = _make_mission_dir(tmp_path, "019e3288abcd")
    cfg = d / "claude_config"
    _seed(cfg / "sessions" / "45552.json", "{}")
    _seed(cfg / "projects" / "x" / "a.jsonl", "{}\n")
    _seed(cfg / "backups" / ".claude.json.backup.123", "{}")
    _seed(cfg / "settings.json", "{}")
    _seed(cfg / "policy-limits.json", "{}")
    _seed(cfg / ".claude.json", "{}")
    _seed(cfg / ".last-cleanup", "x")
    _seed(
        d / "tasks" / "019e0000" / "artifacts" / "files" / "HelloBot.html",
        "<html></html>",
    )
    with TestClient(app) as client:
        r = client.get(f"/api/outputs/{d.name}/artifacts")
    assert r.status_code == 200
    paths = {f["path"] for f in r.json()["files"]}
    assert paths == {"tasks/019e0000/artifacts/files/HelloBot.html"}, (
        "only the genuine deliverable may show; all claude_config rows must be "
        f"filtered. Got: {sorted(paths)}"
    )


@pytest.mark.asyncio
async def test_list_artifacts_skips_codex_and_forensics(
    app: FastAPI, tmp_path: Path
) -> None:
    """``.codex/`` (CODEX_HOME), worker ``logs/`` and ``reflections.md`` are
    internal scaffolding — only the deliverable survives the listing filter."""
    d = _make_mission_dir(tmp_path, "019e3288abcd")
    _seed(d / ".codex" / "cache" / "auth.json", "{}")
    _seed(d / "tasks" / "019e0000" / "logs" / "stream.jsonl", "{}\n")
    _seed(d / "reflections.md", "notes")
    _seed(
        d / "tasks" / "019e0000" / "artifacts" / "files" / "site.html",
        "<html></html>",
    )
    with TestClient(app) as client:
        r = client.get(f"/api/outputs/{d.name}/artifacts")
    assert r.status_code == 200
    paths = {f["path"] for f in r.json()["files"]}
    assert paths == {"tasks/019e0000/artifacts/files/site.html"}, sorted(paths)


@pytest.mark.asyncio
async def test_list_artifacts_lists_nested_deliverables(
    app: FastAPI, tmp_path: Path
) -> None:
    """Deliverables in nested sub-dirs under ``artifacts/files/`` are kept."""
    d = _make_mission_dir(tmp_path, "019e3288abcd")
    _seed(
        d / "tasks" / "019e0000" / "artifacts" / "files" / "assets" / "logo.svg",
        "<svg/>",
    )
    with TestClient(app) as client:
        r = client.get(f"/api/outputs/{d.name}/artifacts")
    assert r.status_code == 200
    paths = {f["path"] for f in r.json()["files"]}
    assert "tasks/019e0000/artifacts/files/assets/logo.svg" in paths


@pytest.mark.asyncio
async def test_raw_serves_deliverable(app: FastAPI, tmp_path: Path) -> None:
    """The raw-file endpoint returns the contents of a genuine deliverable."""
    d = _make_mission_dir(tmp_path, "019e3288abcd")
    _seed(
        d / "tasks" / "019e0000" / "artifacts" / "files" / "out.txt", "payload"
    )
    with TestClient(app) as client:
        r = client.get(
            f"/api/outputs/{d.name}/files/"
            "tasks/019e0000/artifacts/files/out.txt/raw"
        )
    assert r.status_code == 200
    assert r.json()["text"] == "payload"


@pytest.mark.asyncio
async def test_raw_404s_for_scaffolding(app: FastAPI, tmp_path: Path) -> None:
    """Defense-in-depth: a direct raw-fetch of an internal claude_config file
    must 404 even though the file exists on disk — the same allowlist guards
    the raw endpoint, so scaffolding can be neither listed nor fetched."""
    d = _make_mission_dir(tmp_path, "019e3288abcd")
    _seed(d / "claude_config" / ".claude.json", '{"secret":"x"}')
    with TestClient(app) as client:
        r = client.get(
            f"/api/outputs/{d.name}/files/claude_config/.claude.json/raw"
        )
    assert r.status_code == 404


# --- /download route (Task 3) -------------------------------------------------


def _make_deliverable(root: Path, mission_id: str, name: str, content: str) -> str:
    """Create tasks/<tid>/artifacts/files/<name> under mission_<id>; return rel path."""
    files_dir = (
        root / f"mission_{mission_id[:13]}" / "tasks" / "019edeadbeef"
        / "artifacts" / "files"
    )
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / name).write_text(content, encoding="utf-8")
    return f"tasks/019edeadbeef/artifacts/files/{name}"


def test_download_sets_attachment_disposition(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/download")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment")
    assert "report.md" in cd
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.text == "# Hi"


def test_download_inline_disposition(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "page.html", "<p>x</p>")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/download?disposition=inline")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("inline")


def test_download_blocks_non_deliverable(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "reflections.md").write_text("secret", encoding="utf-8")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/reflections.md/download")
    assert r.status_code == 404


def test_download_blocks_path_traversal(app):
    slug = "mission_019ed2dfd0fab"
    (Path(app.state.outputs_root) / slug).mkdir(parents=True, exist_ok=True)
    client = TestClient(app)
    r = client.get(
        f"/api/outputs/{slug}/files/tasks/x/artifacts/files/..%2f..%2f..%2fsecret/download"
    )
    assert r.status_code == 404


def test_view_renders_markdown_with_csp(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Heading\n\nbody")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/view")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<h1>Heading</h1>" in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_view_escapes_plain_text(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "x.txt", "<script>bad</script>")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/view")
    assert r.status_code == 200
    assert "&lt;script&gt;" in r.text
    assert "<script>bad</script>" not in r.text


def test_capabilities_reports_flag_true(app):
    app.state.native_file_actions = True
    client = TestClient(app)
    r = client.get("/api/outputs/capabilities")
    assert r.status_code == 200
    data = r.json()
    assert data["native_file_actions"] is True
    assert data["platform"] in ("win32", "darwin", "linux")


def test_capabilities_defaults_false_when_unset(app):
    # The fixture app never sets the flag — must default to False (VPS-safe).
    client = TestClient(app)
    r = client.get("/api/outputs/capabilities")
    assert r.status_code == 200
    assert r.json()["native_file_actions"] is False


# --- /reveal and /open-native routes (Task 6) --------------------------------


def test_reveal_404_when_native_disabled(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    app.state.native_file_actions = False
    client = TestClient(app)
    r = client.post(f"/api/outputs/{slug}/files/{rel}/reveal")
    assert r.status_code == 404


def test_reveal_calls_platform_when_enabled(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    app.state.native_file_actions = True
    client = TestClient(app)
    with patch("jarvis.platform.open_path.reveal_in_folder", return_value=True) as rev:
        r = client.post(f"/api/outputs/{slug}/files/{rel}/reveal")
    assert r.status_code == 200
    assert r.json()["opened"] is True
    rev.assert_called_once()


def test_open_native_calls_platform_when_enabled(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    app.state.native_file_actions = True
    client = TestClient(app)
    with patch("jarvis.platform.open_path.open_file", return_value=True) as opn:
        r = client.post(f"/api/outputs/{slug}/files/{rel}/open-native")
    assert r.status_code == 200
    assert r.json()["opened"] is True
    opn.assert_called_once()


def test_download_inline_html_has_csp(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "page.html", "<script>bad</script>")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/download?disposition=inline")
    assert r.status_code == 200
    assert "default-src 'none'" in r.headers.get("content-security-policy", "")


def test_download_attachment_html_no_csp_needed(app):
    # Attachment (download to disk) is not rendered, so no CSP is required.
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "page2.html", "<b>x</b>")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/download")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")


def test_view_rejects_oversize_file(app, monkeypatch):
    import jarvis.ui.web.outputs_routes as oroutes
    monkeypatch.setattr(oroutes, "_VIEW_MAX_BYTES", 4)
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "big.md", "# way too long")
    client = TestClient(app)
    r = client.get(f"/api/outputs/{slug}/files/{rel}/view")
    assert r.status_code == 413


# --- "open with" chooser: detection + open-with + preferred-opener -----------
# Replaces the silent-no-op os.startfile/ShellExecute path: the file is opened
# in a resolved editor/app via a real subprocess so a window actually appears.

from types import SimpleNamespace  # noqa: E402

from jarvis.plugins.tool.app_resolver import LaunchTarget  # noqa: E402


def _fake_resolver_only(*installed: str):
    """Return a resolve_app_launch_target stub where only *installed* keys map
    to a real executable; everything else returns the raw-name startfile
    fallback (= 'not installed')."""
    def _resolve(name: str) -> LaunchTarget:
        if name in installed:
            return LaunchTarget("executable", rf"C:\apps\{name}.exe")
        return LaunchTarget("startfile", name)
    return _resolve


def test_openers_native_disabled_returns_empty(app):
    app.state.native_file_actions = False
    client = TestClient(app)
    r = client.get("/api/outputs/openers")
    assert r.status_code == 200
    assert r.json()["openers"] == []


def test_openers_always_includes_default(app):
    app.state.native_file_actions = True
    with patch(
        "jarvis.plugins.tool.app_resolver.resolve_app_launch_target",
        side_effect=_fake_resolver_only(),
    ), patch(
        "jarvis.ui.web.outputs_routes._resolve_browser_target", return_value=None
    ):
        client = TestClient(app)
        r = client.get("/api/outputs/openers")
    ids = {o["id"] for o in r.json()["openers"]}
    assert "default" in ids


def test_openers_detects_installed_editor_only(app):
    app.state.native_file_actions = True
    with patch(
        "jarvis.plugins.tool.app_resolver.resolve_app_launch_target",
        side_effect=_fake_resolver_only("code"),
    ), patch(
        "jarvis.ui.web.outputs_routes._resolve_browser_target", return_value=None
    ):
        client = TestClient(app)
        r = client.get("/api/outputs/openers")
    ids = {o["id"] for o in r.json()["openers"]}
    assert "code" in ids
    assert "cursor" not in ids  # raw fallback → not installed → hidden


def test_openers_result_is_memoized(app):
    """Resolving the editor candidates walks the Start Menu per not-installed
    editor — too slow to redo on every open. The list is cached per process, so
    a second request must serve the cache without re-resolving (the fix for the
    'open' button taking too long and flashing 'no apps detected')."""
    from unittest.mock import MagicMock

    app.state.native_file_actions = True
    resolver = MagicMock(side_effect=_fake_resolver_only("code"))
    with patch(
        "jarvis.plugins.tool.app_resolver.resolve_app_launch_target", resolver
    ), patch(
        "jarvis.ui.web.outputs_routes._resolve_browser_target", return_value=None
    ):
        client = TestClient(app)
        first = client.get("/api/outputs/openers")
        calls_after_first = resolver.call_count
        second = client.get("/api/outputs/openers")

    assert first.json() == second.json()
    assert calls_after_first > 0  # first call did the real resolution
    assert resolver.call_count == calls_after_first  # second came from the cache


def test_open_with_404_when_native_disabled(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    app.state.native_file_actions = False
    client = TestClient(app)
    r = client.post(
        f"/api/outputs/{slug}/files/{rel}/open-with", json={"opener": "default"}
    )
    assert r.status_code == 404


def test_open_with_default_calls_open_file(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    app.state.native_file_actions = True
    client = TestClient(app)
    with patch("jarvis.platform.open_path.open_file", return_value=True) as opn:
        r = client.post(
            f"/api/outputs/{slug}/files/{rel}/open-with",
            json={"opener": "default"},
        )
    assert r.status_code == 200
    assert r.json()["opened"] is True
    opn.assert_called_once()


def test_open_with_editor_calls_open_file_with(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    app.state.native_file_actions = True
    client = TestClient(app)
    with patch(
        "jarvis.plugins.tool.app_resolver.resolve_app_launch_target",
        side_effect=_fake_resolver_only("code"),
    ), patch(
        "jarvis.platform.open_path.open_file_with", return_value=True
    ) as opn:
        r = client.post(
            f"/api/outputs/{slug}/files/{rel}/open-with",
            json={"opener": "code"},
        )
    assert r.status_code == 200
    assert r.json()["opened"] is True
    opn.assert_called_once()
    # Called with (file_path, kind, value) — the resolved executable.
    args = opn.call_args.args
    assert args[1] == "executable"
    assert args[2].endswith("code.exe")


def test_open_with_rejects_unknown_opener(app):
    """A free-form opener (path/arbitrary string) must be rejected — the server
    only launches known opener ids, never a client-supplied executable path."""
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    rel = _make_deliverable(root, "019ed2dfd0fab1234", "report.md", "# Hi")
    app.state.native_file_actions = True
    client = TestClient(app)
    r = client.post(
        f"/api/outputs/{slug}/files/{rel}/open-with",
        json={"opener": r"C:\Windows\System32\evil.exe"},
    )
    assert r.status_code == 400


def test_open_with_blocks_non_deliverable(app):
    root = Path(app.state.outputs_root)
    slug = "mission_019ed2dfd0fab"
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "reflections.md").write_text("secret", encoding="utf-8")
    app.state.native_file_actions = True
    client = TestClient(app)
    r = client.post(
        f"/api/outputs/{slug}/files/reflections.md/open-with",
        json={"opener": "default"},
    )
    assert r.status_code == 404


def test_get_preferred_opener_reads_config(app):
    app.state.config = SimpleNamespace(ui=SimpleNamespace(preferred_opener="code"))
    client = TestClient(app)
    r = client.get("/api/outputs/preferred-opener")
    assert r.status_code == 200
    assert r.json()["opener"] == "code"


def test_get_preferred_opener_defaults_empty(app):
    # No config on app.state → empty (chooser will prompt).
    client = TestClient(app)
    r = client.get("/api/outputs/preferred-opener")
    assert r.status_code == 200
    assert r.json()["opener"] == ""


def test_put_preferred_opener_persists_and_updates(app):
    app.state.config = SimpleNamespace(ui=SimpleNamespace(preferred_opener=""))
    client = TestClient(app)
    with patch(
        "jarvis.core.config_writer.set_preferred_opener"
    ) as setter:
        r = client.put(
            "/api/outputs/preferred-opener", json={"opener": "code"}
        )
    assert r.status_code == 200
    setter.assert_called_once_with("code")
    assert app.state.config.ui.preferred_opener == "code"


def test_put_preferred_opener_rejects_unknown(app):
    client = TestClient(app)
    r = client.put(
        "/api/outputs/preferred-opener", json={"opener": "../../evil"}
    )
    assert r.status_code == 400
