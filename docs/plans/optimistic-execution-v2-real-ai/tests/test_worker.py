"""Unit tests for optimistic/worker.py — Sub-Agent 1 (AI Backend, v2 rewrite).

TDD-first. The worker now uses real LLM calls (via optimistic/llm.py) instead of
SmartTool. Tests use backend='mock' so no network is involved. Uses asyncio.run()
throughout; no pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx

from optimistic.events import (
    CorrectionReason,
    MissionSpawn,
    WorkerCompleted,
    WorkerCorrectionNeeded,
    WorkerStarted,
)

# ---------------------------------------------------------------------------
# FakeBus — copied verbatim from CONTRACTS.md
# ---------------------------------------------------------------------------

class FakeBus:
    def __init__(self):
        self.published = []
        self._subs = {}
        self._all = []

    def subscribe(self, event_type, handler):
        self._subs.setdefault(event_type, []).append(handler)

    def subscribe_all(self, handler):
        self._all.append(handler)

    async def publish(self, event):
        self.published.append(event)
        for et, hs in self._subs.items():
            if isinstance(event, et):
                for h in hs:
                    await h(event)
        for h in self._all:
            await h(event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def _mock_settings():
    """LLMSettings with backend='mock' — no network required."""
    from optimistic.config import load_settings
    return load_settings(env={"LLM_BACKEND": "mock", "LLM_MODEL": "test-model"})


def make_spawn(
    command: str,
    tool_name: str | None = None,
    context: dict | None = None,
    session_id: str = "default",
) -> MissionSpawn:
    """Build a MissionSpawn event with a fresh trace_id."""
    return MissionSpawn(
        command=command,
        tool_name=tool_name,
        context=context or {},
        trace_id=uuid.uuid4(),
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestWorkerConstruction:
    def test_worker_subscribes_to_mission_spawn(self):
        """HeavyDutyWorker registers a MissionSpawn handler on construction."""
        from optimistic.worker import HeavyDutyWorker
        bus = FakeBus()
        HeavyDutyWorker(bus, _mock_settings())
        assert MissionSpawn in bus._subs
        assert len(bus._subs[MissionSpawn]) == 1

    def test_in_flight_starts_at_zero(self):
        from optimistic.worker import HeavyDutyWorker
        bus = FakeBus()
        worker = HeavyDutyWorker(bus, _mock_settings())
        assert worker.in_flight == 0


# ---------------------------------------------------------------------------
# Delegation is instant
# ---------------------------------------------------------------------------

class TestDelegationIsInstant:
    def test_in_flight_ge_1_right_after_publish(self):
        """_on_mission_spawn schedules the task and returns immediately."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("Termin buche für morgen", tool_name="calendar")
            await bus.publish(spawn)
            in_flight_before = worker.in_flight
            await worker.drain()
            in_flight_after = worker.in_flight
            return in_flight_before, in_flight_after

        before, after = run(scenario())
        assert before >= 1, f"Expected in_flight >= 1 right after publish, got {before}"
        assert after == 0, f"Expected in_flight == 0 after drain(), got {after}"

    def test_drain_brings_in_flight_to_zero(self):
        """After drain(), all tasks are finished."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            for i in range(3):
                await bus.publish(make_spawn(f"Termin {i}", tool_name="calendar"))
            await worker.drain()
            return worker.in_flight

        assert run(scenario()) == 0


# ---------------------------------------------------------------------------
# Success path: WorkerStarted then WorkerCompleted (mock LLM)
# ---------------------------------------------------------------------------

class TestWorkerSuccessPath:
    def test_publishes_worker_started_then_completed(self):
        """Mock LLM backend: worker publishes WorkerStarted then WorkerCompleted."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("Termin anlegen", tool_name="calendar")
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        types = [type(e).__name__ for e in events]
        assert "WorkerStarted" in types
        assert "WorkerCompleted" in types

    def test_worker_started_before_completed(self):
        """WorkerStarted must appear before WorkerCompleted."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("drive upload", tool_name="drive")
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        started_idx = next(i for i, e in enumerate(events) if isinstance(e, WorkerStarted))
        completed_idx = next(i for i, e in enumerate(events) if isinstance(e, WorkerCompleted))
        assert started_idx < completed_idx

    def test_result_is_mock_string(self):
        """WorkerCompleted.result contains the mock model prefix."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("do something", tool_name="calendar")
            await bus.publish(spawn)
            await worker.drain()
            completed = next(e for e in bus.published if isinstance(e, WorkerCompleted))
            return completed.result

        result = run(scenario())
        assert result  # non-empty
        assert "mock" in result.lower() or "test-model" in result

    def test_trace_id_propagated_to_started(self):
        """WorkerStarted carries the same trace_id as MissionSpawn."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("Termin anlegen", tool_name="calendar")
            await bus.publish(spawn)
            await worker.drain()
            started = next(e for e in bus.published if isinstance(e, WorkerStarted))
            return spawn.trace_id, started.trace_id

        spawn_tid, started_tid = run(scenario())
        assert spawn_tid == started_tid

    def test_trace_id_propagated_to_completed(self):
        """WorkerCompleted carries the same trace_id as MissionSpawn."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("drive upload", tool_name="drive")
            await bus.publish(spawn)
            await worker.drain()
            completed = next(e for e in bus.published if isinstance(e, WorkerCompleted))
            return spawn.trace_id, completed.trace_id

        spawn_tid, completed_tid = run(scenario())
        assert spawn_tid == completed_tid

    def test_session_id_propagated_to_started(self):
        """WorkerStarted carries the same session_id as MissionSpawn."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("do work", tool_name="drive", session_id="sess-42")
            await bus.publish(spawn)
            await worker.drain()
            started = next(e for e in bus.published if isinstance(e, WorkerStarted))
            return spawn.session_id, started.session_id

        spawn_sid, started_sid = run(scenario())
        assert spawn_sid == started_sid

    def test_session_id_propagated_to_completed(self):
        """WorkerCompleted carries the same session_id as MissionSpawn."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("do work", tool_name="drive", session_id="sess-99")
            await bus.publish(spawn)
            await worker.drain()
            completed = next(e for e in bus.published if isinstance(e, WorkerCompleted))
            return spawn.session_id, completed.session_id

        spawn_sid, completed_sid = run(scenario())
        assert spawn_sid == completed_sid

    def test_mission_id_matches_in_completed(self):
        """WorkerCompleted.mission_id must match MissionSpawn.mission_id."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("drive upload", tool_name="drive")
            await bus.publish(spawn)
            await worker.drain()
            completed = next(e for e in bus.published if isinstance(e, WorkerCompleted))
            return spawn.mission_id, completed.mission_id

        spawn_mid, completed_mid = run(scenario())
        assert spawn_mid == completed_mid


# ---------------------------------------------------------------------------
# Gmail + MISSING_INFO path (check_missing_info pre-check)
# ---------------------------------------------------------------------------

class TestWorkerMissingInfoPath:
    def test_publishes_correction_needed_missing_info_for_max(self):
        """'Schreib Max eine Mail' with empty contacts → WorkerCorrectionNeeded(MISSING_INFO)."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn(
                "Schreib Max eine Mail",
                tool_name="gmail",
                context={},
            )
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        corrections = [e for e in events if isinstance(e, WorkerCorrectionNeeded)]
        assert corrections, "Expected WorkerCorrectionNeeded"
        err = corrections[0]
        assert err.reason == CorrectionReason.MISSING_INFO
        assert "Max" in err.detail

    def test_no_worker_completed_on_missing_info(self):
        """WorkerCompleted must NOT be published when MISSING_INFO fires."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("Schreib Max eine Mail", tool_name="gmail", context={})
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        assert not any(isinstance(e, WorkerCompleted) for e in events)

    def test_correction_needed_trace_id_propagated(self):
        """WorkerCorrectionNeeded carries the same trace_id as MissionSpawn."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn("Schreib Max eine Mail", tool_name="gmail", context={})
            await bus.publish(spawn)
            await worker.drain()
            corr = next(e for e in bus.published if isinstance(e, WorkerCorrectionNeeded))
            return spawn.trace_id, corr.trace_id

        spawn_tid, corr_tid = run(scenario())
        assert spawn_tid == corr_tid

    def test_correction_needed_session_id_propagated(self):
        """WorkerCorrectionNeeded carries the same session_id as MissionSpawn."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn(
                "Schreib Max eine Mail",
                tool_name="gmail",
                context={},
                session_id="session-abc",
            )
            await bus.publish(spawn)
            await worker.drain()
            corr = next(e for e in bus.published if isinstance(e, WorkerCorrectionNeeded))
            return spawn.session_id, corr.session_id

        spawn_sid, corr_sid = run(scenario())
        assert spawn_sid == corr_sid

    def test_correction_needed_includes_command(self):
        """WorkerCorrectionNeeded.command echoes the original command."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            cmd = "Schreib Max eine Mail"
            spawn = make_spawn(cmd, tool_name="gmail", context={})
            await bus.publish(spawn)
            await worker.drain()
            corr = next(e for e in bus.published if isinstance(e, WorkerCorrectionNeeded))
            return cmd, corr.command

        orig_cmd, corr_cmd = run(scenario())
        assert orig_cmd == corr_cmd

    def test_gmail_with_contact_present_completes_successfully(self):
        """Gmail mission with 'Max' in contacts → WorkerCompleted (not correction)."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            spawn = make_spawn(
                "Schreib Max eine Mail",
                tool_name="gmail",
                context={"contacts": {"Max": "max@example.com"}},
            )
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        assert any(isinstance(e, WorkerCompleted) for e in events)
        assert not any(isinstance(e, WorkerCorrectionNeeded) for e in events)


# ---------------------------------------------------------------------------
# Network error path with retry → WorkerCorrectionNeeded(NETWORK_ERROR)
# ---------------------------------------------------------------------------

class TestWorkerNetworkErrorPath:
    def _make_failing_transport(self):
        """httpx.MockTransport that always returns HTTP 500."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "server error"})
        return httpx.MockTransport(handler)

    def _http_settings_with_transport(self):
        """LLMSettings pointing at a fake URL; transport injected separately."""
        from optimistic.config import load_settings
        return load_settings(env={
            "LLM_BACKEND": "http",
            "LLM_MODEL": "test-model",
            "LLM_BASE_URL": "http://localhost:11434/v1",
        })

    def test_network_error_produces_correction_needed(self):
        """HTTP 500 on both attempts → WorkerCorrectionNeeded(NETWORK_ERROR)."""
        from optimistic.worker import HeavyDutyWorker

        transport = self._make_failing_transport()
        settings = self._http_settings_with_transport()

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, settings, _transport=transport)
            spawn = make_spawn("do something", tool_name="drive")
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        corrections = [e for e in events if isinstance(e, WorkerCorrectionNeeded)]
        assert corrections, "Expected WorkerCorrectionNeeded after network errors"
        assert corrections[0].reason == CorrectionReason.NETWORK_ERROR

    def test_network_error_no_completed_event(self):
        """WorkerCompleted must NOT be published when network fails both times."""
        from optimistic.worker import HeavyDutyWorker

        transport = self._make_failing_transport()
        settings = self._http_settings_with_transport()

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, settings, _transport=transport)
            spawn = make_spawn("do something", tool_name="drive")
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        assert not any(isinstance(e, WorkerCompleted) for e in events)

    def test_run_never_raises_out_of_task(self):
        """_run must swallow all exceptions; drain() must not raise."""
        from optimistic.worker import HeavyDutyWorker

        transport = self._make_failing_transport()
        settings = self._http_settings_with_transport()

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, settings, _transport=transport)
            spawn = make_spawn("do something", tool_name="drive")
            await bus.publish(spawn)
            await worker.drain()  # must not raise
            return True

        assert run(scenario()) is True


# ---------------------------------------------------------------------------
# Multiple concurrent missions
# ---------------------------------------------------------------------------

class TestWorkerConcurrency:
    def test_multiple_missions_all_complete(self):
        """Multiple MissionSpawn events all produce outcome events."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus, _mock_settings())
            commands = [
                ("Termin anlegen", "calendar"),
                ("drive upload", "drive"),
                ("Schreib Anna eine Mail", "gmail"),
            ]
            for cmd, tool in commands:
                await bus.publish(make_spawn(
                    cmd, tool_name=tool,
                    context={"contacts": {"Anna": "anna@x.de"}},
                ))
            await worker.drain()
            return bus.published

        events = run(scenario())
        # calendar + drive → WorkerCompleted; gmail with Anna in contacts → WorkerCompleted
        completed = [e for e in events if isinstance(e, WorkerCompleted)]
        assert len(completed) == 3
