"""Outpost work uses real Society authority; only the provider is a labeled fake."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.events import MsgType, SocietyEnvelope
from jarvis.society.mars.models import CommandState, StationCommand, StationError
from jarvis.society.mars.ordinary import OrdinaryStationExecutor
from jarvis.society.mars.service import MarsStationService
from jarvis.society.mars.store import MarsStore
from jarvis.society.runtime import SocietyRuntime


class ControlledChat(AgentChatService):
    """No provider invocation: hold one task until the test emits its durable result."""

    def __init__(self, path: Path):
        super().__init__(AgentChatStore(path))
        self.sent: list[dict] = []
        self.cancel_calls: list[tuple[str, str | None]] = []
        self.cancel_status: str | None = "cancelled"

    async def send(self, session_id, text, **kwargs):
        self.sent.append({"session_id": session_id, "text": text, **kwargs})
        return "controlled-turn"

    async def cancel(self, session_id, *, expected_turn_id=None):
        """Controlled terminal race; no real runner/provider or wall-clock sleep."""
        self.cancel_calls.append((session_id, expected_turn_id))
        if self.cancel_status is not None:
            await self._emit(
                session_id,
                {
                    "kind": "turn_finished",
                    "payload": {"turn_id": expected_turn_id, "status": self.cancel_status},
                },
            )
        return True


@pytest.fixture
async def ordinary(tmp_path):
    service = ControlledChat(tmp_path / "chat.db")
    runtime = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        chat_service=lambda: service,
        cfg=lambda: SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path))),
    )
    await runtime.ensure_started()
    await runtime.roster.create(name="Comms", provider="openai")
    try:
        yield runtime, service, OrdinaryStationExecutor(runtime)
    finally:
        await runtime.close()
        service.store.close()


async def test_draft_dispatch_reuses_authority_and_does_not_repeat_after_retry(ordinary):
    runtime, service, adapter = ordinary
    args = {"agent_id": "comms", "command_id": "command-1", "trace_id": "mars:test-1"}
    result = await adapter.dispatch(**args, draft="Write a short meeting invitation.")
    assert result["state"] == "active"
    assert result["task_ref"] == "turn:controlled-turn"
    assert service.sent[0]["read_only"] is True
    assert service.sent[0]["direct_user"] is False
    assert await adapter.dispatch(**args, draft="Write a short meeting invitation.") == result
    assert len(service.sent) == 1
    events = await runtime.store.events_for_trace(args["trace_id"])
    assert [event.msg_type for event in events] == [MsgType.ASSIGN, MsgType.CLAIM]
    await service._emit(
        "society:comms",
        {  # noqa: SLF001 - controlled provider fixture
            "kind": "assistant_text",
            "payload": {"turn_id": "controlled-turn", "text": "Draft text"},
        },
    )
    await service._emit(
        "society:comms",
        {  # noqa: SLF001
            "kind": "turn_finished",
            "payload": {"turn_id": "controlled-turn", "status": "done"},
        },
    )
    async with asyncio.timeout(2):
        while True:
            receipt = await adapter.inspect(**args, task_ref=result["task_ref"])
            if receipt["state"] == "completed":
                break
            await asyncio.sleep(0.01)
    assert receipt["result_ref"]
    assert len(service.sent) == 1


async def test_pause_kill_and_wrong_identity_cannot_gain_station_execution(ordinary):
    runtime, service, adapter = ordinary
    with pytest.raises(PermissionError):
        await adapter.authorize(agent_id="unknown", capability_id="communication-draft")
    with pytest.raises(PermissionError):
        await adapter.authorize(agent_id="comms", capability_id="send-email")
    await runtime.store.set_kill_switch(True)
    with pytest.raises(PermissionError):
        await adapter.dispatch(
            agent_id="comms", command_id="halted", trace_id="mars:halted", draft="Draft"
        )
    assert not service.sent


async def test_unrecorded_outcome_never_becomes_success_or_retry(ordinary):
    _, service, adapter = ordinary
    receipt = await adapter.inspect(
        agent_id="comms", command_id="missing", trace_id="mars:missing", task_ref=None
    )
    assert receipt["state"] == "unknown"
    assert not service.sent


async def test_credentials_never_reach_mars_society_or_chat(ordinary, tmp_path, caplog):
    runtime, chat, adapter = ordinary
    credential = "sk-proj-" + "A1" * 20  # Synthetic shape, never a usable credential.
    before = await runtime.store.last_seq()
    path = tmp_path / "not-created" / "mars.db"
    gateway = MarsStationService(MarsStore(path), adapter)
    command = StationCommand(request_id="secret-rejected", draft="Use this key " + credential)
    with pytest.raises(StationError, match="credential_input_use_api_key_settings") as exc:
        await gateway.submit("comms", command)
    assert exc.value.status_code == 422
    assert not path.exists()
    # Even a direct trusted-adapter caller cannot bypass the pre-persistence guard.
    with pytest.raises(StationError, match="credential_input_use_api_key_settings"):
        await adapter.dispatch(
            agent_id="comms",
            command_id="secret-rejected",
            trace_id="mars:secret-rejected",
            draft=command.draft,
        )
    assert await runtime.store.last_seq() == before
    assert chat.store.get_session("society:comms") is None
    assert not chat.sent and not chat.store.list_events("society:comms")
    assert credential not in caplog.text and credential not in str(exc.value)


@pytest.mark.parametrize("terminal,expected", [("done", "completed"), ("cancelled", "canceled")])
async def test_cancel_rechecks_exact_durable_terminal_after_grace_period(
    ordinary, terminal, expected
):
    _, service, adapter = ordinary
    args = {"agent_id": "comms", "command_id": "cancel-race", "trace_id": "mars:cancel-race"}
    active = await adapter.dispatch(**args, draft="Draft a meeting invitation.")
    service.cancel_status = terminal
    outcome = await adapter.cancel(**args, task_ref=active["task_ref"])
    assert outcome["state"] == expected
    assert outcome["cancel_attempt"] == "attempted"
    assert outcome["task_ref"] == "turn:controlled-turn" and outcome["result_ref"]
    assert service.cancel_calls == [("society:comms", "controlled-turn")]
    # A replay reads the recorded terminal and does not issue another stop.
    replay = await adapter.cancel(**args, task_ref=active["task_ref"])
    assert replay["state"] == expected and replay["cancel_attempt"] == "not_attempted"
    assert len(service.cancel_calls) == 1


async def test_true_stop_receipt_without_terminal_cannot_manufacture_canceled(ordinary):
    _, service, adapter = ordinary
    args = {"agent_id": "comms", "command_id": "no-terminal", "trace_id": "mars:no-terminal"}
    active = await adapter.dispatch(**args, draft="Draft a handover.")
    service.cancel_status = None
    outcome = await adapter.cancel(**args, task_ref=active["task_ref"])
    assert outcome["state"] == "active" and outcome["cancel_attempt"] == "attempted"
    assert outcome.get("result_ref") is None


async def test_pending_cancel_recovers_missing_claim_without_repeating_dispatch(ordinary, tmp_path):
    runtime, chat, adapter = ordinary
    gateway = MarsStationService(MarsStore(tmp_path / "deferred-mars.db"), adapter)
    await gateway.start()
    try:
        command = await gateway.store.submit(
            "comms", StationCommand(request_id="deferred", draft="Draft the shift handover.")
        )
        await adapter.authorize(agent_id="comms", capability_id="communication-draft")
        # Test-only crash window: an assignment exists, but its accepted task's
        # durable CLAIM has not been recovered yet. No real provider is invoked.
        runtime.scheduler.detach()
        assignment_id = f"mars-assign:{command.command_id}"
        await runtime.store.append_and_publish(
            SocietyEnvelope(
                event_id=assignment_id,
                msg_type=MsgType.ASSIGN,
                from_agent="user",
                to_agent="comms",
                trace_id=command.trace_id,
                payload={"text": command.draft},
            )
        )
        await gateway.reconcile()
        assert (await gateway.store.get(command.command_id)).task_ref is None
        await gateway.cancel("comms", command.command_id)
        await gateway.reconcile()
        pending = await gateway.store.get(command.command_id)
        assert pending.cancel_requested and not pending.cancel_dispatched
        assert not chat.cancel_calls
        # Existing authority recovers the claim; the queued stop targets that
        # exact recovered turn and never creates a replacement assignment.
        await runtime.store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.CLAIM,
                from_agent="comms",
                to_agent="user",
                trace_id=command.trace_id,
                parent_event_id=assignment_id,
                payload={"run_id": "turn:recovered-turn"},
            )
        )
        runtime.scheduler.note_run_started("turn:recovered-turn", "comms")
        await gateway.reconcile()
        canceled = await gateway.store.get(command.command_id)
        assert canceled.state is CommandState.CANCELED
        assert canceled.task_ref == "turn:recovered-turn"
        assert chat.cancel_calls == [("society:comms", "recovered-turn")]
        assert not chat.sent
        assert (
            sum(
                event.msg_type is MsgType.ASSIGN
                for event in await runtime.store.events_for_trace(command.trace_id)
            )
            == 1
        )
    finally:
        await gateway.close()


async def test_unavailable_chat_service_reports_no_stop_attempt(ordinary):
    runtime, service, adapter = ordinary
    args = {"agent_id": "comms", "command_id": "service-gap", "trace_id": "mars:service-gap"}
    active = await adapter.dispatch(**args, draft="Draft a meeting invitation.")
    original = runtime._get_chat  # noqa: SLF001 - isolated unavailable-service fixture
    runtime._get_chat = lambda: None  # noqa: SLF001
    try:
        unavailable = await adapter.cancel(**args, task_ref=active["task_ref"])
    finally:
        runtime._get_chat = original  # noqa: SLF001
    assert unavailable["cancel_attempt"] == "not_attempted"
    assert not service.cancel_calls
    recovered = await adapter.cancel(**args, task_ref=active["task_ref"])
    assert recovered["state"] == "canceled" and recovered["cancel_attempt"] == "attempted"
    assert len(service.cancel_calls) == 1
