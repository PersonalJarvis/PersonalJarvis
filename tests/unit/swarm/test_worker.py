"""Worker accounting, provider quarantine, bounded output, and opaque history."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from decimal import Decimal
from types import SimpleNamespace

import pytest

from jarvis.brain.swarm_factory import SwarmProvider
from jarvis.control.cancel import CancelToken
from jarvis.core.protocols import BrainDelta, BrainMessage, ToolResult
from jarvis.core.swarm_types import SwarmActor, SwarmController
from jarvis.swarm.worker import ProviderUnavailableError, WorkerEngine


class RecordingStore:
    def __init__(self):
        self.reservations = []
        self.reconciliations = []
        self.events = []
        self.fail_reconcile = False

    def get(self):
        return {
            "goal": "Produce a useful result",
            "limits": {
                "max_output_tokens": 128,
                "max_tool_calls": 4,
                "monetary_limit_microusd": None,
            },
        }

    def reserve(self, actor, key, tokens, cost):
        assert len(key) <= 100
        self.reservations.append((key, tokens, cost))
        return {"id": str(len(self.reservations))}

    def reconcile(self, controller, reservation_id, tokens, cost):
        if self.fail_reconcile:
            raise RuntimeError("database unavailable")
        self.reconciliations.append((reservation_id, tokens, cost))

    def append_event(self, *args, **kwargs):
        self.events.append(kwargs)


class ScriptedBrain:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        for item in next(self.responses):
            if isinstance(item, BaseException):
                raise item
            yield item


def engine_for(brain, *, task_id="task", errors=None):
    store = RecordingStore()
    engine = WorkerEngine(
        store,
        SwarmActor("team", "worker", "opaque", task_id, 7),
        SwarmController("team", "instance", 3, "opaque"),
        SwarmProvider(brain, "test-api", "test-model", Decimal("2")),
        CancelToken(),
        on_provider_error=errors.append if errors is not None else None,
    )
    return engine, store


@pytest.mark.asyncio
async def test_usage_after_finish_merges_cumulative_counts_and_all_cache_exposure():
    brain = ScriptedBrain(
        [
            BrainDelta(usage={"input_tokens": 50, "cache_hit_tokens": 20}),
            BrainDelta(content="Result", finish_reason="stop"),
            BrainDelta(usage={"output_tokens": 8, "cache_creation_tokens": 12}),
        ]
    )
    engine, store = engine_for(brain)
    assert await engine.completion([], "system") == ("Result", [])
    assert store.reconciliations == [("1", "90", "180")]
    assert store.events[-1]["data"]["usage_known"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage,finish",
    [
        ({"output_tokens": 8}, "stop"),
        ({"input_tokens": 50}, "stop"),
        ({"input_tokens": 50, "output_tokens": 0}, None),
        ({"input_tokens": True, "output_tokens": 8}, "stop"),
        ({"input_tokens": -1, "output_tokens": 8}, "stop"),
    ],
)
async def test_partial_or_unfinished_usage_never_refunds_unknown_exposure(usage, finish):
    engine, store = engine_for(
        ScriptedBrain(
            [
                BrainDelta(content="Partial result", usage=usage, finish_reason=finish),
            ]
        )
    )
    await engine.completion([], "system")
    assert store.reconciliations == [("1", None, None)]
    assert store.events[-1]["data"]["usage_known"] is False


@pytest.mark.asyncio
async def test_maximum_task_id_has_bounded_deterministic_noncolliding_request_keys():
    finished = [BrainDelta(content="Result", finish_reason="stop")]
    engine, store = engine_for(ScriptedBrain(finished, finished), task_id="x" * 100)
    await engine.completion([], "system", phase="verification" * 30)
    await engine.completion([], "system", phase="verification" * 30)
    keys = [row[0] for row in store.reservations]
    assert all(len(key) == 64 for key in keys)
    assert keys[0] != keys[1]
    replay, replay_store = engine_for(ScriptedBrain(finished), task_id="x" * 100)
    await replay.completion([], "system", phase="verification" * 30)
    assert replay_store.reservations[0][0] == keys[0]


@pytest.mark.asyncio
async def test_only_provider_failure_triggers_cooldown_and_reservation_is_retained():
    errors = []
    engine, store = engine_for(
        ScriptedBrain([RuntimeError("upstream rejected request")]), errors=errors
    )
    with pytest.raises(RuntimeError, match="Provider request failed"):
        await engine.completion([], "system")
    assert errors == ["test-api"]
    assert store.reservations and not store.reconciliations


@pytest.mark.asyncio
async def test_cancellation_does_not_quarantine_provider():
    errors = []
    engine, store = engine_for(ScriptedBrain([asyncio.CancelledError()]), errors=errors)
    with pytest.raises(asyncio.CancelledError):
        await engine.completion([], "system")
    assert errors == []
    assert store.reservations and not store.reconciliations


@pytest.mark.asyncio
async def test_database_failure_does_not_quarantine_provider():
    errors = []
    engine, store = engine_for(
        ScriptedBrain(
            [
                BrainDelta(
                    content="Result",
                    finish_reason="stop",
                    usage={"input_tokens": 1, "output_tokens": 1},
                ),
            ]
        ),
        errors=errors,
    )
    store.fail_reconcile = True
    with pytest.raises(RuntimeError, match="database"):
        await engine.completion([], "system")
    assert errors == []


@pytest.mark.asyncio
async def test_tool_failure_does_not_quarantine_provider():
    errors = []
    brain = ScriptedBrain([BrainDelta(tool_call={"id": "1", "name": "read_artifact", "input": {}})])
    engine, _ = engine_for(brain, errors=errors)

    async def execute(name, args, call_id):
        raise RuntimeError("tool rejected unavailable artifact")

    with pytest.raises(RuntimeError, match="tool rejected"):
        await engine.run({}, tools=(), execute=execute, context={})
    assert errors == []


@pytest.mark.asyncio
async def test_worker_replays_opaque_thinking_metadata_without_handing_it_to_tools():
    metadata = {
        "thought_signature": "c2lnbmF0dXJl",
        "extra_content": {"google": {"opaque": "value"}},
    }
    brain = ScriptedBrain(
        [
            BrainDelta(
                tool_call={
                    "id": "1",
                    "name": "read_artifact",
                    "arguments": '{"id":"a"}',
                    **metadata,
                }
            )
        ],
        [BrainDelta(content="Completed result", finish_reason="stop")],
    )
    engine, _ = engine_for(brain)
    executions = []

    async def execute(name, args, call_id):
        executions.append((name, args, call_id))
        return ToolResult(True, "Evidence")

    result = await engine.run({}, tools=(), execute=execute, context={})
    assert result.text == "Completed result"
    assert executions == [("read_artifact", {"id": "a"}, "1")]
    assistant = next(
        message for message in brain.requests[1].messages if message.role == "assistant"
    )
    assert assistant.content[0] == {
        "type": "tool_use",
        "id": "1",
        "name": "read_artifact",
        "input": {"id": "a"},
        **metadata,
    }


@pytest.mark.asyncio
async def test_tool_arguments_and_metadata_share_the_total_provider_output_byte_limit():
    payload = "x" * 700_000
    brain = ScriptedBrain(
        [
            BrainDelta(
                tool_call={"id": str(i), "name": "write_artifact", "input": {"text": payload}}
            )
            for i in range(3)
        ]
    )
    engine, store = engine_for(brain)
    with pytest.raises(ValueError, match="bounded task output"):
        await engine.completion([BrainMessage(role="user", content="Make an artifact")], "system")
    assert not store.reconciliations


class ProviderHTTPError(Exception):
    def __init__(self, status):
        self.status_code = status
        self.response = SimpleNamespace(headers={"retry-after": "0"})


@pytest.mark.asyncio
async def test_quota_wait_retries_completion_with_one_reservation_per_actual_request():
    errors = []
    engine, store = engine_for(
        ScriptedBrain(
            [ProviderHTTPError(429)],
            [
                BrainDelta(
                    content="Accepted",
                    finish_reason="stop",
                    usage={"input_tokens": 2, "output_tokens": 3},
                )
            ],
        ),
        errors=errors,
    )
    admissions = []

    async def ready(name):
        admissions.append(len(store.reservations))

    async def recover(provider, exc):
        assert exc.status_code == 429
        assert store.reconciliations == [("1", "0", "0")]
        return provider

    engine.await_provider_ready = ready
    engine.recover_provider = recover
    assert await engine.completion([], "system") == ("Accepted", [])
    assert admissions == [0, 1]
    assert len(store.reservations) == 2
    assert store.reconciliations == [("1", "0", "0"), ("2", "5", "10")]
    assert errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix",
    [
        [BrainDelta(content="Partial")],
        [BrainDelta(usage={"input_tokens": 5})],
        [BrainDelta(usage={})],
        [BrainDelta(tool_call={"name": "read"})],
        [BrainDelta(finish_reason="stop")],
    ],
)
async def test_quota_error_after_partial_response_keeps_full_exposure(prefix):
    engine, store = engine_for(ScriptedBrain([*prefix, ProviderHTTPError(429)]))
    with pytest.raises(ProviderUnavailableError):
        await engine.completion([], "system")
    assert store.reconciliations == [("1", None, None)]


@pytest.mark.asyncio
async def test_network_failure_without_output_is_still_ambiguous_exposure():
    engine, store = engine_for(ScriptedBrain([ConnectionError("connection lost")]))
    with pytest.raises(ProviderUnavailableError):
        await engine.completion([], "system")
    assert store.reconciliations == [("1", None, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 404])
async def test_authentication_rejection_is_not_a_failed_task_verification(status):
    engine, store = engine_for(ScriptedBrain([ProviderHTTPError(status)]))
    with pytest.raises(ProviderUnavailableError):
        await engine.completion([], "system")
    assert store.reconciliations == [("1", "0", "0")]


@pytest.mark.asyncio
async def test_cancel_interrupts_shared_cooldown_without_creating_a_reservation():
    engine, store = engine_for(ScriptedBrain())
    waiting = asyncio.Event()

    async def ready(name):
        waiting.set()
        await asyncio.Event().wait()

    engine.await_provider_ready = ready
    operation = asyncio.create_task(engine.completion([], "system"))
    await asyncio.wait_for(waiting.wait(), 1)
    engine.cancel.cancel("stop")
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, 1)
    assert not store.reservations


@pytest.mark.asyncio
async def test_provider_wait_cannot_expand_team_runtime_budget():
    engine, store = engine_for(ScriptedBrain())
    original_get = store.get

    def get():
        team = original_get()
        team["started_at"] = time.time() - 0.99
        team["limits"]["runtime_seconds"] = 1
        return team

    async def ready(name):
        await asyncio.Event().wait()

    store.get = get
    engine.await_provider_ready = ready
    with pytest.raises(ProviderUnavailableError, match="bounded request/runtime"):
        await asyncio.wait_for(engine.completion([], "system"), 1)
    assert not store.reservations


@pytest.mark.asyncio
async def test_worker_refreshes_catalog_after_registering_a_tool():
    engine, _ = engine_for(
        ScriptedBrain(
            [BrainDelta(tool_call={"id": "1", "name": "register_team_tool", "input": {}})],
            [BrainDelta(content="Complete", finish_reason="stop")],
        )
    )
    names = ["register_team_tool"]

    async def catalog():
        return tuple({"name": name} for name in names)

    async def execute(name, args, call_id):
        names.append("created_tool")
        return ToolResult(True, "Registered")

    await engine.run({}, tools=(), execute=execute, context={}, catalog=catalog)
    assert engine.provider.brain.requests[0].tools == ({"name": "register_team_tool"},)
    assert engine.provider.brain.requests[1].tools == (
        {"name": "register_team_tool"},
        {"name": "created_tool"},
    )


@pytest.mark.asyncio
async def test_supplied_first_catalog_is_reused_then_refreshed_after_execution():
    engine, _ = engine_for(
        ScriptedBrain(
            [BrainDelta(tool_call={"id": "1", "name": "register_team_tool", "input": {}})],
            [BrainDelta(content="Complete", finish_reason="stop")],
        )
    )
    refreshes = []

    async def catalog():
        refreshes.append(True)
        return ({"name": "register_team_tool"}, {"name": "created_tool"})

    async def execute(name, args, call_id):
        assert not refreshes  # The caller already provided this first-turn catalog.
        return ToolResult(True, "Registered")

    await engine.run(
        {}, tools=({"name": "register_team_tool"},), execute=execute, context={}, catalog=catalog
    )
    assert len(refreshes) == 1
    assert engine.provider.brain.requests[0].tools == ({"name": "register_team_tool"},)
    assert engine.provider.brain.requests[1].tools[-1]["name"] == "created_tool"


@pytest.mark.asyncio
async def test_initial_team_snapshot_is_refreshed_after_tool_execution():
    engine, store = engine_for(
        ScriptedBrain(
            [BrainDelta(tool_call={"id": "1", "name": "work", "input": {}})],
            [BrainDelta(content="Complete", finish_reason="stop")],
        )
    )
    initial = store.get()
    initial["limits"]["max_output_tokens"] = 64

    async def execute(name, args, call_id):
        return ToolResult(True, "Done")

    await engine.run(
        {},
        tools=({"name": "work"},),
        execute=execute,
        context={},
        initial_team=initial,
    )
    assert [request.max_tokens for request in engine.provider.brain.requests] == [64, 128]
    assert len(store.reservations) == 2


@pytest.mark.asyncio
async def test_initial_team_snapshot_is_refreshed_after_provider_recovery():
    engine, store = engine_for(
        ScriptedBrain(
            [ProviderHTTPError(429)],
            [BrainDelta(content="Complete", finish_reason="stop")],
        )
    )
    initial = store.get()
    initial["limits"]["max_output_tokens"] = 64

    async def recover(provider, exc):
        return provider

    engine.recover_provider = recover
    assert await engine.completion([], "work", initial_team=initial) == ("Complete", [])
    assert [request.max_tokens for request in engine.provider.brain.requests] == [64, 128]
    assert len(store.reservations) == 2


@pytest.mark.asyncio
async def test_recovery_replacements_are_closed_idempotently():
    class AsyncClient:
        def __init__(self):
            self.closed = 0

        async def close(self):
            self.closed += 1

    first_client, second_client = AsyncClient(), AsyncClient()
    first = ScriptedBrain([ProviderHTTPError(429)])
    first._client = first_client
    second = ScriptedBrain([BrainDelta(content="Recovered", finish_reason="stop")])
    second._client = second_client
    engine, _ = engine_for(first)
    replacement = SwarmProvider(second, "other-api", "test-model", Decimal("2"))

    async def recover(provider, exc):
        return replacement

    engine.recover_provider = recover
    assert await engine.completion([], "system") == ("Recovered", [])
    await engine.aclose()
    await engine.aclose()
    assert first_client.closed == second_client.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, 400, 401, 422, 429, 503])
async def test_provider_bodies_never_reach_exceptions_or_debug_logs(status, caplog):
    caplog.set_level(logging.DEBUG)
    failure = RuntimeError("private-provider-response-body")
    failure.status_code = status
    failure.__cause__ = ValueError("private-provider-chained-body")
    engine, store = engine_for(ScriptedBrain([failure]))
    with pytest.raises(RuntimeError) as caught:
        await engine.completion([], "system")
    rendered = "".join(traceback.format_exception(caught.value))
    for private in ("private-provider-response-body", "private-provider-chained-body"):
        assert private not in rendered
        assert private not in caplog.text
        assert private not in str(store.events)
    if status in {401, 429}:
        assert store.reconciliations == [("1", "0", "0")]
    elif status == 503:
        assert store.reconciliations == [("1", None, None)]
    else:
        assert not store.reconciliations
        if status is not None:
            assert f"HTTP {status}" in str(caught.value)


@pytest.mark.asyncio
async def test_accounting_failure_during_provider_rejection_drops_response_chain(caplog):
    caplog.set_level(logging.DEBUG)
    failure = RuntimeError("private-provider-response-body")
    failure.status_code = 429
    engine, store = engine_for(ScriptedBrain([failure]))
    store.fail_reconcile = True
    with pytest.raises(RuntimeError, match="database unavailable") as caught:
        await engine.completion([], "system")
    assert "private-provider-response-body" not in "".join(traceback.format_exception(caught.value))
    assert "private-provider-response-body" not in caplog.text


@pytest.mark.asyncio
async def test_recovery_state_failure_does_not_format_original_error_from_retry_args(caplog):
    caplog.set_level(logging.DEBUG)
    failure = RuntimeError("private-provider-retry-body")
    failure.status_code = 429
    engine, store = engine_for(ScriptedBrain([failure]))

    def set_execution_state(actor, state, reason):
        raise RuntimeError("storage projection unavailable")

    async def recover(provider, exc):
        return provider

    store.set_execution_state = set_execution_state
    engine.recover_provider = recover
    with pytest.raises(RuntimeError, match="storage projection unavailable") as caught:
        await engine.completion([], "system")
    assert "private-provider-retry-body" not in "".join(traceback.format_exception(caught.value))
    assert "private-provider-retry-body" not in caplog.text


@pytest.mark.asyncio
async def test_recovery_callback_failure_never_exposes_a_provider_body(caplog):
    caplog.set_level(logging.DEBUG)
    engine, _ = engine_for(ScriptedBrain([ProviderHTTPError(429)]))

    async def recover(provider, exc):
        raise RuntimeError("private-recovery-provider-body")

    engine.recover_provider = recover
    with pytest.raises(ProviderUnavailableError, match="Provider recovery failed") as caught:
        await engine.completion([], "system")
    assert "private-recovery-provider-body" not in "".join(traceback.format_exception(caught.value))
    assert "private-recovery-provider-body" not in caplog.text


@pytest.mark.asyncio
async def test_failure_callback_exception_is_logged_without_its_body(caplog):
    caplog.set_level(logging.DEBUG)
    engine, _ = engine_for(ScriptedBrain([ValueError("private-original-provider-body")]))

    def failed(provider):
        raise RuntimeError("private-callback-provider-body")

    engine.on_provider_error = failed
    with pytest.raises(RuntimeError, match="Provider request failed") as caught:
        await engine.completion([], "system")
    rendered = "".join(traceback.format_exception(caught.value)) + caplog.text
    assert "private-original-provider-body" not in rendered
    assert "private-callback-provider-body" not in rendered
    assert "Swarm provider failure callback failed" in caplog.text
