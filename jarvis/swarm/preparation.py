"""Durable owner clarification before any execution worker can be admitted.

Preparation has a private accounting principal, never worker authority. Only
tool-free completions are exposed; a generation, lease and operation nonce fence
every write. Full drafts live in decisions and only a pointer enters checkpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from jarvis.control.cancel import CancelScope
from jarvis.core.protocols import BrainMessage
from jarvis.core.swarm_preparation import (
    PreparationAnswers,
    PreparationBegin,
    PreparationLaunch,
    PreparationPlan,
    PreparationQuestion,
    PreparationView,
)
from jarvis.core.swarm_types import (
    SwarmActor,
    SwarmController,
    TaskSpec,
    TeamCreate,
    decimal_counter,
)
from jarvis.core.turn_language import DEFAULT_LOCALE, resolve_output_language

from .autonomy import PLAN, install_in
from .store import (
    SwarmAccessError,
    SwarmBudgetError,
    SwarmConflictError,
    TeamStore,
    _check_expected,
    _digest,
    _json,
    _validate_graph,
)
from .worker import WorkerEngine, parse_json_response

log = logging.getLogger(__name__)
HEAD = "preparation:head"
PREPARATION_SECONDS = 180


def _hash(value: Any) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def initialize(store: TeamStore, connection: Any, spec: TeamCreate) -> None:
    if spec.tasks:
        raise ValueError("Goal preparation cannot include a preinstalled execution plan")
    if connection.execute("SELECT 1 FROM decisions WHERE request_key=?", (HEAD,)).fetchone():
        return
    record = {
        "id": uuid4().hex,
        "kind": "preparation_head",
        "team_id": store.team_id,
        "original_goal": spec.goal,
        "original_acceptance": spec.acceptance,
        "revision": 0,
        "state": "clarifying",
        "busy": False,
        "questions": [],
        "answers": {},
        "plan": None,
        "digest": "",
        "error": "",
    }
    connection.execute("INSERT INTO decisions VALUES (?,?,?)", (record["id"], HEAD, _json(record)))
    _save(store, connection, record)


def _head(connection: Any) -> dict[str, Any]:
    row = connection.execute("SELECT record FROM decisions WHERE request_key=?", (HEAD,)).fetchone()
    if row is None:
        raise SwarmConflictError("This team has no preparation; begin with an empty created team")
    return json.loads(row[0])


def _save(store: TeamStore, connection: Any, head: dict[str, Any]) -> None:
    connection.execute("UPDATE decisions SET record=? WHERE request_key=?", (_json(head), HEAD))
    team = store._team(connection)
    checkpoint = dict(team.get("checkpoint") or {})
    checkpoint["preparation"] = {
        "required": True,
        "revision": head["revision"],
        "state": head["state"],
        "decision_id": head["id"],
        "digest": head["digest"],
    }
    store._checkpoint(connection, team, checkpoint, source="preparation")


def _view(store: TeamStore, connection: Any) -> dict[str, Any]:
    team, head = store._team(connection), _head(connection)
    view = {key: head[key] for key in PreparationView.model_fields if key != "team"}
    if view["busy"]:
        lease = connection.execute(
            "SELECT fence,expires_at FROM controller WHERE singleton=1"
        ).fetchone()
        if (
            team["state"] != "created"
            or head.get("storage_generation", "") != team.get("storage_generation", "")
            or lease is None
            or lease["fence"] != head.get("controller_fence")
            or lease["expires_at"] <= store.clock()
            or head.get("lease_until", 0) <= store.clock()
        ):
            view.update(
                busy=False, state="failed", error="Preparation was interrupted; retry to continue."
            )
    return PreparationView.model_validate({"team": team, **view}).model_dump(mode="json")


def read(store: TeamStore) -> dict[str, Any]:
    with store._tx() as connection:
        return _view(store, connection)


def _begin(
    store: TeamStore,
    controller: SwarmController,
    request_key: str,
    generation: str,
    body: PreparationAnswers | None,
    reply_language: str = "auto",
    default_language: str = DEFAULT_LOCALE,
) -> dict[str, Any] | None:
    operation_key = "preparation:operation:" + _hash(request_key)
    fingerprint = _hash({"generation": generation, "answers": body.model_dump() if body else None})
    with store._tx(write=True) as connection:
        store._controller(connection, controller)
        team = store._team(connection)
        _check_expected(team, None, generation)
        if team["state"] != "created":
            raise SwarmConflictError("Only an unlaunched team can be prepared")
        if connection.execute("SELECT 1 FROM tasks LIMIT 1").fetchone():
            raise SwarmConflictError("Preparation requires a team without execution tasks")
        if not connection.execute(
            "SELECT 1 FROM decisions WHERE request_key=?", (HEAD,)
        ).fetchone():
            initialize(
                store,
                connection,
                TeamCreate(
                    name=team["name"],
                    goal=team["goal"],
                    acceptance=team["acceptance"],
                    limits=team["limits"],
                    policy=team["policy"],
                    mode=team["mode"],
                    request_key=request_key,
                    preparation_required=True,
                ),
            )
        head = _head(connection)
        prior = connection.execute(
            "SELECT record FROM decisions WHERE request_key=?", (operation_key,)
        ).fetchone()
        if prior:
            previous = json.loads(prior[0])
            if previous["fingerprint"] != fingerprint:
                raise SwarmConflictError("Preparation request key identifies different input")
            # A finished or ambiguous request never repeats inference on replay.
            return None
        if _view(store, connection)["busy"]:
            raise SwarmConflictError("Preparation is already in progress")
        if body is None and head["questions"]:
            return None
        if body is not None:
            if head["revision"] != body.expected_revision:
                raise SwarmConflictError("The questions or plan changed; refresh before answering")
            ids = {question["id"] for question in head["questions"]}
            if not ids or set(body.answers) != ids:
                raise ValueError("Answer exactly the current clarification questions")
        revision = head["revision"] + 1
        output_language = resolve_output_language(
            reply_language,
            "unknown",
            "\n".join(body.answers.values()) if body else head["original_goal"],
            default=default_language,
            conversation_language=head.get("output_language", ""),
        )
        record = {
            "id": uuid4().hex,
            "kind": "preparation_operation",
            "team_id": store.team_id,
            "fingerprint": fingerprint,
            "request_key": operation_key,
            "revision": revision,
            "nonce": uuid4().hex,
            "controller_fence": controller.fence,
            "storage_generation": generation,
            "state": "running",
            "created_at": store.clock(),
            "lease_until": store.clock() + PREPARATION_SECONDS + 30,
            "original_goal": head["original_goal"],
            "original_acceptance": head["original_acceptance"],
            "questions": head["questions"],
            "answers": body.answers if body else {},
            "phase": "plan" if body else "clarify",
            "output_language": output_language,
        }
        connection.execute(
            "INSERT INTO decisions VALUES (?,?,?)", (record["id"], operation_key, _json(record))
        )
        head.update(
            revision=revision,
            state="planning" if body else "clarifying",
            busy=True,
            answers=record["answers"],
            plan=None,
            digest="",
            error="",
            operation_id=record["id"],
            nonce=record["nonce"],
            controller_fence=controller.fence,
            lease_until=record["lease_until"],
            storage_generation=generation,
            output_language=output_language,
        )
        _save(store, connection, head)
        return record


def _check(
    store: TeamStore, connection: Any, controller: SwarmController, operation: dict[str, Any]
) -> dict[str, Any]:
    store._controller(connection, controller)
    team = store._team(connection)
    head = _head(connection)
    if (
        team["state"] != "created"
        or operation["storage_generation"] != team.get("storage_generation", "")
        or head.get("operation_id") != operation["id"]
        or head.get("nonce") != operation["nonce"]
        or head["revision"] != operation["revision"]
        or not head["busy"]
        or head["lease_until"] <= store.clock()
    ):
        raise SwarmConflictError("Preparation operation is no longer current")
    return head


class _Accounting:
    """Private accounting facade; it intentionally exposes no worker or tool methods."""

    def __init__(self, store: TeamStore, controller: SwarmController, operation: dict[str, Any]):
        self.store, self.controller, self.operation = store, controller, operation
        self.clock = store.clock
        self.actor = SwarmActor(
            store.team_id,
            store.get()["lead_id"],
            operation["nonce"],
            "preparation_" + operation["id"],
            operation["revision"],
        )

    def get(self) -> dict[str, Any]:
        with self.store._tx() as connection:
            _check(self.store, connection, self.controller, self.operation)
            return self.store._team(connection)

    def reserve(self, actor: SwarmActor, key: str, tokens: str, cost: str) -> dict[str, Any]:
        if actor != self.actor:
            raise SwarmAccessError("Preparation accounting principal mismatch")
        with self.store._tx(write=True) as connection:
            _check(self.store, connection, self.controller, self.operation)
            return self.store._reserve_in(
                connection, actor, key, decimal_counter(tokens), decimal_counter(cost)
            )

    def reconcile(
        self, controller: SwarmController, reservation_id: str, tokens: str | None, cost: str | None
    ) -> dict[str, Any]:
        if controller != self.controller:
            raise SwarmAccessError("Preparation controller mismatch")
        with self.store._tx(write=True) as connection:
            _check(self.store, connection, controller, self.operation)
            row = connection.execute(
                "SELECT task_id,task_fence,controller_fence FROM reservations WHERE id=?",
                (reservation_id,),
            ).fetchone()
            if row is None or (row["task_id"], row["task_fence"], row["controller_fence"]) != (
                self.actor.task_id,
                self.actor.task_fence,
                controller.fence,
            ):
                raise SwarmAccessError("Reservation is outside this preparation operation")
            return self.store._reconcile_in(
                connection,
                reservation_id,
                decimal_counter(tokens) if tokens is not None else None,
                decimal_counter(cost) if cost is not None else None,
            )

    def append_event(
        self, controller: SwarmController, kind: str, summary: str, **kwargs: Any
    ) -> None:
        with self.store._tx(write=True) as connection:
            _check(self.store, connection, controller, self.operation)
            self.store._event(connection, "preparation." + kind, summary, **kwargs)


def _finish(
    store: TeamStore,
    controller: SwarmController,
    operation: dict[str, Any],
    output: Any,
    error: str = "",
) -> dict[str, Any]:
    with store._tx(write=True) as connection:
        head = _check(store, connection, controller, operation)
        if error:
            head.update(state="failed", error=error, busy=False)
        elif operation["phase"] == "clarify":
            questions = [
                PreparationQuestion.model_validate(item).model_dump()
                for item in output["questions"]
            ]
            if not 1 <= len(questions) <= 3 or len({item["id"] for item in questions}) != len(
                questions
            ):
                raise ValueError("Preparation requires one to three unique questions")
            head.update(questions=questions, state="clarifying", busy=False)
        else:
            plan = PreparationPlan.model_validate(output)
            if any(item.id.startswith("__swarm_") for item in plan.tasks):
                raise ValueError("Plan task IDs cannot use reserved prefixes")
            _validate_graph(plan.tasks, {})
            team = store._team(connection)
            if any(
                not set(item.required_tools) <= set(team["policy"]["tools"]) for item in plan.tasks
            ):
                raise ValueError("A plan cannot expand the owner's tool policy")
            encoded = plan.model_dump(mode="json")
            if len(_json(encoded).encode("utf-8")) > 240_000:
                raise ValueError("The prepared plan exceeds its storage bound")
            head.update(plan=encoded, digest=_hash(encoded), state="ready", busy=False)
        settled = {
            **operation,
            "state": head["state"],
            "finished_at": store.clock(),
            "output": output if not error else None,
            "error": error,
        }
        connection.execute(
            "UPDATE decisions SET record=? WHERE id=?", (_json(settled), operation["id"])
        )
        _save(store, connection, head)
        return _view(store, connection)


def _launch(
    store: TeamStore, controller: SwarmController, body: PreparationLaunch, capacity: int
) -> dict[str, Any]:
    with store._tx(write=True) as connection:
        store._controller(connection, controller)
        team = store._team(connection)
        _check_expected(team, None, body.expected_storage_generation)
        head = _head(connection)
        key = "preparation:launch:" + _hash(body.request_key)
        fingerprint = _hash(body.model_dump())
        previous = connection.execute(
            "SELECT record FROM decisions WHERE request_key=?", (key,)
        ).fetchone()
        if previous:
            if json.loads(previous[0])["fingerprint"] != fingerprint:
                raise SwarmConflictError("Launch key identifies different approval")
            return _view(store, connection)
        if team["state"] != "created" or head["state"] != "ready" or head["busy"]:
            raise SwarmConflictError("Review a ready plan before launching")
        if head["revision"] != body.expected_revision or head["digest"] != body.digest:
            raise SwarmConflictError("The plan changed; review its current revision")
        plan = PreparationPlan.model_validate(head["plan"])
        if _hash(plan.model_dump(mode="json")) != body.digest:
            raise SwarmConflictError("Stored plan does not match the reviewed digest")
        if connection.execute("SELECT 1 FROM tasks LIMIT 1").fetchone():
            raise SwarmConflictError("Execution tasks already exist")
        team.update(goal=plan.goal, acceptance=plan.acceptance)
        store._save_team(connection, team)
        store._insert_tasks(
            connection,
            [
                TaskSpec(
                    id=PLAN,
                    title="Owner-approved goal plan",
                    description=plan.summary,
                    acceptance="The owner approved the exact stored plan.",
                    domain="planning",
                )
            ],
        )
        install_in(store, connection, plan.tasks, plan.remaining_decomposition, [], capacity)
        approval = {
            "id": uuid4().hex,
            "kind": "preparation_approval",
            "team_id": store.team_id,
            "fingerprint": fingerprint,
            "revision": head["revision"],
            "digest": body.digest,
            "storage_generation": body.expected_storage_generation,
            "created_at": store.clock(),
        }
        connection.execute(
            "INSERT INTO decisions VALUES (?,?,?)", (approval["id"], key, _json(approval))
        )
        head.update(state="launched", approval_id=approval["id"])
        _save(store, connection, head)
        store._transition(connection, "running", None, "Owner launched the reviewed plan")
        return _view(store, connection)


async def _durable(service: Any, function: Any, *args: Any, **kwargs: Any) -> Any:
    """Join every dispatched store write before cancellation releases admission."""
    work = asyncio.create_task(service.storage.call(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                # Drain the protected write before releasing this operation's fence.
                continue
        # Retrieve any exception; the owning operation still remains canceled.
        if not work.cancelled():
            work.exception()
        raise


class Preparations:
    """Owner-only facade mixed into the Swarm control plane."""

    async def create_preparation(self: Any, spec: TeamCreate) -> dict[str, Any]:
        if spec.tasks:
            raise ValueError("Start preparation with a goal, without execution tasks")
        team = await self.create_team(spec.model_copy(update={"preparation_required": True}))
        if team["state"] != "created":
            return await self.preparation(team["id"])
        return await self.begin_preparation(
            team["id"],
            PreparationBegin(
                expected_storage_generation=team.get("storage_generation", ""),
                request_key=spec.request_key,
            ),
        )

    async def preparation(self: Any, team_id: str) -> dict[str, Any]:
        store = await self.storage.call(self.registry.open, team_id)
        return await self.storage.call(read, store)

    async def begin_preparation(self: Any, team_id: str, body: PreparationBegin) -> dict[str, Any]:
        return await self._prepare(
            team_id, body.request_key, body.expected_storage_generation, None
        )

    async def answer_preparation(
        self: Any, team_id: str, body: PreparationAnswers
    ) -> dict[str, Any]:
        return await self._prepare(
            team_id, body.request_key, body.expected_storage_generation, body
        )

    async def _prepare(
        self: Any, team_id: str, key: str, generation: str, body: PreparationAnswers | None
    ) -> dict[str, Any]:
        await self.start()
        if self._is_suspended():
            await self._pause_for_emergency()
            await self._arm_emergency_stop()
        jobs = self._preparation_jobs
        fingerprint = _hash([key, generation, body.model_dump() if body else None])
        async with self._storage_change_lock():
            if self._closing or self._is_suspended():
                raise SwarmConflictError(
                    "Preparation is stopped; resume only after the emergency stop is cleared"
                )
            existing = jobs.get(team_id)
            if existing:
                if existing[0] != fingerprint:
                    raise SwarmConflictError("Another preparation request is already in progress")
                task = existing[1]
            else:
                if len(jobs) >= 2:
                    raise SwarmConflictError(
                        "Two preparations are already active; retry after one finishes"
                    )
                store = await self.storage.call(self.registry.open, team_id)
                controller = await self._controller(store)
                if controller is None:
                    raise SwarmConflictError("Another controller is preparing this team")
                task = asyncio.create_task(
                    self._run_preparation(store, controller, key, generation, body)
                )
                jobs[team_id] = (fingerprint, task)

                def completed(done: asyncio.Task[Any]) -> None:
                    if jobs.get(team_id, (None, None))[1] is done:
                        jobs.pop(team_id, None)
                    if not done.cancelled():
                        # A disconnected HTTP awaiter still leaves an owned task.
                        # Retrieve its sanitized failure instead of leaking a task warning.
                        done.exception()

                task.add_done_callback(completed)
        # HTTP disconnects do not abandon an owned, bounded preparation operation.
        return await asyncio.shield(task)

    async def _run_preparation(
        self: Any,
        store: TeamStore,
        controller: SwarmController,
        key: str,
        generation: str,
        body: PreparationAnswers | None,
    ) -> dict[str, Any]:
        operation = None
        engine = None
        provider = None
        renew = None
        async with CancelScope(
            self._kill_switch, holder="swarm:preparation:" + store.team_id
        ) as cancel:
            try:
                config = getattr(self.brain_factory, "cfg", None)
                operation = await _durable(
                    self,
                    _begin,
                    store,
                    controller,
                    key,
                    generation,
                    body,
                    getattr(getattr(config, "brain", None), "reply_language", "auto"),
                    getattr(getattr(config, "ui", None), "language", DEFAULT_LOCALE),
                )
                if operation is None:
                    return await _durable(self, read, store)

                async def renew_lease() -> None:
                    while True:
                        await asyncio.sleep(20)
                        await _durable(self, store.renew_controller, controller)

                renew = asyncio.create_task(renew_lease())
                async with asyncio.timeout(PREPARATION_SECONDS):
                    try:
                        provider = await self.brain_factory.create()
                    except Exception:
                        raise RuntimeError(
                            "No provider is ready; check its connection in API Keys"
                        ) from None
                    accounting = await _durable(self, _Accounting, store, controller, operation)
                    engine = WorkerEngine(
                        accounting,
                        accounting.actor,
                        controller,
                        provider,
                        cancel,
                        offload=lambda function, *args, **kwargs: _durable(
                            self, function, *args, **kwargs
                        ),
                    )
                    team = await _durable(self, accounting.get)
                    prompt = {
                        "original_goal": operation["original_goal"],
                        "original_acceptance": operation["original_acceptance"],
                        "questions": operation["questions"],
                        "answers": operation["answers"],
                        "authorized_policy": team["policy"],
                        "authorized_limits": team["limits"],
                        "output_language": operation["output_language"],
                    }
                    if operation["phase"] == "clarify":
                        prompt["question_schema"] = PreparationQuestion.model_json_schema()
                        system = (
                            "You prepare a goal for its owner before a Swarm is launched. "
                            'Return only JSON {"questions":[Question,...]}. '
                            "Ask one to three short, goal-specific questions about missing "
                            "deliverable, scope, constraints or evidence. "
                            "Address a person with no technical background. Ask for concrete "
                            "missing information: the actual input data, desired output, how "
                            "success should be checked, or a meaningful constraint. Do not ask "
                            "the owner to choose an implementation, programming language, data "
                            "transport, function arguments, hardcoded values or architecture. "
                            "When required data such as a list of numbers is missing, ask the "
                            "owner to paste or provide that exact data in their answer. Never "
                            "invent missing inputs or propose sample values as a substitute. "
                            "Offer choices only for genuine owner preferences; keep factual "
                            "input requests free-form. Never suggest reading a local file or "
                            "another data source unless its contents or an authorized access "
                            "mechanism are already available to this team. "
                            "Use stable descriptive IDs and an optional short hint. "
                            "Do not ask about internals or agent counts; choose implementation "
                            "defaults internally. Do not claim that open research problems "
                            "are solved. "
                            "No tools, execution, budget changes or permissions are allowed. "
                        )
                    else:
                        prompt["plan_schema"] = PreparationPlan.model_json_schema()
                        system = (
                            "You prepare the exact plan for owner review before any Swarm "
                            "worker starts. Return only the PreparationPlan JSON. "
                            "Use the original goal and answers to specify the goal, "
                            "measurable acceptance, concise summary, assumptions and "
                            "exclusions. "
                            "Produce 1-32 nonredundant TaskSpecs proportional to the goal. No "
                            "ID may begin __swarm_. Use independent parallel approaches where "
                            "useful. "
                            "Set remaining_decomposition only when useful work beyond this "
                            "batch remains. Tasks may depend only on other tasks in this "
                            "batch. "
                            "Required tools must be a subset of authorized_policy.tools. "
                            "Never add permissions, budgets or guarantees for unsolved "
                            "research. "
                            "The sandbox runs JavaScript function main(input), not Python, "
                            "shell or native tools. For computation include independent "
                            "JavaScript "
                            "verification returning {accepted:boolean,reason:string} and "
                            "evaluating input.result. Use review verification for prose and "
                            "research. "
                            "The runtime saves final replies and execution/fetch receipts and "
                            "independently verifies work: do not add redundant tasks for "
                            "those steps. "
                            "The runtime adds final delivery and automatically manages "
                            "agents. Use understandable language for the owner. "
                        )
                    system += (
                        f" Resolved output language: {operation['output_language']}. "
                        "Use this exact language for all owner-facing questions, choices, "
                        "hints and plan prose. The host resolved it once for this turn; do not "
                        "infer a different language. Keep JSON keys, task IDs and code unchanged."
                    )
                    text, calls = await engine.completion(
                        [BrainMessage(role="user", content=_json(prompt))],
                        system,
                        phase="preparation:" + operation["phase"],
                    )
                    if calls:
                        raise ValueError("Preparation must not request tools")
                    return await _durable(
                        self, _finish, store, controller, operation, parse_json_response(text)
                    )
            except asyncio.CancelledError:
                if operation is not None:
                    try:
                        await _durable(
                            self,
                            _finish,
                            store,
                            controller,
                            operation,
                            None,
                            "Preparation was stopped; retry when ready.",
                        )
                    except (SwarmAccessError, SwarmConflictError):
                        log.debug("Preparation cancellation was already fenced")
                raise
            except Exception as exc:
                # Keep SDK bodies and invalid model output out of logs, UI and storage.
                reason = (
                    "Preparation could not finish; retry after checking "
                    "the provider and your limits."
                )
                if isinstance(exc, (ValidationError, ValueError, KeyError, TypeError)):
                    reason = (
                        "The provider returned an invalid preparation; "
                        "retry to create a valid plan."
                    )
                elif isinstance(exc, SwarmBudgetError):
                    reason = "The authorized budget is insufficient for preparation."
                log.info("Swarm preparation ended (%s)", type(exc).__name__)
                if operation is None:
                    raise SwarmConflictError(reason) from None
                return await _durable(self, _finish, store, controller, operation, None, reason)
            finally:
                if renew is not None:
                    renew.cancel()
                    await asyncio.gather(renew, return_exceptions=True)
                if engine is not None:
                    try:
                        async with asyncio.timeout(10):
                            await engine.aclose()
                    except Exception:
                        log.warning("Preparation provider cleanup failed")
                elif provider is not None:
                    try:
                        async with asyncio.timeout(10):
                            await provider.aclose()
                    except Exception:
                        log.warning("Unused preparation provider cleanup failed")

    async def _cancel_preparations(self: Any, team_id: str | None = None) -> None:
        jobs = [
            job
            for identity, (_, job) in list(self._preparation_jobs.items())
            if team_id is None or identity == team_id
        ]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    async def launch_preparation(
        self: Any, team_id: str, body: PreparationLaunch
    ) -> dict[str, Any]:
        await self.start()
        if self._is_suspended():
            await self._pause_for_emergency()
            await self._arm_emergency_stop()
        async with self._storage_change_lock():
            if self._closing or self._is_suspended():
                raise SwarmConflictError("Clear the emergency stop before launching a Swarm")
            if team_id in self._preparation_jobs:
                raise SwarmConflictError("Wait for preparation to finish before launching")
            store = await self.storage.call(self.registry.open, team_id)
            controller = await self._controller(store)
            if controller is None:
                raise SwarmConflictError("Another controller owns this team; retry shortly")
            team = await self.storage.call(store.get)
            capacity = min(
                team["limits"]["concurrency"],
                32 if team["mode"] == "local" else self._remote_capacity,
            )
            if capacity < 1:
                raise SwarmConflictError(
                    "Execution capacity is unavailable; check distributed setup"
                )
            async with self._stop_lock:
                if self._is_suspended():
                    raise SwarmConflictError("Emergency stop interrupted the launch")
                result = await _durable(self, _launch, store, controller, body, capacity)
                await _durable(self, self._emergency_fence.allow, team_id)
            self._wake.set()
            return result
