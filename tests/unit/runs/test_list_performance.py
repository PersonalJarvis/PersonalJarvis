"""The run LIST must stay cheap and must not block the event loop.

Both guards exist because of one live failure (2026-07-25): with a voice
session running, ``GET /api/runs?limit=100`` took 45 s and froze every other
API call — the desktop UI looked dead. Two causes, one test each:

  1. the list pulled EVERY event of EVERY session (100 store-lock acquisitions
     while the recorder wrote through the same lock),
  2. the route did that synchronous work directly on the asyncio loop, so the
     wait blocked the whole server rather than just this request.
"""
from __future__ import annotations

import asyncio
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.runs.loader import _LIST_EVENT_KINDS, RunLoader
from jarvis.runs.routes import router as runs_router


class _RecordingStore:
    """Session store stub that records HOW it was queried."""

    def __init__(self, sessions: list) -> None:
        self._sessions = sessions
        self.per_session_calls: list[str] = []
        self.bulk_calls: list[tuple[int, list[str] | None]] = []

    def list_sessions(self, *, limit: int = 100, **_: object) -> list:
        return self._sessions[:limit]

    def get_events(self, session_id: str) -> list:
        self.per_session_calls.append(session_id)
        return []

    def get_events_for_sessions(
        self, session_ids: list[str], *, kinds: list[str] | None = None
    ) -> dict[str, list]:
        self.bulk_calls.append((len(session_ids), kinds))
        return {}

    def get_session(self, session_id: str):  # pragma: no cover - unused here
        return None

    def get_turns(self, session_id: str) -> list:  # pragma: no cover
        return []


def _session(idx: int):
    from jarvis.sessions.models import SessionListItem
    return SessionListItem(id=f"s{idx}", started_ms=idx, ended_ms=idx + 1,
                           duration_s=0.001, preview="")


def test_list_runs_uses_one_narrow_bulk_query_not_a_fetch_per_session():
    store = _RecordingStore([_session(i) for i in range(100)])
    RunLoader(session_store=store, usage_log=None).list_runs(limit=100)
    assert store.per_session_calls == [], "regressed to a full fetch per session"
    assert len(store.bulk_calls) == 1
    count, kinds = store.bulk_calls[0]
    assert count == 100
    # Narrow by kind — "all kinds" is exactly the regression this replaced.
    assert kinds == _LIST_EVENT_KINDS
    assert "TranscriptionUpdate" not in (kinds or [])


def test_run_routes_do_their_blocking_work_off_the_event_loop():
    """A slow store must not stall a concurrent request on another route."""
    loop_thread_ids: list[int] = []
    barrier = threading.Event()

    class _SlowStore(_RecordingStore):
        def get_events_for_sessions(self, session_ids, *, kinds=None):
            loop_thread_ids.append(threading.get_ident())
            barrier.wait(timeout=5.0)
            return {}

    app = FastAPI()
    app.include_router(runs_router)
    app.state.session_store = _SlowStore([_session(0)])

    @app.get("/probe")
    async def probe() -> dict:  # the "is the server still alive?" call
        return {"alive": True}

    with TestClient(app) as client:
        # Capture the loop's own thread id, then prove the store ran elsewhere.
        loop_ident: dict[str, int] = {}

        @app.get("/whoami")
        async def whoami() -> dict:
            loop_ident["id"] = threading.get_ident()
            return loop_ident

        client.get("/whoami")

        done: list[int] = []

        def _call_runs() -> None:
            done.append(client.get("/api/runs?limit=1").status_code)

        t = threading.Thread(target=_call_runs, daemon=True)
        t.start()
        # While the store is blocked, an unrelated route must still answer.
        for _ in range(50):
            if loop_thread_ids:
                break
            asyncio.run(asyncio.sleep(0.02))
        assert loop_thread_ids, "store was never called"
        assert client.get("/probe").json() == {"alive": True}
        # And the blocking work genuinely ran on a worker thread.
        assert loop_thread_ids[0] != loop_ident["id"]
        barrier.set()
        t.join(timeout=5.0)
        assert done == [200]
