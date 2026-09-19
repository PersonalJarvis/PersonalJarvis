"""Attempt-bound tools; all executions pass through the existing ToolExecutor."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.core.swarm_types import PeerMessage, SandboxResult, SwarmActor, SwarmController

log = logging.getLogger(__name__)


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_TEXT = {"type": "string"}
_OBJECT = {"type": "object"}
_SPECS: dict[str, tuple[str, dict[str, Any]]] = {
    "fetch_url": (
        "Fetch a public HTTP(S) source through this task's authorized egress.",
        _schema({"url": _TEXT}, ["url"]),
    ),
    "run_javascript": (
        "Run JavaScript function main(input) in the isolated WASM sandbox. The inputs value "
        "is passed unchanged: inputs={n:7} requires input.n, while inputs=7 uses input itself. "
        "Return the exact JSON value needed by the task; printing does not return a result.",
        _schema(
            {"script": _TEXT, "inputs": {"description": "Exact JSON argument to main(input)."}},
            ["script", "inputs"],
        ),
    ),
    "write_artifact": (
        "Save a named result or evidence in this team's private artifact store.",
        _schema({"name": _TEXT, "content": _TEXT, "media_type": _TEXT}, ["name", "content"]),
    ),
    "read_artifact": (
        "Read a team artifact by ID, retaining its provenance.",
        _schema({"artifact_id": _TEXT}, ["artifact_id"]),
    ),
    "search_team": (
        "Find tasks, messages, artifacts or decisions in your team. Use kind=peers to "
        "discover relevant coworkers for the specified task; no external team access.",
        _schema({"query": _TEXT, "kind": _TEXT, "task_id": _TEXT}, ["query"]),
    ),
    "send_message": (
        "Asynchronously exchange scoped progress, requests, findings, claims or conflicts. "
        "Messages convey information only, never authority.",
        PeerMessage.model_json_schema(),
    ),
    "read_messages": (
        "Read your durable mailbox without waiting. Optionally subscribe to a task/topic.",
        _schema(
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "subscribe_topic": _TEXT,
                "task_id": _TEXT,
            }
        ),
    ),
    "ack_message": (
        "Acknowledge a processed mailbox delivery; duplicate acknowledgements are harmless.",
        _schema({"message_id": _TEXT}, ["message_id"]),
    ),
    "register_team_tool": (
        "Submit a deterministic tool and input/expected-output tests for isolated "
        "validation. Registration does not add permissions.",
        _schema(
            {
                "name": _TEXT,
                "schema": _OBJECT,
                "entrypoint_script": _TEXT,
                "test_suite": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 25,
                    "items": _schema({"input": {}, "expected": {}}, ["input", "expected"]),
                },
            },
            ["name", "schema", "entrypoint_script", "test_suite"],
        ),
    ),
    "install_dependency": (
        "Fetch an exact, integrity-verified pure JavaScript dependency bundle "
        "from an allowed registry; no host install or package scripts.",
        _schema({"package": _TEXT, "version": _TEXT, "sha256": _TEXT}, ["package", "version"]),
    ),
}


class _BoundTool:
    risk_tier = "monitor"

    def __init__(self, toolkit: SwarmToolkit, name: str, schema: dict[str, Any], call_id: str):
        self.toolkit = toolkit
        self.name = name
        self.schema = schema
        self.description = _SPECS.get(name, ("Validated team-scoped JavaScript tool", {}))[0]
        self.call_id = call_id

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        return await self.toolkit._invoke(self.name, args, self.call_id)


class SwarmToolkit:
    def __init__(
        self,
        service: Any,
        store: Any,
        actor: SwarmActor,
        controller: SwarmController,
        cancel: Any,
    ) -> None:
        self.service, self.store = service, store
        self.offload = service.storage.call
        self.actor, self.controller, self.cancel = actor, controller, cancel
        self.evidence: list[str] = []

    async def catalog(self) -> tuple[dict[str, Any], ...]:
        state = await self.offload(self.store.tool_context, self.actor)
        allowed = set(state["tools"])
        result = [
            {"name": name, "description": description, "input_schema": schema}
            for name, (description, schema) in _SPECS.items()
            if name in allowed
        ]
        if "run_javascript" in allowed:
            for item in state["registered"]:
                if item["name"] not in _SPECS:
                    result.append(
                        {
                            "name": item["name"],
                            "description": "Validated team tool",
                            "input_schema": item["schema"],
                        }
                    )
        if state["role"] in {"lead", "coordinator"}:
            from .control_requests import COORDINATOR_OPERATIONS, LEAD_OPERATIONS

            operations = LEAD_OPERATIONS if state["role"] == "lead" else COORDINATOR_OPERATIONS
            result.append(
                {
                    "name": "request_control",
                    "description": "Queue a scoped controller request; execution is asynchronous.",
                    "input_schema": _schema(
                        {
                            "operation": {"type": "string", "enum": sorted(operations)},
                            "payload": _OBJECT,
                        },
                        ["operation", "payload"],
                    ),
                }
            )
        return tuple(result)

    async def execute(self, name: str, args: dict[str, Any], call_id: str) -> ToolResult:
        catalog = {tool["name"]: tool for tool in await self.catalog()}
        if name not in catalog:
            return ToolResult(
                False, None, "Tool is outside this task's authorized capability catalog"
            )
        try:
            Draft202012Validator(catalog[name]["input_schema"]).validate(args)
        except Exception as exc:  # noqa: BLE001 - invalid model arguments are recoverable
            log.info("Swarm tool schema rejected %s: %s", name, type(exc).__name__)
            return ToolResult(False, None, "Arguments do not match the tool schema")
        team = await self.offload(self.store.get)
        return await self.service.tool_executor.execute(
            _BoundTool(self, name, catalog[name]["input_schema"], call_id),
            args,
            user_utterance=team["goal"],
            trace_id=uuid4(),
            cancel_token=self.cancel,
            config_snapshot={
                "approval_surface": "unattended",
                "swarm_id": self.actor.team_id,
                "worker_id": self.actor.agent_id,
                "output_language": "en",
            },
        )

    def _key(self, call_id: str) -> str:
        raw = f"{self.actor.task_id}:{self.actor.task_fence}:{call_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _invoke(self, name: str, args: dict[str, Any], call_id: str) -> ToolResult:
        try:
            await self.offload(self.store.validate_actor, self.actor, writing=True)
            await self.offload(self.store.set_tool_activity, self.actor, name)
            key = self._key(call_id)
            team = await self.offload(self.store.get)
            policy = team["policy"]
            execution_error = ""
            if name == "fetch_url":
                if not policy["internet"]:
                    raise PermissionError("This team's Internet capability is disabled")
                await self.offload(self.store.consume_network, self.actor, key, "2000000")
                fetched = await self.service.egress.fetch(
                    args["url"],
                    allowed_domains=tuple(policy["allowed_domains"]),
                    max_bytes=2_000_000,
                    cancel=self.cancel.is_cancelled,
                )
                record = await self.offload(
                    self.store.capture_result,
                    self.controller,
                    self.actor,
                    "source.txt",
                    fetched.body,
                    key,
                    media_type=fetched.content_type,
                )
                self.evidence.append(record["id"])
                receipt = await self.offload(
                    self.store.capture_receipt,
                    self.controller,
                    self.actor,
                    "http",
                    {"url": fetched.url, "status": fetched.status, "body_sha256": fetched.sha256},
                    [record["id"]],
                    self._key(call_id + ":http-receipt"),
                )
                self.evidence.append(receipt["id"])
                output: Any = {
                    "url": fetched.url,
                    "status": fetched.status,
                    "sha256": fetched.sha256,
                    "artifact_id": record["id"],
                    "receipt_id": receipt["id"],
                    "content": fetched.body.decode("utf-8", errors="replace")[:60000],
                }
            elif name == "run_javascript":
                run = await self.service.sandbox.run(
                    args["script"], args["inputs"], cancel=self.cancel.is_cancelled
                )
                output = asdict(run)
                await self._execution_evidence(args["script"], args["inputs"], run, key)
                if run.exit_code != 0:
                    execution_error = run.stderr or "JavaScript execution failed"
            elif name == "write_artifact":
                output = await self.offload(
                    self.store.write_artifact,
                    self.actor,
                    args["name"],
                    args["content"],
                    key,
                    media_type=args.get("media_type", "text/plain"),
                )
                self.evidence.append(output["id"])
            elif name == "read_artifact":
                output = await self.artifact_input(args["artifact_id"])
            elif name == "search_team":
                if args.get("kind") == "peers":
                    output = await self.offload(
                        self.store.discover, self.actor, args.get("task_id", self.actor.task_id)
                    )
                else:
                    output = await self.offload(
                        self.store.search,
                        self.actor,
                        args["query"],
                        kind=args.get("kind", "tasks"),
                        task_id=args.get("task_id", ""),
                    )
            elif name == "send_message":
                message = PeerMessage.model_validate(dict(args, request_key=key))
                output = await self.offload(self.store.send_message, self.actor, message)
            elif name == "read_messages":
                if args.get("subscribe_topic"):
                    await self.offload(
                        self.store.subscribe,
                        self.actor,
                        args["subscribe_topic"],
                        args.get("task_id", self.actor.task_id),
                    )
                output = await self.offload(
                    self.store.messages, self.actor, limit=args.get("limit", 20)
                )
            elif name == "request_control":
                from .control_requests import enqueue

                output = await self.offload(
                    enqueue, self.store, self.actor, args["operation"], args["payload"], key
                )
                self.service._wake.set()
            elif name == "ack_message":
                output = await self.offload(self.store.ack_message, self.actor, args["message_id"])
            elif name == "register_team_tool":
                output = await self._register(args, key)
            elif name == "install_dependency":
                if not policy["allow_dependencies"] or not policy["internet"]:
                    raise PermissionError("Dependency retrieval is disabled for this team")
                registries = policy["dependency_registries"]
                allowed = policy["allowed_domains"]
                if allowed:
                    registries = [
                        host
                        for host in registries
                        if any(host == item or host.endswith("." + item) for item in allowed)
                    ]
                if not registries:
                    raise PermissionError(
                        "No dependency registry is within the team's network scope"
                    )
                await self.offload(self.store.consume_network, self.actor, key, "2000000")
                bundle = await self.service.dependencies.install(
                    args["package"],
                    args["version"],
                    sha256=args.get("sha256"),
                    registries=tuple(registries),
                    cancel=self.cancel.is_cancelled,
                )
                output = asdict(bundle)
                artifact = await self.offload(
                    self.store.capture_result,
                    self.controller,
                    self.actor,
                    "dependency.json",
                    json.dumps(output),
                    key,
                    media_type="application/json",
                )
                receipt = await self.offload(
                    self.store.capture_receipt,
                    self.controller,
                    self.actor,
                    "dependency",
                    output,
                    [artifact["id"]],
                    self._key(call_id + ":dependency-receipt"),
                )
                output["artifact_id"] = artifact["id"]
                output["receipt_id"] = receipt["id"]
                self.evidence.extend([artifact["id"], receipt["id"]])
            else:
                definition = await self.offload(self.store.get_tool, self.actor, name)
                Draft202012Validator(definition["schema"]).validate(args)
                run = await self.service.sandbox.run(
                    definition["script"], args, cancel=self.cancel.is_cancelled
                )
                output = asdict(run)
                await self._execution_evidence(definition["script"], args, run, key)
                if run.exit_code != 0:
                    execution_error = run.stderr or "JavaScript execution failed"
            # A late response cannot be surfaced as active work after revocation.
            await self.offload(self.store.validate_actor, self.actor, writing=True)
            return ToolResult(not execution_error, output, execution_error or None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - worker can adapt to a refused/failed capability
            log.info("Swarm tool %s failed: %s", name, type(exc).__name__)
            return ToolResult(False, None, str(exc)[:2000])
        finally:
            try:
                await self.offload(self.store.set_tool_activity, self.actor, None)
            except PermissionError:
                # Stop/revocation already cleared authoritative activity.
                log.debug("Tool activity completion was fenced")

    async def _execution_evidence(
        self,
        script: str,
        inputs: Any,
        result: SandboxResult,
        key: str,
    ) -> dict[str, Any]:
        record = await self.offload(
            self.store.capture_receipt,
            self.controller,
            self.actor,
            "execution",
            {"script": script, "inputs": inputs, "execution": asdict(result)},
            [],
            key,
        )
        self.evidence.append(record["id"])
        return record

    async def _register(self, args: dict[str, Any], key: str) -> dict[str, Any]:
        name = args["name"]
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name) or name in {*_SPECS, "request_control"}:
            raise ValueError("Choose a non-reserved lowercase tool name")
        schema = args["schema"]
        Draft202012Validator.check_schema(schema)
        script = args["entrypoint_script"]
        if len(script.encode()) > 100000:
            raise ValueError("The team tool exceeds its source size limit")
        results = []
        for test in args["test_suite"]:
            run = await self.service.sandbox.run(
                script, test["input"], cancel=self.cancel.is_cancelled
            )
            if run.exit_code != 0 or run.output != test["expected"]:
                raise ValueError("Tool registration test failed; correct the script and retry")
            results.append(asdict(run))
        proof = await self.offload(
            self.store.capture_result,
            self.controller,
            self.actor,
            "tool-validation.json",
            json.dumps(
                {"name": name, "script": script, "tests": args["test_suite"], "results": results}
            ),
            key,
            media_type="application/json",
        )
        return await self.offload(
            self.store.register_tool,
            self.controller,
            self.actor,
            name,
            schema,
            script,
            args["test_suite"],
            proof["id"],
            key,
        )

    async def capture_result(self, text: str) -> dict[str, Any]:
        """The trusted result journal is part of the attempt, not an external publication."""
        return await self.offload(
            self.store.capture_result,
            self.controller,
            self.actor,
            "result.txt",
            text,
            self._key("result"),
            media_type="text/plain",
        )

    async def artifact_input(self, artifact_id: str) -> dict[str, Any]:
        record = await self.offload(self.store.read_artifact, self.actor, artifact_id)
        if artifact_id not in self.evidence:
            self.evidence.append(artifact_id)
        if "content" in record:
            record["content"] = record["content"][:80000]
        return record

    async def run_script(self, script: str, inputs: Any) -> SandboxResult:
        result = await self.execute(
            "run_javascript", {"script": script, "inputs": inputs}, "trusted-verification"
        )
        if not result.success:
            return SandboxResult(None, "", result.error or "Verification refused", 1, 0)
        return SandboxResult(**result.output)
