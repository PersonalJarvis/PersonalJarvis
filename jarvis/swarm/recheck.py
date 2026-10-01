"""Owner-requested, bounded rechecks of immutable accepted contributions.

This is a historical quality check, not another worker attempt. It cannot start
inference, access the network, change acceptance, publish, or award more credit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from jarvis.control.cancel import CancelScope
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.core.swarm_reputation import ContributionRecheck

from .receipts import canonical, is_runtime_receipt, value_hash
from .store import SwarmAccessError, SwarmConflictError, SwarmStoreError, _json, task_contract_hash
from .worker import parse_json_response

log = logging.getLogger(__name__)
RECHECK_SECONDS = 45


async def _join_durable(job: asyncio.Task[Any]) -> Any:
    """A canceled awaiter must still join an already-running storage operation."""
    while True:
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            # Keep the admission lock until the protected storage thread has finished.
            if job.done():
                return job.result()
            # Repeated cancellation cannot release a generation's admission lock
            # while its storage thread can still commit into the old namespace.


async def _finish_owned(service: Any, *args: Any) -> dict[str, Any]:
    job = asyncio.create_task(service.storage.call(_finish, *args))
    try:
        return await asyncio.shield(job)
    except asyncio.CancelledError:
        await _join_durable(job)
        raise


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return ContributionRecheck.model_validate(
        {key: record[key] for key in ContributionRecheck.model_fields}
    ).model_dump(mode="json")


def _begin(
    store: Any, task_id: str, request_key: str = "original-proof"
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not request_key or len(request_key) > 100:
        raise ValueError("A bounded, retry-stable recheck request key is required")
    with store._tx(write=True) as connection:
        team = store._team(connection)
        task = store._task(connection, task_id)
        if task["state"] != "succeeded":
            raise SwarmConflictError("Only an accepted contribution can be rechecked")
        row = connection.execute(
            "SELECT record FROM verifications WHERE task_id=? AND fence=? AND accepted=1",
            (task_id, task["fence"]),
        ).fetchone()
        if row is None:
            raise SwarmStoreError("The accepted contribution has no original verification")
        contribution = json.loads(row[0])
        key = "recheck:v1:" + value_hash([contribution["id"], request_key])
        previous = connection.execute(
            "SELECT record FROM decisions WHERE request_key=?",
            (key,),
        ).fetchone()
        if previous:
            record = json.loads(previous[0])
            if record["state"] != "running" or (
                record.get("storage_generation", "") == team.get("storage_generation", "")
                and record["lease_until"] > time.time()
            ):
                return record, None
        else:
            record = dict(
                id=uuid4().hex,
                team_id=store.team_id,
                task_id=task_id,
                contribution_id=contribution["id"],
                agent_id=contribution["agent_id"],
                domain=task["domain"],
                kind="contribution_recheck",
                state="running",
                reason="",
                created_at=store.clock(),
                finished_at=None,
                evidence_ids=list(task["evidence"]),
                rating_id=None,
                request_key=key,
            )
        if "run_javascript" not in team["policy"]["tools"]:
            raise SwarmAccessError("This team's policy does not authorize JavaScript rechecks")
        active = connection.execute(
            "SELECT count(*) FROM decisions WHERE json_extract(record,'$.kind')="
            "'contribution_recheck' AND json_extract(record,'$.state')='running' "
            "AND CAST(json_extract(record,'$.lease_until') AS REAL)>? "
            "AND COALESCE(json_extract(record,'$.storage_generation'),'')=?",
            (time.time(), team.get("storage_generation", "")),
        ).fetchone()[0]
        if active >= min(2, team["limits"]["concurrency"]):
            raise SwarmConflictError("Another bounded recheck is active; retry after it finishes")
        record.update(
            lease_until=time.time() + RECHECK_SECONDS + 10,
            run_id=uuid4().hex,
            storage_generation=team.get("storage_generation", ""),
        )
        connection.execute(
            "INSERT INTO decisions VALUES (?,?,?) ON CONFLICT(request_key) DO UPDATE "
            "SET record=excluded.record",
            (record["id"], key, _json(record)),
        )
        store._event(
            connection,
            "contribution.recheck_started",
            "Owner requested a recheck",
            agent_id=contribution["agent_id"],
            task_id=task_id,
            data={"recheck_id": record["id"], "contribution_id": contribution["id"]},
        )
        return record, task


def _finish(
    store: Any, record: dict[str, Any], state: str, reason: str, observations: list[dict[str, Any]]
) -> dict[str, Any]:
    with store._tx(write=True) as connection:
        team = store._team(connection)
        if record.get("storage_generation", "") != team.get("storage_generation", ""):
            raise SwarmConflictError("Team storage changed before the recheck finished")
        row = connection.execute(
            "SELECT record FROM decisions WHERE id=?", (record["id"],)
        ).fetchone()
        if row is None:
            raise SwarmConflictError("The recheck no longer belongs to this team generation")
        current = json.loads(row[0])
        if current["run_id"] != record["run_id"] or current["state"] != "running":
            return _public(current)
        task = store._task(connection, record["task_id"])
        if state in {"regression", "fabrication"}:
            rating = store._rating(
                connection,
                record["agent_id"],
                task,
                "recheck-negative:" + value_hash([record["contribution_id"], state]),
                accepted=False,
                reason=state,
            )
            current["rating_id"] = rating["id"]
        current.update(
            state=state,
            reason=reason[:2000],
            finished_at=store.clock(),
            observations=observations,
            observations_sha256=value_hash(observations),
        )
        connection.execute(
            "UPDATE decisions SET record=? WHERE id=?", (_json(current), record["id"])
        )
        store._event(
            connection,
            "contribution.rechecked",
            reason,
            agent_id=record["agent_id"],
            task_id=record["task_id"],
            data={"recheck_id": record["id"], "state": state, "rating_id": current["rating_id"]},
        )
        return _public(current)


def _proofs(store: Any, task: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if task["verification"] != "javascript":
        raise NotImplementedError(
            "This contribution requires independent review with a newly authorized review budget."
        )
    result = parse_json_response(task["result"])
    execution = verification = None
    for artifact_id in task["evidence"]:
        metadata, content = store.download_artifact(artifact_id)
        if (
            not is_runtime_receipt(metadata)
            or metadata["task_id"] != task["id"]
            or metadata["attempt_fence"] != task["fence"]
            or metadata["owner_id"] != task["owner_id"]
        ):
            continue
        envelope = json.loads(content)
        payload = envelope["payload"]
        if envelope["kind"] == "execution" and payload["execution"]["exit_code"] == 0:
            if canonical(payload["execution"]["output"]) == canonical(result):
                execution = payload
        if (
            envelope["kind"] == "verification"
            and payload.get("accepted") is True
            and payload.get("contract_hash") == task_contract_hash(task)
            and payload.get("kind") == "javascript"
            and payload.get("execution")
        ):
            verification = payload["execution"]
    if execution is None or verification is None:
        raise NotImplementedError("The original authenticated deterministic proof is unavailable.")
    if verification["script"] != task["verification_script"] or canonical(
        verification["inputs"].get("result")
    ) != canonical(result):
        raise SwarmStoreError(
            "The stored verification contract is inconsistent; restore its backup"
        )
    return execution, verification


class _ReplayTool:
    name = "run_javascript"
    description = "Recheck one immutable accepted Swarm contribution in its bounded sandbox"
    risk_tier = "monitor"
    schema = {
        "type": "object",
        "properties": {"script": {"type": "string"}, "inputs": {}},
        "required": ["script", "inputs"],
        "additionalProperties": False,
    }

    def __init__(
        self, service: Any, store: Any, record: dict[str, Any], recipe: dict[str, Any], cancel: Any
    ):
        self.service, self.store, self.record = service, store, record
        self.recipe, self.cancel = recipe, cancel

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        if args != {"script": self.recipe["script"], "inputs": self.recipe["inputs"]}:
            raise SwarmAccessError("A recheck cannot change its authenticated recipe")
        team = await self.service.storage.call(self.store.get)
        if team.get("storage_generation", "") != self.record.get("storage_generation", ""):
            raise SwarmConflictError("Team storage changed before recheck execution")
        if "run_javascript" not in team["policy"]["tools"] or self.service._is_suspended():
            return ToolResult(False, None, "Recheck execution is no longer authorized")
        if self.cancel.is_cancelled():
            raise asyncio.CancelledError
        run = await self.service.sandbox.run(
            self.recipe["script"],
            self.recipe["inputs"],
            timeout_s=10,
            cancel=self.cancel.is_cancelled,
        )
        return ToolResult(True, asdict(run))


class Rechecks:
    async def skills(
        self: Any, team_id: str, agent_id: str, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        store = await self.storage.call(self.registry.open, team_id)
        return await self.storage.call(store.skill_profiles, agent_id, limit, offset)

    async def recheck(self: Any, team_id: str, task_id: str, request_key: str) -> dict[str, Any]:
        if self._closing:
            raise SwarmConflictError("The Swarm service is shutting down")
        active: dict[str, tuple[str, asyncio.Task[Any]]] | None = getattr(
            self, "_rechecks_active", None
        )
        if active is None:
            active = {}
            self._rechecks_active = active
        if len(active) >= 2:
            raise SwarmConflictError(
                "Two owner rechecks are already active; retry after completion"
            )
        request_id = uuid4().hex
        job = asyncio.current_task()
        assert job is not None
        active[request_id] = (team_id, job)
        try:
            return await Rechecks._recheck_owned(self, team_id, task_id, request_key)
        finally:
            active.pop(request_id, None)

    async def _cancel_rechecks(self: Any, team_id: str | None = None) -> None:
        """Drain owned rechecks before replacing their data or closing execution pools."""
        active = getattr(self, "_rechecks_active", {})
        jobs = [
            job
            for owned_team, job in list(active.values())
            if (team_id is None or owned_team == team_id) and job is not asyncio.current_task()
        ]
        for job in jobs:
            job.cancel()
        results = await asyncio.gather(*jobs, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                log.warning(
                    "Swarm recheck ended during lifecycle cleanup: %s", type(result).__name__
                )

    async def _recheck_owned(
        self: Any, team_id: str, task_id: str, request_key: str
    ) -> dict[str, Any]:
        # Restore/delete hold the same admission lock until the replacement is
        # installed, so a new request cannot capture a store being retired.
        async with self._storage_change_lock():
            if self._closing:
                raise SwarmConflictError("The Swarm service is shutting down")
            store = await self.storage.call(self.registry.open, team_id)
            if self._is_suspended() or not await self.storage.call(
                self._emergency_fence.permits, team_id
            ):
                raise SwarmConflictError(
                    "Resume after the process stop before requesting a recheck"
                )
            beginning = asyncio.create_task(self.storage.call(_begin, store, task_id, request_key))
            try:
                record, task = await asyncio.shield(beginning)
            except asyncio.CancelledError:
                record, task = await _join_durable(beginning)
                if task is not None:
                    await _finish_owned(
                        self,
                        store,
                        record,
                        "inconclusive",
                        "The recheck was canceled before execution; no rating changed.",
                        [],
                    )
                raise
        if task is None:
            return _public(record)
        observations: list[dict[str, Any]] = []
        state, reason = "inconclusive", "The recheck did not finish; no rating changed."
        async with CancelScope(self._kill_switch, holder=f"swarm:recheck:{record['id']}") as cancel:
            try:
                async with asyncio.timeout(RECHECK_SECONDS):
                    execution, verification = await self.storage.call(_proofs, store, task)
                    results = []
                    for recipe in (execution, execution, verification, verification):
                        outcome = await self.tool_executor.execute(
                            _ReplayTool(self, store, record, recipe, cancel),
                            {"script": recipe["script"], "inputs": recipe["inputs"]},
                            user_utterance="Recheck this contribution against its original proof",
                            trace_id=uuid4(),
                            cancel_token=cancel,
                            config_snapshot={"swarm_id": team_id, "approval_surface": "unattended"},
                        )
                        if not outcome.success:
                            raise SwarmConflictError(
                                outcome.error or "Recheck execution was refused"
                            )
                        output = outcome.output
                        observations.append(
                            dict(
                                recipe_sha256=value_hash(recipe),
                                output_sha256=value_hash(output["output"]),
                                exit_code=output["exit_code"],
                                stderr=output["stderr"][:1000],
                            )
                        )
                        if output["exit_code"] != 0:
                            raise SwarmConflictError(
                                "Sandbox execution failed; no agent fault inferred"
                            )
                        results.append(output["output"])
                    if cancel.is_cancelled() or self._is_suspended():
                        raise asyncio.CancelledError
                    if canonical(results[0]) != canonical(results[1]) or canonical(
                        results[2]
                    ) != canonical(results[3]):
                        reason = (
                            "The computation or verifier is not reproducible; no fault inferred."
                        )
                    elif (
                        not isinstance(results[2], dict)
                        or type(results[2].get("accepted")) is not bool
                    ):
                        reason = "The original verifier returned no boolean decision."
                    elif canonical(results[0]) != canonical(execution["execution"]["output"]):
                        state, reason = (
                            "regression",
                            "Two replays contradict the accepted computation.",
                        )
                    elif not results[2]["accepted"]:
                        state, reason = (
                            "regression",
                            "The original verifier rejects the accepted result.",
                        )
                    else:
                        state, reason = (
                            "passed",
                            "The computation and original verifier still agree.",
                        )
            except NotImplementedError as exc:
                # Unsupported execution is persisted in the recheck verdict below.
                state, reason = "unsupported", str(exc)
            except (SwarmStoreError, PermissionError, ValueError, OSError) as exc:
                log.warning("Swarm contribution recheck unavailable: %s", type(exc).__name__)
                reason = (
                    "Evidence or execution is unavailable; repair storage or retry independently."
                )
            except TimeoutError:
                # Persist a timeout verdict after canceling the owned sandbox.
                cancel.cancel("recheck_timeout")
                reason = "The bounded recheck timed out; no agent fault inferred."
            except asyncio.CancelledError:
                cancel.cancel("recheck_canceled")
                try:
                    await _finish_owned(self, store, record, state, reason, observations)
                except (SwarmStoreError, PermissionError):
                    # Another process may already have replaced/deleted this
                    # generation. Never write a cancellation into its successor.
                    log.info("Swarm recheck cancellation was superseded by storage replacement")
                raise
        return await _finish_owned(self, store, record, state, reason, observations)
