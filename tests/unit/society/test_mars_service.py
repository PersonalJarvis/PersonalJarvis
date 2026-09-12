"""Test-only executor fixtures; these never call a provider or claim live evidence."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.society.mars.models import CommandState, DispatchRejected, StationCommand, StationError
from jarvis.society.mars.service import MarsStationService
from jarvis.society.mars.store import MarsStore


class RecordingExecutor:
    """Test-only trusted-boundary stand-in with inspectable acceptance and cancellation."""

    def __init__(self):
        self.dispatches = []
        self.inspections = []
        self.cancellations = []
        self.denied = set()
        self.receipts = {}
        self.dispatch_error = None
        self.cancel_error = None
        self.hang_dispatch = False
        self.hang_cancel = False
        self.invalid_receipt = False
        self.cancel_unavailable = False

    async def authorize(self, *, agent_id, capability_id):
        assert capability_id == "communication-draft"
        if agent_id in self.denied:
            raise DispatchRejected("permission_denied")

    async def dispatch(self, *, agent_id, command_id, trace_id, draft):
        self.dispatches.append((agent_id, command_id, trace_id, draft))
        self.receipts[command_id] = {"state": "active", "task_ref": f"turn:{command_id}"}
        if self.hang_dispatch:
            await asyncio.Event().wait()
        if self.dispatch_error:
            raise self.dispatch_error
        if self.invalid_receipt:
            return {"state": "completed"}
        return self.receipts[command_id]

    async def inspect(self, *, agent_id, command_id, trace_id, task_ref):
        self.inspections.append((agent_id, command_id, trace_id, task_ref))
        return self.receipts.get(command_id, {"state": "unknown"})

    async def cancel(self, *, agent_id, command_id, trace_id, task_ref):
        if self.cancel_unavailable:
            return {"state": "unknown", "cancel_attempt": "not_attempted"}
        task_ref = task_ref or self.receipts.get(command_id, {}).get("task_ref")
        self.cancellations.append((agent_id, command_id, trace_id, task_ref))
        if self.hang_cancel:
            await asyncio.Event().wait()
        if self.cancel_error:
            raise self.cancel_error
        self.receipts[command_id] = {"state": "canceled", "task_ref": task_ref}
        return self.receipts[command_id]


@pytest.fixture
async def service(tmp_path):
    executor = RecordingExecutor()
    service = MarsStationService(
        MarsStore(tmp_path / "mars.db"),
        executor,
        operation_timeout_s=0.05,
        cancellation_timeout_s=0.05,
    )
    await service.start()
    try:
        yield service, executor
    finally:
        await service.close()


async def settle(service):
    """Drain just the service's owned one-shot work, with no renderer or connection."""
    if service._background:
        await asyncio.gather(*tuple(service._background))


def request(ident="one"):
    return StationCommand(request_id=ident, draft="Draft a short communications handover.")


async def test_submit_uses_trusted_agent_and_accepts_raw_string_receipt(service):
    gateway, executor = service
    queued = await gateway.submit("operator", request())
    await settle(gateway)
    active = await gateway.store.get(queued.command_id)
    assert active.state is CommandState.ACTIVE
    assert active.task_ref == f"turn:{queued.command_id}"
    assert executor.dispatches == [
        ("operator", queued.command_id, queued.trace_id, request().draft)
    ]
    assert active.result_ref is None


async def test_authorization_denial_persists_no_command(service):
    gateway, executor = service
    executor.denied.add("operator")
    with pytest.raises(DispatchRejected, match="permission_denied"):
        await gateway.submit("operator", request())
    assert (await gateway.snapshot()).seq == 0
    assert not executor.dispatches


async def test_credentials_are_rejected_before_start_or_authorization(tmp_path, caplog):
    executor = RecordingExecutor()
    path = tmp_path / "must-not-open.db"
    gateway = MarsStationService(MarsStore(path), executor)
    credential = "sk-proj-" + "A1" * 20
    command = StationCommand(request_id="secret-rejected", draft="Use this key " + credential)
    with pytest.raises(StationError, match="credential_input_use_api_key_settings"):
        await gateway.submit("operator", command)
    assert not path.exists()
    assert not executor.dispatches and not executor.inspections
    assert credential not in caplog.text


async def test_durable_completion_reopens_without_running_again(service):
    gateway, executor = service
    submitted = await gateway.submit("operator", request())
    await settle(gateway)
    executor.receipts[submitted.command_id] = {
        "state": "completed",
        "task_ref": f"turn:{submitted.command_id}",
        "result_ref": "result:actual",
    }
    await gateway.reconcile()
    completed = await gateway.store.get(submitted.command_id)
    assert completed.state is CommandState.COMPLETED
    assert completed.result_ref == "result:actual"
    await gateway.close()
    await gateway.start()
    replay = await gateway.submit("operator", request())
    await settle(gateway)
    assert replay.command_id == completed.command_id
    assert replay.result_ref == completed.result_ref
    assert len(executor.dispatches) == 1


async def test_dispatch_uncertainty_never_repeats_and_reconciles_reference(service):
    gateway, executor = service
    executor.dispatch_error = TimeoutError("private upstream response must not enter event")
    submitted = await gateway.submit("operator", request())
    await settle(gateway)
    unknown = await gateway.store.get(submitted.command_id)
    assert unknown.state is CommandState.UNKNOWN
    assert (await gateway.snapshot()).lease.command_id == submitted.command_id
    executor.receipts[submitted.command_id] = {
        "state": "completed",
        "task_ref": "turn:accepted",
        "result_ref": "result:accepted",
    }
    await gateway.submit("operator", request())
    await settle(gateway)
    assert len(executor.dispatches) == 1
    assert (await gateway.store.get(submitted.command_id)).state is CommandState.COMPLETED
    assert "private upstream response" not in (await gateway.events(0)).model_dump_json()


async def test_dispatch_timeout_and_invalid_success_remain_unknown(service):
    gateway, executor = service
    executor.hang_dispatch = True
    submitted = await gateway.submit("operator", request())
    await settle(gateway)
    assert (await gateway.store.get(submitted.command_id)).state is CommandState.UNKNOWN
    assert len(executor.dispatches) == 1
    executor.hang_dispatch = False
    executor.receipts[submitted.command_id] = {"state": "failed"}
    await gateway.reconcile()
    executor.invalid_receipt = True
    other = await gateway.submit("operator", request("other"))
    await settle(gateway)
    assert (await gateway.store.get(other.command_id)).state is CommandState.UNKNOWN


async def test_known_preaccept_refusal_fails_without_claiming_success(service):
    gateway, executor = service
    executor.dispatch_error = DispatchRejected("budget_exhausted")
    submitted = await gateway.submit("operator", request())
    await settle(gateway)
    record = await gateway.store.get(submitted.command_id)
    assert record.state is CommandState.FAILED and record.reason == "budget_exhausted"
    assert (await gateway.snapshot()).lease is None


async def test_revoked_queued_agent_never_dispatches(service):
    gateway, executor = service
    first = await gateway.submit("one", request("one"))
    await settle(gateway)
    second = await gateway.submit("two", request("two"))
    await settle(gateway)
    executor.denied.add("two")
    executor.receipts[first.command_id] = {"state": "failed"}
    await gateway.reconcile()
    await gateway.reconcile()
    assert len(executor.dispatches) == 1
    rejected = await gateway.store.get(second.command_id)
    assert rejected.state is CommandState.FAILED and rejected.reason == "permission_denied"


async def test_owned_cancel_remains_available_after_grant_revocation(service):
    gateway, executor = service
    first = await gateway.submit("operator", request())
    await settle(gateway)
    executor.denied.add("operator")
    with pytest.raises(StationError, match="command_not_found"):
        await gateway.cancel("other-agent", first.command_id)
    await gateway.cancel("operator", first.command_id)
    await settle(gateway)
    assert len(executor.cancellations) == 1
    assert executor.cancellations[0][-1] == f"turn:{first.command_id}"
    assert (await gateway.store.get(first.command_id)).state is CommandState.CANCELED
    await gateway.cancel("operator", first.command_id)
    await settle(gateway)
    assert len(executor.cancellations) == 1


async def test_unknown_cancellation_is_not_reissued_on_tick_or_restart(service):
    gateway, executor = service
    first = await gateway.submit("operator", request())
    await settle(gateway)
    executor.cancel_error = TimeoutError("lost cancellation response")
    await gateway.cancel("operator", first.command_id)
    await settle(gateway)
    assert (await gateway.store.get(first.command_id)).state is CommandState.UNKNOWN
    await gateway.reconcile()
    await gateway.close()
    await gateway.start()
    await gateway.reconcile()
    assert len(executor.cancellations) == 1
    assert len(executor.dispatches) == 1
    assert (await gateway.store.get(first.command_id)).cancel_requested


async def test_not_attempted_cancellation_retries_after_prerequisite_recovers(service):
    gateway, executor = service
    # Simulate acceptance whose task reference was lost before the dispatch receipt.
    executor.dispatch_error = TimeoutError("lost acceptance response")
    first = await gateway.submit("operator", request())
    await settle(gateway)
    assert (await gateway.store.get(first.command_id)).task_ref is None
    executor.cancel_unavailable = True
    await gateway.cancel("operator", first.command_id)
    await settle(gateway)
    pending = await gateway.store.get(first.command_id)
    assert pending.cancel_requested and not pending.cancel_dispatched
    assert not executor.cancellations
    await gateway.close()
    await gateway.start()
    executor.cancel_unavailable = False
    # Reconcile discovers the durable task reference and the pending stop is attempted once.
    executor.receipts[first.command_id] = {"state": "active", "task_ref": "turn:recovered"}
    await gateway.reconcile()
    assert len(executor.cancellations) == 1
    assert len(executor.dispatches) == 1
    assert executor.cancellations[0][-1] == "turn:recovered"
    assert (await gateway.store.get(first.command_id)).state is CommandState.CANCELED


async def test_two_clients_submit_one_request_and_close_without_stopping_work(service):
    gateway, executor = service
    clients = await asyncio.gather(
        gateway.submit("operator", request()), gateway.submit("operator", request())
    )
    await settle(gateway)
    assert clients[0].command_id == clients[1].command_id
    # No client or scene objects exist. Backend reconciliation observes actual executor evidence.
    executor.receipts[clients[0].command_id] = {
        "state": "completed",
        "task_ref": f"turn:{clients[0].command_id}",
        "result_ref": "result:headless",
    }
    await gateway.reconcile()
    snapshot = await gateway.snapshot()
    assert snapshot.commands[0].state is CommandState.COMPLETED
    assert len(executor.dispatches) == 1
