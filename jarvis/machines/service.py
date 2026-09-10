"""The hub owns permissions and durable dispatch; a socket never grants authority."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from .models import (
    LEASE_SECONDS,
    MachineCapabilities,
    MachineCommand,
    MachineGrant,
    MachineOperation,
    MachineResult,
)
from .store import MachineStore

log = logging.getLogger(__name__)


class MachineService:
    def __init__(self, data_dir: Path) -> None:
        self.store = MachineStore(data_dir / "machines.db")
        self._started = False
        self._start_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self.connections: dict[str, Any] = {}
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.rpc_handlers: dict[str, tuple[str, Any]] = {}
        self.transfer_tasks: dict[str, asyncio.Task[None]] = {}
        self.running_calls: dict[str, asyncio.Task[Any]] = {}

    async def close(self) -> None:
        tasks = list(self.transfer_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for socket in list(self.connections.values()):
            try:
                await socket.close(code=1001)
            except Exception:  # noqa: BLE001 — transport already closed; shutdown continues
                log.debug("Machine socket already closed during shutdown", exc_info=True)
        if self._started:
            await self.store.write(
                "UPDATE machine_jobs SET state='uncertain' WHERE state='running'"
            )
        for future in self.pending.values():
            if not future.done():
                future.set_result(
                    {"success": False, "error": "Hub stopped; reconcile the remote outcome"}
                )
        self.connections.clear()
        self.rpc_handlers.clear()
        _services.pop(self.store.path.parent.resolve(), None)

    async def start(self) -> None:
        async with self._start_lock:
            if not self._started:
                await self.store.initialize()
                self._started = True

    async def attach(self, machine_id: str, socket: Any, capabilities: MachineCapabilities) -> None:
        async with self._lock:
            await self.store.machine(machine_id)
            if machine_id in self.connections:
                raise PermissionError("Machine already has an active connector")
            await self.store.write(
                "UPDATE machines SET capabilities=?,last_seen=? WHERE id=?",
                (capabilities.model_dump_json(), time.time(), machine_id),
            )
            self.connections[machine_id] = socket

    async def detach(self, machine_id: str, socket: Any) -> None:
        async with self._lock:
            if self.connections.get(machine_id) is socket:
                self.connections.pop(machine_id)
                await self.store.write(
                    "UPDATE machine_jobs SET state='uncertain' "
                    "WHERE machine_id=? AND state='running'",
                    (machine_id,),
                )
                rows = await self.store.rows(
                    "SELECT id FROM machine_jobs WHERE machine_id=? AND state='uncertain'",
                    (machine_id,),
                )
                for row in rows:
                    future = self.pending.get(row["id"])
                    if future is not None and not future.done():
                        future.set_result(
                            {
                                "success": False,
                                "error": "Connection lost; outcome requires reconciliation",
                                "job_id": row["id"],
                            }
                        )

    async def list_machines(self) -> list[dict[str, Any]]:
        rows = await self.store.rows("SELECT id FROM machines WHERE revoked=0 ORDER BY name")
        result = []
        for row in rows:
            machine = await self.store.machine(row["id"])
            machine["online"] = row["id"] in self.connections
            result.append(machine)
        return result

    async def revoke(self, machine_id: str) -> None:
        async with self._lock:
            await self.store.write(
                "UPDATE machines SET revoked=1,token_hash=NULL WHERE id=?", (machine_id,)
            )
            socket = self.connections.get(machine_id)
            jobs = await self.store.rows(
                "SELECT id FROM machine_jobs WHERE machine_id=? AND state='running'", (machine_id,)
            )
        for job in jobs:
            await self.cancel(job["id"])
        if socket is not None:
            await socket.close(code=4403)

    async def set_grant(self, grant: MachineGrant) -> None:
        async with self._lock:
            old = await self.store.rows(
                "SELECT body FROM grants WHERE agent_id=? AND machine_id=?",
                (grant.agent_id, grant.machine_id),
            )
            await self.store.put_grant(grant)
            previous = MachineGrant.model_validate_json(old[0]["body"]) if old else None
            changed = previous is not None and (
                (previous.shell and not grant.shell)
                or (previous.files and not grant.files)
                or (previous.scope == "account" and grant.scope == "workspace")
                or previous.workspace != grant.workspace
                or (previous.desktop != "none" and previous.desktop != grant.desktop)
            )
            jobs = (
                await self.store.rows(
                    "SELECT id FROM machine_jobs WHERE agent_id=? AND machine_id=? "
                    "AND state='running'",
                    (grant.agent_id, grant.machine_id),
                )
                if changed
                else []
            )
        for job in jobs:
            await self.cancel(job["id"])

    async def heartbeat(self, machine_id: str, socket: Any) -> None:
        await self.store.machine(machine_id)
        if socket is None or self.connections.get(machine_id) is not socket:
            raise PermissionError("Connector session is no longer current")
        await self.store.write(
            "UPDATE machines SET last_seen=? WHERE id=?", (time.time(), machine_id)
        )
        await socket.send_json({"type": "lease", "seconds": LEASE_SECONDS})

    async def execute_on_machine(
        self,
        *,
        agent_id: str,
        machine_id: str,
        operation: str,
        args: dict[str, Any],
        trace_id: str,
        timeout_s: float = 120,
    ) -> dict[str, Any]:
        """Call only after the invoking tool has passed ToolExecutor and society policy."""
        await self.start()
        async with self._lock:
            machine = await self.store.machine(machine_id)
            grant = await self.store.grant(agent_id, machine_id)
            caps = MachineCapabilities.model_validate(machine["capabilities"])
            allowed = {
                "shell": grant.shell and caps.shell,
                "read": grant.files and caps.files,
                "write": grant.files and caps.files,
                "list": grant.files and caps.files,
                "manifest": grant.files and caps.files and machine["transport"] == "connector",
                "desktop": grant.desktop != "none" and caps.desktop,
                "agent_turn": caps.agent_runtime and machine["transport"] == "connector",
            }
            if operation == "agent_turn":
                rows = await self.store.rows(
                    "SELECT host_id,moving FROM placements WHERE agent_id=?", (agent_id,)
                )
                if not rows or rows[0]["host_id"] != machine_id or rows[0]["moving"]:
                    raise PermissionError("Agent is not assigned to this host or is moving")
            if not allowed.get(operation):
                raise PermissionError("This operation is not granted or available on the target")
            if operation == "shell" and grant.scope == "workspace" and not caps.workspace_isolation:
                raise PermissionError(
                    "Workspace-only shell needs OS isolation; "
                    "select an isolated account or grant account access"
                )
            command = MachineCommand(
                job_id=uuid4().hex,
                agent_id=agent_id,
                trace_id=trace_id,
                operation=cast(MachineOperation, operation),
                args=args,
                grant=grant,
                timeout_s=timeout_s,
            )
            unresolved = await self.store.rows(
                "SELECT command FROM machine_jobs WHERE agent_id=? AND machine_id=? "
                "AND state='uncertain'",
                (agent_id, machine_id),
            )
            for row in unresolved:
                previous = MachineCommand.model_validate_json(row["command"])
                if (
                    previous.operation == command.operation
                    and previous.args == command.args
                    and previous.grant.workspace == command.grant.workspace
                ):
                    raise PermissionError(
                        "An identical action has an uncertain outcome; reconcile it before retrying"
                    )
            socket = self.connections.get(machine_id)
            if machine["transport"] == "connector" and socket is None:
                raise ConnectionError("Target is offline; no command was dispatched")
            await self.store.write(
                "INSERT INTO machine_jobs VALUES (?,?,?,?,?,?,?,?)",
                (
                    command.job_id,
                    agent_id,
                    machine_id,
                    trace_id,
                    "queued",
                    command.model_dump_json(),
                    None,
                    time.time(),
                ),
            )
            future = asyncio.get_running_loop().create_future()
            self.pending[command.job_id] = future
            # Commit before sending: a crash here is uncertain, never an automatic retry.
            await self.store.write(
                "UPDATE machine_jobs SET state='running' WHERE id=?", (command.job_id,)
            )
            current = asyncio.current_task()
            assert current is not None
            self.running_calls[command.job_id] = current
            try:
                if operation == "agent_turn":
                    handler = self.rpc_handlers.get(agent_id)
                    if handler is None or handler[0] != machine_id:
                        raise PermissionError("No authorized hosted turn context")
                    self.rpc_handlers[command.job_id] = handler
                if socket is not None:
                    # Sending under the policy lock makes revocation/cancellation follow
                    # the command on the wire; a cancel cannot arrive before its command.
                    await asyncio.wait_for(
                        socket.send_json({"type": "command", "command": command.model_dump()}), 10
                    )
            except BaseException:
                await self.store.write(
                    "UPDATE machine_jobs SET state='uncertain' WHERE id=? AND state='running'",
                    (command.job_id,),
                )
                self.pending.pop(command.job_id, None)
                self.running_calls.pop(command.job_id, None)
                self.rpc_handlers.pop(command.job_id, None)
                raise
        try:
            if machine["transport"] == "ssh":
                from .ssh import execute_ssh

                result = await execute_ssh(machine, command)
                await self.complete(machine_id, result)
            return await asyncio.wait_for(asyncio.shield(future), timeout_s + LEASE_SECONDS + 5)
        except Exception:  # Failure after dispatch has an uncertain outcome; never replay.
            await self.store.write(
                "UPDATE machine_jobs SET state='uncertain' WHERE id=? AND state='running'",
                (command.job_id,),
            )
            raise
        except asyncio.CancelledError:
            await self.cancel(command.job_id)
            raise
        finally:
            self.pending.pop(command.job_id, None)
            self.rpc_handlers.pop(command.job_id, None)
            self.running_calls.pop(command.job_id, None)

    async def rpc(self, machine_id: str, job_id: str, method: str, payload: dict[str, Any]) -> Any:
        await self.store.machine(machine_id)
        handler = self.rpc_handlers.get(job_id)
        rows = await self.store.rows("SELECT state FROM machine_jobs WHERE id=?", (job_id,))
        if handler is None or handler[0] != machine_id or not rows or rows[0]["state"] != "running":
            raise PermissionError("Hosted run is no longer authorized")
        return await handler[1](method, payload)

    async def complete(self, machine_id: str, result: MachineResult) -> None:
        await self.store.machine(machine_id)
        body = result.model_dump()
        changed = await self.store.write(
            "UPDATE machine_jobs SET state=?,result=? WHERE id=? AND machine_id=? "
            "AND state IN ('running','uncertain')",
            (
                "uncertain" if result.uncertain else "succeeded" if result.success else "failed",
                result.model_dump_json(),
                result.job_id,
                machine_id,
            ),
        )
        if changed:
            future = self.pending.get(result.job_id)
            if future is not None and not future.done():
                future.set_result(body)

    async def cancel(self, job_id: str) -> None:
        async with self._lock:
            rows = await self.store.rows(
                "SELECT machine_id,state FROM machine_jobs WHERE id=?", (job_id,)
            )
            if not rows or rows[0]["state"] not in {"queued", "running", "uncertain"}:
                return
            row = rows[0]
            socket = self.connections.get(row["machine_id"])
            state = "cancelled" if row["state"] == "queued" else "uncertain"
            await self.store.write("UPDATE machine_jobs SET state=? WHERE id=?", (state, job_id))
        if socket is not None:
            await socket.send_json({"type": "cancel", "job_id": job_id})
        task = self.running_calls.get(job_id)
        if task is not None and task is not asyncio.current_task() and not task.cancelling():
            task.cancel()


_services: dict[Path, MachineService] = {}


def machine_service(data_dir: Path) -> MachineService:
    key = data_dir.resolve()
    if key not in _services:
        _services[key] = MachineService(key)
    return _services[key]


def peek_machine_service(data_dir: Path) -> MachineService | None:
    return _services.get(data_dir.resolve())
