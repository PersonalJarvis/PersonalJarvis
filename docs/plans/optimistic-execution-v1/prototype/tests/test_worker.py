"""Unit tests for optimistic/worker.py — SA2 (MCP & Tooling).

TDD-first. All tests are sync functions that drive async code via asyncio.run().
Uses the FakeBus stub from CONTRACTS.md — no dependency on SA1's EventBus.
"""
from __future__ import annotations

import asyncio
import uuid

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


def make_spawn(
    command: str, tool_name: str | None = None, context: dict | None = None
) -> MissionSpawn:
    """Build a MissionSpawn event with a fresh trace_id."""
    return MissionSpawn(
        command=command,
        tool_name=tool_name,
        context=context or {},
        trace_id=uuid.uuid4(),
    )


# ---------------------------------------------------------------------------
# Worker construction and subscription
# ---------------------------------------------------------------------------

class TestWorkerConstruction:
    def test_worker_subscribes_to_mission_spawn(self):
        """HeavyDutyWorker must register a handler for MissionSpawn on construction."""
        from optimistic.worker import HeavyDutyWorker
        bus = FakeBus()
        HeavyDutyWorker(bus)  # construction registers the subscription (side effect)
        assert MissionSpawn in bus._subs
        assert len(bus._subs[MissionSpawn]) == 1

    def test_in_flight_starts_at_zero(self):
        from optimistic.worker import HeavyDutyWorker
        bus = FakeBus()
        worker = HeavyDutyWorker(bus)
        assert worker.in_flight == 0


# ---------------------------------------------------------------------------
# Delegation is instant (in_flight test)
# ---------------------------------------------------------------------------

class TestDelegationIsInstant:
    def test_in_flight_ge_1_right_after_publish(self):
        """_on_mission_spawn must schedule the task and return immediately;
        in_flight must be >= 1 before drain() is called."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            spawn = make_spawn("Termin buche für morgen", tool_name="calendar")  # i18n-allow: test content — user voice utterance DE
            # Use a slow work_seconds so the task is definitely still running.
            # We patch via monkeypatching after construction is not practical here,
            # so we use a zero-delay spawn and check the property synchronously
            # right after the publish call, before yielding to the event loop.
            await bus.publish(spawn)
            # At this point _on_mission_spawn has been awaited (FakeBus is sync-dispatch),
            # which scheduled the task but has not yet run _run. in_flight must be >= 1.
            in_flight_before_drain = worker.in_flight
            await worker.drain()
            in_flight_after_drain = worker.in_flight
            return in_flight_before_drain, in_flight_after_drain

        before, after = run(scenario())
        assert before >= 1, f"Expected in_flight >= 1 right after publish, got {before}"
        assert after == 0, f"Expected in_flight == 0 after drain(), got {after}"

    def test_drain_brings_in_flight_to_zero(self):
        """After drain(), all tasks are finished and in_flight == 0."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            for i in range(3):
                await bus.publish(make_spawn(f"Termin {i}", tool_name="calendar"))
            await worker.drain()
            return worker.in_flight

        assert run(scenario()) == 0


# ---------------------------------------------------------------------------
# Success path: WorkerStarted then WorkerCompleted
# ---------------------------------------------------------------------------

class TestWorkerSuccessPath:
    def test_publishes_worker_started_then_completed(self):
        """On a successful mission, worker publishes WorkerStarted then WorkerCompleted."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            spawn = make_spawn("Termin anlegen", tool_name="calendar")
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        types = [type(e).__name__ for e in events]
        # MissionSpawn is already published by the test itself.
        assert "WorkerStarted" in types
        assert "WorkerCompleted" in types

    def test_worker_started_before_completed(self):
        """WorkerStarted must appear before WorkerCompleted in the event log."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            spawn = make_spawn("drive upload", tool_name="drive")
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        started_idx = next(i for i, e in enumerate(events) if isinstance(e, WorkerStarted))
        completed_idx = next(i for i, e in enumerate(events) if isinstance(e, WorkerCompleted))
        assert started_idx < completed_idx

    def test_trace_id_propagated_to_started(self):
        """WorkerStarted carries the same trace_id as the originating MissionSpawn."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            spawn = make_spawn("Termin anlegen", tool_name="calendar")
            await bus.publish(spawn)
            await worker.drain()
            started = next(e for e in bus.published if isinstance(e, WorkerStarted))
            return spawn.trace_id, started.trace_id

        spawn_tid, started_tid = run(scenario())
        assert spawn_tid == started_tid

    def test_trace_id_propagated_to_completed(self):
        """WorkerCompleted carries the same trace_id as the originating MissionSpawn."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            spawn = make_spawn("drive upload", tool_name="drive")
            await bus.publish(spawn)
            await worker.drain()
            completed = next(e for e in bus.published if isinstance(e, WorkerCompleted))
            return spawn.trace_id, completed.trace_id

        spawn_tid, completed_tid = run(scenario())
        assert spawn_tid == completed_tid

    def test_mission_id_matches_in_completed(self):
        """WorkerCompleted.mission_id must match the MissionSpawn.mission_id."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            spawn = make_spawn("drive upload", tool_name="drive")
            await bus.publish(spawn)
            await worker.drain()
            completed = next(e for e in bus.published if isinstance(e, WorkerCompleted))
            return spawn.mission_id, completed.mission_id

        spawn_mid, completed_mid = run(scenario())
        assert spawn_mid == completed_mid


# ---------------------------------------------------------------------------
# MissingInfo failure path (canonical Max scenario)
# ---------------------------------------------------------------------------

class TestWorkerMissingInfoPath:
    def test_publishes_correction_needed_missing_info(self):
        """Worker publishes WorkerCorrectionNeeded(MISSING_INFO) for the
        Max-without-contact mission."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            spawn = make_spawn(
                "Schreib Max eine Mail",
                tool_name="gmail",
                context={},  # empty contacts — must trigger MISSING_INFO
            )
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        corrections = [e for e in events if isinstance(e, WorkerCorrectionNeeded)]
        assert corrections, "Expected at least one WorkerCorrectionNeeded event"
        err = corrections[0]
        assert err.reason == CorrectionReason.MISSING_INFO
        assert "Max" in err.detail

    def test_no_worker_completed_on_missing_info(self):
        """When MissingInfoError is raised, WorkerCompleted must NOT be published."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            spawn = make_spawn("Schreib Max eine Mail", tool_name="gmail", context={})
            await bus.publish(spawn)
            await worker.drain()
            return bus.published

        events = run(scenario())
        assert not any(isinstance(e, WorkerCompleted) for e in events)

    def test_correction_needed_trace_id_propagated(self):
        """WorkerCorrectionNeeded carries the same trace_id as the originating MissionSpawn."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            spawn = make_spawn("Schreib Max eine Mail", tool_name="gmail", context={})
            await bus.publish(spawn)
            await worker.drain()
            corr = next(e for e in bus.published if isinstance(e, WorkerCorrectionNeeded))
            return spawn.trace_id, corr.trace_id

        spawn_tid, corr_tid = run(scenario())
        assert spawn_tid == corr_tid

    def test_correction_needed_includes_command(self):
        """WorkerCorrectionNeeded.command echoes the original command."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            cmd = "Schreib Max eine Mail"
            spawn = make_spawn(cmd, tool_name="gmail", context={})
            await bus.publish(spawn)
            await worker.drain()
            corr = next(e for e in bus.published if isinstance(e, WorkerCorrectionNeeded))
            return cmd, corr.command

        orig_cmd, corr_cmd = run(scenario())
        assert orig_cmd == corr_cmd


# ---------------------------------------------------------------------------
# Fatal / generic exception path with retry
# ---------------------------------------------------------------------------

class TestWorkerFatalPath:
    def test_fatal_exception_publishes_correction_needed_fatal(self):
        """When a non-MissingInfoError exception occurs twice (retry exhausted),
        worker publishes WorkerCorrectionNeeded(FATAL)."""
        import unittest.mock as mock

        from optimistic import tools as tools_mod
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            # Use a command that we can make fail by patching SmartTool.execute.
            spawn = make_spawn("drive upload", tool_name="drive", context={})

            call_count = 0

            def always_fail_tool(name):
                class FailTool:
                    async def execute(self, command, context):
                        nonlocal call_count
                        call_count += 1
                        raise RuntimeError("simulated MCP failure")
                return FailTool()

            with mock.patch.object(tools_mod, "get_smart_tool", side_effect=always_fail_tool):
                await bus.publish(spawn)
                await worker.drain()

            return bus.published, call_count

        events, retries = run(scenario())
        corrections = [e for e in events if isinstance(e, WorkerCorrectionNeeded)]
        assert corrections, "Expected WorkerCorrectionNeeded after fatal failure"
        assert corrections[0].reason == CorrectionReason.FATAL
        # Must have retried once (2 total calls) before publishing FATAL.
        assert retries == 2, f"Expected 2 execute calls (1 original + 1 retry), got {retries}"

    def test_run_never_raises_out_of_task(self):
        """_run must swallow all exceptions; no task exception should escape to drain()."""
        import unittest.mock as mock

        from optimistic import tools as tools_mod
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            spawn = make_spawn("drive upload", tool_name="drive")

            def catastrophic_tool(name):
                class BrokenTool:
                    async def execute(self, command, context):
                        raise RuntimeError("catastrophic failure")
                return BrokenTool()

            with mock.patch.object(tools_mod, "get_smart_tool", side_effect=catastrophic_tool):
                await bus.publish(spawn)
                # drain() must not raise even if _run had unhandled exceptions.
                await worker.drain()
            return True  # reaching here means no exception escaped

        assert run(scenario()) is True


# ---------------------------------------------------------------------------
# Multiple concurrent missions
# ---------------------------------------------------------------------------

class TestWorkerConcurrency:
    def test_multiple_missions_all_complete(self):
        """Multiple MissionSpawn events all produce WorkerCompleted events."""
        from optimistic.worker import HeavyDutyWorker

        async def scenario():
            bus = FakeBus()
            worker = HeavyDutyWorker(bus)
            commands = [
                ("Termin anlegen", "calendar"),
                ("drive upload", "drive"),
                ("Schreib Anna eine Mail", "gmail"),
            ]
            for cmd, tool in commands:
                await bus.publish(make_spawn(cmd, tool_name=tool,
                                             context={"contacts": {"Anna": "anna@x.de"}}))
            await worker.drain()
            return bus.published

        events = run(scenario())
        completed = [e for e in events if isinstance(e, WorkerCompleted)]
        # calendar + drive → WorkerCompleted; gmail with Anna in contacts → WorkerCompleted
        assert len(completed) == 3
