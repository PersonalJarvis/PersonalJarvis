"""Owned routine readback retains durable outcomes without stale success claims."""

from jarvis.society.routines import list_routines
from jarvis.tasks.schema import AgentAction, TaskSpec, TriggerEvery
from jarvis.tasks.store import TaskStore


async def test_receipt_readback_survives_reopen_and_excludes_other_owners(tmp_path):
    path = tmp_path / "tasks.db"
    async with TaskStore(path) as store:
        own = await store.insert(TaskSpec(
            title="Scout briefing",
            trigger=TriggerEvery(interval_seconds=3600),
            action=AgentAction(prompt="Brief"),
            tags=("society", "agent:scout"),
        ))
        other = await store.insert(TaskSpec(
            title="Private briefing",
            trigger=TriggerEvery(interval_seconds=3600),
            action=AgentAction(prompt="Private"),
            tags=("society", "agent:other"),
        ))
        assert (await list_routines(store, "scout"))[0]["last_run_state"] is None
        await store.update_state(own, "running")
        await store.append_step(own, "log", {"event": "agent_result", "text": "A" * 500})
        await store.update_state(own, "scheduled", result={"duration_ms": 1})
        await store.append_step(other, "log", {"event": "agent_result", "text": "Private"})
        await store.update_state(other, "scheduled", result={"duration_ms": 1})

    async with TaskStore(path) as store:
        rows = await list_routines(store, "scout")
        assert len(rows) == 1
        assert rows[0]["id"] == own
        assert rows[0]["state"] == "scheduled"
        assert rows[0]["last_run_state"] == "completed"
        assert rows[0]["last_result"] == "A" * 400
        assert rows[0]["last_error"] is None

        await store.update_state(own, "running")
        running = (await list_routines(store, "scout"))[0]
        assert running["last_run_state"] == "running"
        assert running["last_result"] is None
        await store.update_state(own, "scheduled", error="Owner unavailable")
        failed = (await list_routines(store, "scout"))[0]
        assert failed["last_run_state"] == "failed"
        assert failed["last_result"] is None
        assert failed["last_error"] == "Owner unavailable"
