"""Synthetic Brain and real-tool composition for Swarm lifecycle integration tests."""

import asyncio
import json
from decimal import Decimal

from jarvis.brain.swarm_factory import SwarmProvider
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.protocols import BrainDelta
from jarvis.core.swarm_types import SandboxResult
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor
from jarvis.swarm.service import LocalSwarmService


class SyntheticBrain:
    def __init__(self, factory):
        self.factory = factory

    async def complete(self, request):
        self.factory.started.set()
        if self.factory.gate is not None:
            await self.factory.gate.wait()
        payload = json.loads(request.messages[0].content)
        if "Independently verify" in request.system:
            result = json.loads(payload["proposed_result"])
            accepted = result["value"] == int(payload["original_task"]["description"])
            text = json.dumps({"accepted": accepted, "reason": "Synthetic exact-value grader"})
        elif "persistent goal planner" in request.system:
            text = json.dumps(
                {
                    "decision": "deliver",
                    "tasks": [],
                    "summary": "All fixture work is accepted",
                    "reason": "The goal is satisfied",
                    "remaining_decomposition": False,
                }
            )
        elif "persistent team lead" in request.system:
            value = int(payload["goal"])
            text = json.dumps(
                {
                    "tasks": [
                        {
                            "id": name,
                            "title": name,
                            "description": str(value),
                            "acceptance": f"Return value {value}",
                            "domain": "general",
                        }
                        for name in ("first", "second")
                    ]
                }
            )
        elif payload.get("task", {}).get("id", "").startswith("__swarm_supervise_"):
            text = "Reviewed active fixture work; no intervention is needed."
        elif request.messages[-1].role == "tool":
            tool = json.loads(request.messages[-1].content)
            assert tool["success"], tool
            text = json.dumps(tool["output"]["output"])
        else:
            value = int(payload["task"]["description"])
            yield BrainDelta(
                tool_call={
                    "id": "compute",
                    "name": "run_javascript",
                    "input": {
                        "script": "function main(input) { return {value: input.value}; }",
                        "inputs": {"value": value},
                    },
                }
            )
            yield BrainDelta(
                finish_reason="tool_calls", usage={"input_tokens": 30, "output_tokens": 20}
            )
            return
        yield BrainDelta(content=text)
        yield BrainDelta(finish_reason="stop", usage={"input_tokens": 30, "output_tokens": 20})


class SyntheticFactory:
    def __init__(self, gate=None):
        self.gate = gate
        self.started = asyncio.Event()
        self.failures = []

    async def create(self):
        return SwarmProvider(SyntheticBrain(self), "synthetic", "synthetic", Decimal("1"))

    def failed(self, provider):
        self.failures.append(provider)

    async def catalog(self):
        return [{"id": "synthetic", "model": "synthetic", "available": True}]


class SyntheticSandbox:
    async def run(self, script, inputs, *, timeout_s=10, cancel=None):
        if cancel and cancel():
            raise asyncio.CancelledError
        return SandboxResult(inputs, "", "", 0, 1)


class NoNetwork:
    async def fetch(self, *args, **kwargs):
        raise AssertionError("This synthetic fixture must not access the network")

    async def aclose(self):
        pass


def runtime(root, *, gate=None, sandbox=None):
    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus))
    return LocalSwarmService(
        root,
        brain_factory=SyntheticFactory(gate),
        sandbox=sandbox or SyntheticSandbox(),
        tool_executor=executor,
        egress=NoNetwork(),
        dependencies=None,
    )


async def terminal(service, team_id, timeout=20):  # noqa: ASYNC109 - bounded polling fixture
    async with asyncio.timeout(timeout):
        while True:
            team = await service.team(team_id)
            if team["state"] in {"succeeded", "failed", "blocked", "canceled"}:
                return team
            await asyncio.sleep(0.05)
