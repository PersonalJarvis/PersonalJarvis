"""Synthetic three-worker proof using the production scheduler, tools and durable control queue."""

import asyncio
import json
from decimal import Decimal

import pytest

from jarvis.brain.swarm_factory import SwarmProvider
from jarvis.core.protocols import BrainDelta
from jarvis.core.swarm_types import BudgetLimits, TaskSpec, TeamCreate
from jarvis.swarm.control_requests import acknowledge, drain, enqueue
from tests.fakes.swarm_runtime import runtime, terminal


class CoordinatingBrain:
    def __init__(self, factory):
        self.factory = factory

    async def complete(self, request):
        payload = json.loads(request.messages[0].content)
        if "Independently verify" in request.system:
            yield BrainDelta(
                content=json.dumps(
                    {
                        "accepted": True,
                        "reason": "Synthetic grader checked the durable peer-work fixture",
                    }
                )
            )
            yield BrainDelta(finish_reason="stop", usage={"input_tokens": 20, "output_tokens": 10})
            return
        task = payload["task"]
        tools = [message for message in request.messages if message.role == "tool"]
        for message in tools:
            assert json.loads(message.content)["success"], message.content
        calls = []
        text = ""
        if task["id"].startswith("__swarm_supervise_" + self.factory.coordinator_id):
            allowed = next(tool for tool in request.tools if tool["name"] == "request_control")
            assert set(allowed["input_schema"]["properties"]["operation"]["enum"]) == {
                "report_summary",
                "request_reconciliation",
            }
            stage = len(tools)
            inbox = json.loads(tools[0].content)["output"] if tools else []
            evidence = [message["id"] for message in inbox]
            if stage == 0:
                calls = [("read_messages", {"limit": 16})]
            elif stage == 1:
                calls = [
                    (
                        "request_control",
                        {
                            "operation": "report_summary",
                            "payload": {
                                "summary": "Three workers claimed work; findings conflict.",
                                "task_ids": list(self.factory.workers),
                                "evidence": evidence,
                                "reason": "Report bounded group evidence to the persistent lead",
                            },
                        },
                    )
                ]
            elif stage == 2:
                calls = [
                    (
                        "send_message",
                        self.factory.message(
                            "third",
                            "REQUEST_PROGRESS",
                            [self.factory.workers["third"]],
                            "Please explain the conflicting finding with original evidence IDs",
                        ),
                    )
                ]
            elif stage == 3:
                calls = [
                    (
                        "request_control",
                        {
                            "operation": "request_reconciliation",
                            "payload": {
                                "summary": "Findings need independent reconciliation.",
                                "task_ids": list(self.factory.workers),
                                "evidence": evidence,
                                "reason": "Preserve the disagreement and inspect original evidence",
                            },
                        },
                    )
                ]
            else:
                text = (
                    "Group summary and reconciliation have been queued with original message IDs."
                )
        elif task["id"].startswith("__swarm_supervise_"):
            text = (
                "The group coordinator is inspecting worker evidence; retain current assignments."
            )
        elif task["id"] in self.factory.workers:
            if not tools:
                target = [self.factory.coordinator_id]
                intents = {
                    "first": "REQUEST_PROGRESS",
                    "second": "SHARE_FINDING",
                    "third": "CONFLICT",
                }
                calls = [
                    (
                        "send_message",
                        self.factory.message(
                            task["id"],
                            "CLAIM_WORK",
                            target,
                            "I have claimed the assigned group task",
                        ),
                    ),
                    (
                        "send_message",
                        self.factory.message(
                            task["id"],
                            intents[task["id"]],
                            target,
                            "Synthetic peer evidence for group reconciliation",
                        ),
                    ),
                ]
            else:
                self.factory.active_workers.add(task["id"])
                if len(self.factory.active_workers) == 3:
                    self.factory.all_workers.set()
                await self.factory.release.wait()
                text = json.dumps(
                    {"task": task["id"], "report": "Durable claim and peer exchange recorded"}
                )
        elif task["id"].startswith("reconcile_"):
            if not tools:
                calls = [("search_team", {"query": "reconciliation", "kind": "messages"})]
            else:
                text = json.dumps(
                    {
                        "reconciliation": "Claims remain unverified; retain the disagreement.",
                        "original_contract_and_evidence": task["description"],
                        "observed_messages": json.loads(tools[0].content)["output"],
                    }
                )
        else:
            raise AssertionError("Unexpected synthetic task: " + task["id"])
        for index, (name, arguments) in enumerate(calls):
            yield BrainDelta(
                tool_call={"id": f"step-{len(tools)}-{index}", "name": name, "input": arguments}
            )
        if text:
            yield BrainDelta(content=text)
        yield BrainDelta(
            finish_reason="tool_calls" if calls else "stop",
            usage={"input_tokens": 20, "output_tokens": 10},
        )


class CoordinationFactory:
    def __init__(self):
        self.workers = {}
        self.coordinator_id = ""
        self.all_workers = asyncio.Event()
        self.release = asyncio.Event()
        self.active_workers = set()

    async def create(self):
        return SwarmProvider(CoordinatingBrain(self), "synthetic", "synthetic", Decimal("1"))

    def failed(self, provider):
        raise AssertionError("Synthetic provider must not fail")

    @staticmethod
    def message(task_id, intent, recipients, summary):
        return {
            "task_id": task_id,
            "intent": intent,
            "recipients": recipients,
            "summary": summary,
            "request_key": "runtime-replaces-this-key",
        }


@pytest.mark.asyncio
async def test_three_assigned_workers_and_persistent_coordinator_complete_durable_peer_demo(
    tmp_path,
):
    service = runtime(tmp_path)
    factory = CoordinationFactory()
    service.brain_factory = factory
    team = service.registry.create(
        TeamCreate(
            name="Three-worker coordination",
            goal="Exchange peer findings and reconcile disagreement",
            request_key="three-worker-proof",
            # Three blocked workers and one active coordinator still reserve
            # a separate slot for the persistent lead.
            limits=BudgetLimits(concurrency=5, worker_limit="4"),
            tasks=[
                TaskSpec(
                    id=task_id,
                    title=task_id,
                    description="Exchange scoped peer findings",
                    acceptance="Record a claim and peer exchange",
                    milestone="research",
                )
                for task_id in ("first", "second", "third")
            ],
        )
    )
    store = service.registry.open(team["id"])
    controller = await service._controller(store)
    store.transition(controller, "running")
    lead = store.actor_for(controller, team["lead_id"])

    def apply(operation, key, **payload):
        request = enqueue(
            store, lead, operation, {"reason": "Run authorized group work", **payload}, key
        )
        records = drain(store, controller)
        record = next(record for record in records if record["id"] == request["id"])
        assert record["state"] == "applied", record
        acknowledge(store, controller, record["id"])
        return record["outcome"]

    factory.coordinator_id = apply("spawn_coordinator", "group-coordinator", group_id="research")[
        "agent_id"
    ]
    for task_id in ("first", "second", "third"):
        worker = apply("spawn_worker", "worker-" + task_id, name=task_id, group_id="research")
        factory.workers[task_id] = worker["agent_id"]
        apply("assign_task", "assign-" + task_id, task_id=task_id, agent_id=worker["agent_id"])
    try:
        await service.start()
        await asyncio.wait_for(factory.all_workers.wait(), 5)
        async with asyncio.timeout(12):
            while True:
                decisions = await service.records(team["id"], "decisions", limit=200)
                applied = [
                    record
                    for record in decisions
                    if record.get("actor_id") == factory.coordinator_id
                    and record.get("state") == "applied"
                ]
                if {record["operation"] for record in applied} >= {
                    "report_summary",
                    "request_reconciliation",
                }:
                    break
                await asyncio.sleep(0.02)
        assert not factory.release.is_set()
        messages = await service.records(team["id"], "messages", limit=100)
        assert {message["intent"] for message in messages} >= {
            "CLAIM_WORK",
            "REQUEST_PROGRESS",
            "SHARE_FINDING",
            "CONFLICT",
        }
        assert any(
            message["sender_id"] == factory.coordinator_id
            and message["intent"] == "REQUEST_PROGRESS"
            for message in messages
        )
        tasks = await service.records(team["id"], "tasks", limit=100)
        for task_id, worker_id in factory.workers.items():
            task = next(task for task in tasks if task["id"] == task_id)
            assert task["owner_id"] == worker_id, {
                "task": task,
                "team": await service.team(team["id"]),
            }
        assert (
            len(
                [
                    agent
                    for agent in await service.records(team["id"], "agents")
                    if agent["role"] == "worker"
                ]
            )
            == 3
        )
        factory.release.set()
        final = await terminal(service, team["id"], timeout=15)
        assert final["state"] == "succeeded", final
        coordinator = store.get_record("agents", factory.coordinator_id)
        assert coordinator["verified_tasks"] == "0"
        assert not any(
            item["agent_id"] == factory.coordinator_id for item in store.records("reputation")
        )
        assert final["lead_id"] == lead.agent_id
        assert int(final["tokens_used"]) > 0
    finally:
        factory.release.set()
        await service.stop()
