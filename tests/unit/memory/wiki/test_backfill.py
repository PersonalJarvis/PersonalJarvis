"""Tests for evidence-safe persisted Realtime session backfill."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.memory.wiki.backfill import backfill_realtime_sessions


class _Store:
    def __init__(self, sessions, turns):  # noqa: ANN001
        self.sessions = sessions
        self.turns = turns
        self.list_calls: list[tuple[int, int]] = []

    def list_sessions(  # noqa: ANN201
        self, *, limit: int = 100, offset: int = 0,
    ):
        self.list_calls.append((limit, offset))
        return self.sessions[offset : offset + limit]

    def get_turns(self, session_id: str):  # noqa: ANN201
        return self.turns.get(session_id, [])


class _Extractor:
    def __init__(
        self,
        seen: set[str] | None = None,
        *,
        result_status: str = "candidates",
    ) -> None:
        self.seen = set(seen or ())
        self.statuses = {key: "candidates" for key in self.seen}
        self.result_status = result_status
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def capture_seen(self, review_key: str) -> bool:
        return review_key in self.seen

    def capture_status(self, review_key: str) -> str | None:
        return self.statuses.get(review_key)

    async def extract_session_and_journal(
        self, turns, *, session_id: str, source_label: str, review_key: str  # noqa: ANN001
    ) -> int:
        assert source_label.endswith(session_id)
        self.calls.append((session_id, tuple(t.turn_id for t in turns)))
        self.statuses[review_key] = self.result_status
        if self.result_status in {"filtered", "empty", "candidates"}:
            self.seen.add(review_key)
        return len(turns) if self.result_status == "candidates" else 0


class _V3Extractor(_Extractor):
    def session_review_keys(self, turns, *, session_id: str):  # noqa: ANN001, ANN201
        del turns
        return (f"session:v3:{session_id}:chunk:000:grounded",)

    async def extract_session_and_journal(
        self, turns, *, session_id: str, source_label: str  # noqa: ANN001
    ) -> int:
        key = self.session_review_keys(turns, session_id=session_id)[0]
        assert source_label.endswith(session_id)
        self.calls.append((session_id, tuple(t.turn_id for t in turns)))
        self.statuses[key] = self.result_status
        if self.result_status in {"filtered", "empty", "candidates"}:
            self.seen.add(key)
        return len(turns) if self.result_status == "candidates" else 0


def _session(session_id: str, started_ms: int, *, ended: bool = True):  # noqa: ANN202
    return SimpleNamespace(
        id=session_id,
        started_ms=started_ms,
        ended_ms=started_ms + 1_000 if ended else None,
    )


def _turn(turn_id: str, *, tier: str = "realtime", user_text: str = "A fact"):
    return SimpleNamespace(
        id=turn_id,
        tier=tier,
        user_text=user_text,
        jarvis_text="Context only",
    )


@pytest.mark.asyncio
async def test_preview_counts_only_recent_completed_realtime_sessions() -> None:
    now_ms = 10 * 24 * 60 * 60 * 1000
    recent = now_ms - 60_000
    old = now_ms - 4 * 24 * 60 * 60 * 1000
    store = _Store(
        [
            _session("eligible", recent),
            _session("already", recent),
            _session("classic", recent),
            _session("open", recent, ended=False),
            _session("old", old),
        ],
        {
            "eligible": [_turn("t1"), _turn("t2")],
            "already": [_turn("t3")],
            "classic": [_turn("t4", tier="router")],
            "open": [_turn("t5")],
            "old": [_turn("t6")],
        },
    )
    extractor = _Extractor({"session:v2:already"})

    result = await backfill_realtime_sessions(
        store=store,
        extractor=extractor,
        days=2,
        dry_run=True,
        now_ms=now_ms,
    )

    assert result.sessions_scanned == 3
    assert result.sessions_eligible == 1
    assert result.sessions_already_reviewed == 1
    assert result.sessions_in_progress == 0
    assert result.turns_considered == 3
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_execute_sweeps_once_and_is_idempotent() -> None:
    now_ms = 10 * 24 * 60 * 60 * 1000
    store = _Store(
        [_session("s1", now_ms - 1_000)],
        {"s1": [_turn("t1"), _turn("t2")]},
    )
    extractor = _Extractor()

    first = await backfill_realtime_sessions(
        store=store,
        extractor=extractor,
        dry_run=False,
        now_ms=now_ms,
    )
    second = await backfill_realtime_sessions(
        store=store,
        extractor=extractor,
        dry_run=False,
        now_ms=now_ms,
    )

    assert first.sessions_reviewed == 1
    assert first.sessions_failed == 0
    assert first.candidates_journaled == 2
    assert first.attempted_review_keys == ("session:v2:s1",)
    assert extractor.calls == [("s1", ("t1", "t2"))]
    assert second.sessions_reviewed == 0
    assert second.sessions_already_reviewed == 1


@pytest.mark.asyncio
async def test_legacy_v2_terminal_review_does_not_suppress_grounded_v3() -> None:
    now_ms = 10 * 24 * 60 * 60 * 1000
    store = _Store(
        [_session("s1", now_ms - 1_000)],
        {"s1": [_turn("t1")]},
    )
    extractor = _V3Extractor({"session:v2:s1"})

    result = await backfill_realtime_sessions(
        store=store,
        extractor=extractor,
        dry_run=False,
        now_ms=now_ms,
    )

    assert result.sessions_reviewed == 1
    assert result.sessions_already_reviewed == 0
    assert result.attempted_review_keys == (
        "session:v3:s1:chunk:000:grounded",
    )
    assert extractor.calls == [("s1", ("t1",))]


@pytest.mark.asyncio
async def test_started_capture_is_reported_without_duplicate_model_call() -> None:
    now_ms = 10 * 24 * 60 * 60 * 1000
    store = _Store(
        [_session("active", now_ms - 1_000)],
        {"active": [_turn("t1")]},
    )
    extractor = _Extractor()
    extractor.statuses["session:v2:active"] = "started"

    result = await backfill_realtime_sessions(
        store=store,
        extractor=extractor,
        dry_run=False,
        now_ms=now_ms,
    )

    assert result.sessions_in_progress == 1
    assert result.sessions_reviewed == 0
    assert result.sessions_failed == 0
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_provider_failure_is_not_counted_as_reviewed() -> None:
    now_ms = 10 * 24 * 60 * 60 * 1000
    store = _Store(
        [_session("failed", now_ms - 1_000)],
        {"failed": [_turn("t1")]},
    )
    extractor = _Extractor(result_status="failed")

    result = await backfill_realtime_sessions(
        store=store,
        extractor=extractor,
        dry_run=False,
        now_ms=now_ms,
    )

    assert result.sessions_reviewed == 0
    assert result.sessions_failed == 1
    assert result.candidates_journaled == 0


@pytest.mark.asyncio
async def test_backfill_pages_past_non_realtime_prefix() -> None:
    now_ms = 10 * 24 * 60 * 60 * 1000
    sessions = [
        _session(f"classic-{index}", now_ms - index)
        for index in range(100)
    ]
    sessions.append(_session("eligible-later", now_ms - 101))
    turns = {
        session.id: [_turn(f"turn-{session.id}", tier="router")]
        for session in sessions[:-1]
    }
    turns["eligible-later"] = [_turn("grounded-realtime")]
    store = _Store(sessions, turns)
    extractor = _Extractor()

    result = await backfill_realtime_sessions(
        store=store,
        extractor=extractor,
        max_sessions=1,
        dry_run=True,
        now_ms=now_ms,
    )

    assert result.sessions_eligible == 1
    assert result.attempted_review_keys == ("session:v2:eligible-later",)
    assert store.list_calls == [(100, 0), (100, 100)]
