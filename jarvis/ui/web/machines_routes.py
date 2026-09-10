"""Paired machines: management uses the UI guard, connectors authenticate separately."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from jarvis.machines.models import (
    LEASE_SECONDS,
    PROTOCOL_VERSION,
    MachineCapabilities,
    MachineGrant,
    MachineOS,
    MachineResult,
)
from jarvis.machines.service import MachineService, machine_service

from .society_routes import _runtime

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/machines", tags=["machines"])


async def service(request: Request | WebSocket) -> MachineService:
    runtime = await _runtime(request)
    result = machine_service(runtime.data_dir)
    await result.start()
    return result


class PairBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class MoveBody(BaseModel):
    host_id: str = Field(min_length=1, max_length=100)


class CloneBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    files: list[str] = Field(default_factory=list, max_length=1000)


class MachineTaskBody(BaseModel):
    machine_id: str = Field(min_length=1, max_length=100)
    task: str = Field(min_length=1, max_length=20000)


@router.post("/agents/{agent_id}/task", openapi_extra={"x-jarvis-dangerous": True})
async def assign_machine_task(
    agent_id: str, body: MachineTaskBody, request: Request
) -> dict[str, Any]:
    """Assign work to a permitted target without moving the agent's normal execution host."""
    from jarvis.society.events import MsgType

    runtime = await _runtime(request)
    hub = await service(request)
    agent = await runtime.roster.get(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent does not exist")
    if str(agent.state) != "active":
        raise HTTPException(409, "Activate this agent before assigning a task")
    from jarvis.agent_chat.runner_api import supports_api_runner
    from jarvis.society.chat_binding import pair_for

    if not supports_api_runner(pair_for(runtime.config(), agent)[0]):
        raise HTTPException(409, "This runner cannot enforce a remote target; select an API agent")
    if runtime.chat_service() is None:
        raise HTTPException(503, "The agent chat runtime is not ready")
    try:
        await hub.store.grant(agent_id, body.machine_id)
        await hub.store.machine(body.machine_id)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    event = await runtime.say(
        from_agent="user",
        to_agent=agent_id,
        text=body.task,
        trace_id="remote-task:" + uuid4().hex,
        msg_type=MsgType.ASSIGN,
        payload={"text": body.task, "target_machine_id": body.machine_id},
    )
    return {"event": event.model_dump()}


@router.get("/placements")
async def list_agent_hosts(request: Request) -> dict[str, Any]:
    """List execution locations; agents with no placement record run locally."""
    hub = await service(request)
    return {
        "placements": await hub.store.rows("SELECT * FROM placements"),
        "transfers": await hub.store.rows(
            "SELECT id,agent_id,source,target,state,"
            "json_object('error',json_extract(manifest,'$.error')) AS manifest "
            "FROM transfers ORDER BY rowid DESC LIMIT 100"
        ),
    }


@router.post("/agents/{agent_id}/move", openapi_extra={"x-jarvis-dangerous": True})
async def move_agent(agent_id: str, body: MoveBody, request: Request) -> dict[str, Any]:
    """Drain work and move its verified workspace before changing the execution host."""
    from jarvis.machines.transfers import AgentTransfer

    runtime = await _runtime(request)
    hub = await service(request)
    if await runtime.roster.get(agent_id) is None:
        raise HTTPException(404, "Agent does not exist")
    rows = await hub.store.rows("SELECT host_id FROM placements WHERE agent_id=?", (agent_id,))
    transfer_id = uuid4().hex
    await hub.store.write(
        "INSERT INTO transfers(id,agent_id,source,target,state) VALUES (?,?,?,?,?)",
        (transfer_id, agent_id, rows[0]["host_id"] if rows else "local", body.host_id, "queued"),
    )

    async def perform() -> None:
        try:
            await AgentTransfer(runtime).move(agent_id, body.host_id, transfer_id=transfer_id)
        except asyncio.CancelledError:
            await hub.store.write(
                "UPDATE transfers SET state='cancelled' WHERE id=? AND state!='complete'",
                (transfer_id,),
            )
            raise
        except Exception as exc:  # noqa: BLE001 — background failures are persisted for UI/CLI
            log.info("Agent transfer failed: %s", type(exc).__name__)
            await hub.store.write(
                "UPDATE transfers SET state='failed',manifest=json_set(manifest,'$.error',?) "
                "WHERE id=? AND state!='complete'",
                (str(exc), transfer_id),
            )
        finally:
            hub.transfer_tasks.pop(transfer_id, None)

    hub.transfer_tasks[transfer_id] = asyncio.create_task(perform())
    return {"id": transfer_id, "state": "queued"}


@router.get("/transfers/{transfer_id}")
async def get_machine_transfer(transfer_id: str, request: Request) -> dict[str, Any]:
    """Read a durable handoff result, including a failure reason."""
    hub = await service(request)
    rows = await hub.store.rows("SELECT * FROM transfers WHERE id=?", (transfer_id,))
    if not rows:
        raise HTTPException(404, "Transfer does not exist")
    return {"transfer": rows[0]}


@router.post("/transfers/{transfer_id}/cancel", openapi_extra={"x-jarvis-dangerous": True})
async def cancel_machine_transfer(transfer_id: str, request: Request) -> dict[str, bool]:
    """Cancel an unfinished handoff, leaving its source responsible for the agent."""
    hub = await service(request)
    task = hub.transfer_tasks.get(transfer_id)
    if task is not None:
        task.cancel()
    return {"requested": task is not None}


@router.post("/agents/{agent_id}/clone", openapi_extra={"x-jarvis-dangerous": True})
async def copy_agent(agent_id: str, body: CloneBody, request: Request) -> dict[str, Any]:
    """Copy settings and knowledge into a paused new identity without logins or chat history."""
    from jarvis.machines.cloning import clone_agent

    try:
        return await clone_agent(await _runtime(request), agent_id, body.name, body.files)
    except (PermissionError, ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


class SshProbeBody(BaseModel):
    host: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9.:_-]+$")
    port: int = Field(default=22, ge=1, le=65535)


class SshBody(SshProbeBody):
    name: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=100)
    host_key: str = Field(min_length=1, max_length=16384)
    private_key: str = Field(min_length=1, max_length=32768, repr=False)
    os: MachineOS


@router.post("/ssh/probe")
async def probe_ssh_machine(body: SshProbeBody, request: Request) -> dict[str, str]:
    """Read an SSH public host key for explicit fingerprint confirmation."""
    from jarvis.machines.ssh import probe_key

    await service(request)
    try:
        return await probe_key(body.host, body.port)
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/ssh", openapi_extra={"x-jarvis-dangerous": True})
async def add_ssh_machine(body: SshBody, request: Request) -> dict[str, Any]:
    """Register a confirmed SSH host; its private key stays in the secret store."""
    import json

    from jarvis.core.config import set_secret
    from jarvis.machines.ssh import ssh_module

    hub = await service(request)
    try:
        ssh = ssh_module()
        ssh.import_public_key(body.host_key)
        ssh.import_private_key(body.private_key)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, "SSH key could not be loaded") from exc
    machine_id = uuid4().hex
    if not await asyncio.to_thread(set_secret, "machine_ssh_" + machine_id, body.private_key):
        raise HTTPException(503, "Could not save the SSH credential")
    caps = MachineCapabilities(
        os=body.os, shell_name="PowerShell" if body.os == "windows" else "sh"
    )
    settings = body.model_dump(include={"host", "port", "username", "host_key"})
    await hub.store.write(
        "INSERT INTO machines(id,name,transport,capabilities,settings) VALUES (?,?,?,?,?)",
        (machine_id, body.name, "ssh", caps.model_dump_json(), json.dumps(settings)),
    )
    return {"machine": await hub.store.machine(machine_id)}


@router.get("")
async def list_machines(request: Request) -> dict[str, Any]:
    """List connected computers and their actual capabilities."""
    hub = await service(request)
    return {"machines": await hub.list_machines(), "protocol_version": PROTOCOL_VERSION}


@router.post("/pairing", openapi_extra={"x-jarvis-dangerous": True})
async def pair_machine(body: PairBody, request: Request) -> dict[str, Any]:
    """Create a single-use pairing code valid for ten minutes."""
    hub = await service(request)
    return {"code": await hub.store.pair_code(body.name), "expires_in": 600}


@router.delete("/{machine_id}", openapi_extra={"x-jarvis-dangerous": True})
async def revoke_machine(machine_id: str, request: Request) -> dict[str, bool]:
    """Revoke the computer and close its connector session."""
    await (await service(request)).revoke(machine_id)
    return {"revoked": True}


@router.get("/grants/{agent_id}")
async def list_machine_grants(agent_id: str, request: Request) -> dict[str, Any]:
    """List explicit machine permissions for an agent."""
    import json

    hub = await service(request)
    rows = await hub.store.rows("SELECT body FROM grants WHERE agent_id=?", (agent_id,))
    return {"grants": [json.loads(row["body"]) for row in rows]}


@router.put("/grants", openapi_extra={"x-jarvis-dangerous": True})
async def grant_machine(body: MachineGrant, request: Request) -> dict[str, Any]:
    """Set this agent's device permissions without granting administrator access."""
    runtime = await _runtime(request)
    if await runtime.roster.get(body.agent_id) is None:
        raise HTTPException(404, "Agent does not exist")
    hub = await service(request)
    try:
        machine = await hub.store.machine(body.machine_id)
        caps = MachineCapabilities.model_validate(machine["capabilities"])
        if body.shell and body.scope == "workspace" and not caps.workspace_isolation:
            raise PermissionError("Workspace-only shell is unavailable without OS isolation")
        if body.desktop != "none" and not caps.desktop:
            raise PermissionError("This machine has no available desktop session")
        await hub.set_grant(body)
        if body.desktop != "none" and body.machine_id in hub.connections:
            await hub.connections[body.machine_id].send_json({"type": "desktop_resume"})
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"grant": body.model_dump()}


@router.get("/jobs")
async def list_machine_jobs(request: Request, agent_id: str | None = None) -> dict[str, Any]:
    """List persistent remote job outcomes, including uncertain executions."""
    hub = await service(request)
    if agent_id:
        rows = await hub.store.rows(
            "SELECT id,agent_id,machine_id,trace_id,state,NULL AS result,created FROM machine_jobs "
            "WHERE agent_id=? ORDER BY created DESC LIMIT 100",
            (agent_id,),
        )
    else:
        rows = await hub.store.rows(
            "SELECT id,agent_id,machine_id,trace_id,state,NULL AS result,created FROM machine_jobs "
            "ORDER BY created DESC LIMIT 100",
        )
    return {"jobs": rows}


@router.get("/jobs/{job_id}")
async def get_machine_job(job_id: str, request: Request) -> dict[str, Any]:
    """Fetch a job's output on demand so status polling never retransmits screenshots."""
    rows = await (await service(request)).store.rows(
        "SELECT id,agent_id,machine_id,trace_id,state,result,created FROM machine_jobs WHERE id=?",
        (job_id,),
    )
    if not rows:
        raise HTTPException(404, "Job does not exist")
    return {"job": rows[0]}


@router.post("/{machine_id}/desktop/stop", openapi_extra={"x-jarvis-dangerous": True})
async def stop_machine_desktop(machine_id: str, request: Request) -> dict[str, bool]:
    """Release the connector's desktop sessions without stopping the host's desktop app."""
    hub = await service(request)
    socket = hub.connections.get(machine_id)
    if socket is None:
        raise HTTPException(409, "Device is offline; its execution lease will expire")
    await socket.send_json({"type": "desktop_stop"})
    rows = await hub.store.rows("SELECT body FROM grants WHERE machine_id=?", (machine_id,))
    for row in rows:
        grant = MachineGrant.model_validate_json(row["body"])
        await hub.store.put_grant(grant.model_copy(update={"desktop": "none"}))
    return {"requested": True}


class ResolveBody(BaseModel):
    outcome: Literal["succeeded", "failed"]
    note: str = Field(min_length=1, max_length=2000)


@router.post("/jobs/{job_id}/resolve", openapi_extra={"x-jarvis-dangerous": True})
async def resolve_machine_job(job_id: str, body: ResolveBody, request: Request) -> dict[str, bool]:
    """Record a human-verified outcome before allowing an uncertain action to be repeated."""
    import json

    hub = await service(request)
    changed = await hub.store.write(
        "UPDATE machine_jobs SET state=?,result=? WHERE id=? AND state='uncertain'",
        (body.outcome, json.dumps({"verified_by": "user", "note": body.note}), job_id),
    )
    if not changed:
        raise HTTPException(409, "Only uncertain jobs can be reconciled")
    return {"resolved": True}


@router.post("/jobs/{job_id}/cancel", openapi_extra={"x-jarvis-dangerous": True})
async def cancel_machine_job(job_id: str, request: Request) -> dict[str, bool]:
    """Request cancellation; an uncertain result is not presented as successful cancellation."""
    await (await service(request)).cancel(job_id)
    return {"requested": True}


@router.websocket("/connect")
async def connect_machine(socket: WebSocket) -> None:
    """Dedicated first-frame authentication; device credentials authorize only this socket."""
    from .surface_security import is_secure_or_loopback

    if not is_secure_or_loopback(socket.scope) or socket.headers.get("origin"):
        await socket.close(code=4403)
        return
    await socket.accept()
    machine_id = None
    hub = None
    calls: dict[str, asyncio.Task] = {}

    async def answer(message: dict[str, Any]) -> None:
        request_id = str(message["request_id"])
        try:
            assert hub is not None and machine_id is not None
            result = await hub.rpc(
                machine_id, str(message["job_id"]), str(message["method"]), message["payload"]
            )
            await socket.send_json(
                {"type": "rpc_result", "request_id": request_id, "result": result}
            )
        except Exception as exc:  # noqa: BLE001 — RPC failures are returned without stopping heartbeats
            log.info("Hosted RPC failed: %s", type(exc).__name__)
            await socket.send_json(
                {"type": "rpc_result", "request_id": request_id, "error": str(exc)}
            )
        except asyncio.CancelledError:
            try:
                await socket.send_json(
                    {
                        "type": "rpc_result",
                        "request_id": request_id,
                        "error": "Remote operation was cancelled",
                    }
                )
            except (RuntimeError, WebSocketDisconnect):
                log.debug("Cancelled RPC socket already closed")
            raise
        finally:
            calls.pop(str(message["job_id"]), None)

    try:
        hello = await asyncio.wait_for(socket.receive_json(), 10)
        if hello.get("version") != PROTOCOL_VERSION:
            raise PermissionError("Connector protocol version mismatch")
        caps = MachineCapabilities.model_validate(hello["capabilities"])
        hub = await service(socket)
        token = str(hello.get("token") or "")
        issued = None
        if token:
            machine_id = await hub.store.authenticate(token)
        elif hello.get("pairing_code"):
            machine_id, issued = await hub.store.enroll(str(hello["pairing_code"]), caps)
        if not machine_id:
            raise PermissionError("Invalid connector credential")
        await hub.attach(machine_id, socket, caps)
        await socket.send_json(
            {
                "type": "welcome",
                "version": PROTOCOL_VERSION,
                "machine_id": machine_id,
                "token": issued,
                "lease_seconds": LEASE_SECONDS,
            }
        )
        while True:
            message = await asyncio.wait_for(socket.receive_json(), 15)
            if message.get("type") == "heartbeat":
                await hub.heartbeat(machine_id, socket)
            elif message.get("type") == "result":
                result = MachineResult.model_validate(message["result"])
                await hub.complete(machine_id, result)
                await socket.send_json({"type": "ack", "job_id": result.job_id})
            elif message.get("type") == "rpc":
                job_id = str(message["job_id"])
                if job_id in calls or len(calls) >= 32:
                    raise PermissionError("Hosted RPC concurrency exceeded")
                calls[job_id] = asyncio.create_task(answer(message))
            else:
                raise ValueError("Unsupported connector message")
    except WebSocketDisconnect:
        log.info("Machine connector disconnected: %s", machine_id)
    except TimeoutError:
        log.info("Machine heartbeat expired: %s", machine_id)
        await socket.close(code=1012)
    except (PermissionError, ValueError, KeyError):
        log.info("Machine connection refused or expired: %s", machine_id)
        await socket.close(code=4403)
    finally:
        for task in calls.values():
            task.cancel()
        await asyncio.gather(*list(calls.values()), return_exceptions=True)
        if machine_id and hub is not None:
            await hub.detach(machine_id, socket)
