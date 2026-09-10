"""Outbound-only connector. Secrets are entered locally and stored through get_secret."""

from __future__ import annotations

import asyncio
import getpass
import hashlib
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

from .connections import wait_for_connection
from .models import PROTOCOL_VERSION, MachineCapabilities, MachineCommand, MachineOS
from .runner import ConnectorRunner

log = logging.getLogger(__name__)


def capabilities() -> MachineCapabilities:
    from jarvis.agent_chat.tools import shell_argv

    system = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(sys.platform)
    if system is None:
        raise RuntimeError("This operating system is not supported")
    return MachineCapabilities(
        os=cast(MachineOS, system),
        agent_runtime=True,
        shell_name=shell_argv()[0],
        reason="Shell and files ready; desktop and agent hosting require their runtime components",
    )


def validate_hub(url: str) -> str:
    value = urlsplit(url)
    if value.username or value.password or value.query or value.fragment:
        raise ValueError("Hub URL must not contain credentials, query parameters or fragments")
    if value.scheme != "wss" and not (
        value.scheme == "ws" and value.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("A remote hub requires wss:// with a valid TLS certificate")
    if not value.hostname or value.path not in {"", "/", "/api/machines/connect"}:
        raise ValueError("Enter the hub origin, without a path")
    return (
        url.rstrip("/")
        if value.path == "/api/machines/connect"
        else url.rstrip("/") + "/api/machines/connect"
    )


async def connected(socket, runner: ConnectorRunner) -> None:
    responses: dict[str, asyncio.Future] = {}

    async def rpc(job_id: str, method: str, payload: dict) -> dict:
        request_id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        responses[request_id] = future
        try:
            await socket.send(
                json.dumps(
                    {
                        "type": "rpc",
                        "job_id": job_id,
                        "request_id": request_id,
                        "method": method,
                        "payload": payload,
                    }
                )
            )
            return await asyncio.wait_for(future, 900)
        finally:
            responses.pop(request_id, None)

    runner.rpc = rpc

    async def heartbeats() -> None:
        while True:
            await socket.send(json.dumps({"type": "heartbeat"}))
            await asyncio.sleep(5)

    async def watchdog() -> None:
        while True:
            await asyncio.sleep(0.2)
            await runner.desktops.expire()
            try:
                runner.check_lease()
            except PermissionError:
                await socket.close()
                return

    async def execute(command: MachineCommand) -> None:
        try:
            result = await runner.run(command)
            await socket.send(json.dumps({"type": "result", "result": result.model_dump()}))
        finally:
            runner.tasks.pop(command.job_id, None)

    for result in await runner.completed():
        await socket.send(json.dumps({"type": "result", "result": result}))
    heartbeat = asyncio.create_task(heartbeats())
    watch = asyncio.create_task(watchdog())
    try:
        async for raw in socket:
            message = json.loads(raw)
            if message["type"] == "rpc_result":
                future = responses.get(message["request_id"])
                if future is not None and not future.done():
                    if message.get("error"):
                        future.set_exception(RuntimeError(message["error"]))
                    else:
                        future.set_result(message["result"])
            elif message["type"] == "lease":
                runner.renew(float(message["seconds"]))
            elif message["type"] == "command":
                command = MachineCommand.model_validate(message["command"])
                if command.job_id not in runner.tasks:
                    runner.tasks[command.job_id] = asyncio.create_task(execute(command))
            elif message["type"] == "cancel":
                task = runner.tasks.get(message["job_id"])
                if task and not task.cancelling():
                    task.cancel()
            elif message["type"] == "ack":
                await runner.acknowledge(message["job_id"])
                for result in await runner.completed():
                    await socket.send(json.dumps({"type": "result", "result": result}))
            elif message["type"] == "desktop_stop":
                await runner.desktops.close(pause=True)
            elif message["type"] == "desktop_resume":
                runner.desktops.paused = False
    finally:
        runner.deadline = 0
        runner.rpc = None
        tasks = [heartbeat, watch, *runner.tasks.values()]
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.desktops.close()


async def run_connector(
    hub: str, root: Path, *, pair: bool = False, desktop_mode: str = "none"
) -> None:
    from websockets.asyncio.client import connect

    from jarvis.core.config import get_secret, set_secret

    url = validate_hub(hub)
    key = "machine_connector_" + hashlib.sha256(url.encode()).hexdigest()[:24]
    token = None if pair else await asyncio.to_thread(get_secret, key)
    code = (
        await asyncio.to_thread(getpass.getpass, "One-time pairing code: ") if not token else None
    )
    runner = ConnectorRunner(root, desktop_mode=desktop_mode)
    failures = 0
    while True:
        try:
            await wait_for_connection()
            async with connect(url, max_size=4 * 1024 * 1024, open_timeout=15) as socket:
                caps = capabilities()
                desktop, isolated, reason = await asyncio.to_thread(runner.desktops.probe)
                caps.desktop, caps.isolated_desktop = desktop, isolated
                caps.reason = reason
                await socket.send(
                    json.dumps(
                        {
                            "version": PROTOCOL_VERSION,
                            "token": token,
                            "pairing_code": code,
                            "capabilities": caps.model_dump(),
                        }
                    )
                )
                reply = json.loads(await asyncio.wait_for(socket.recv(), 15))
                if reply.get("type") != "welcome":
                    raise PermissionError("Connector authentication was refused")
                if reply.get("token"):
                    token = reply["token"]
                    if not await asyncio.to_thread(set_secret, key, token):
                        raise PermissionError("Could not persist the connector credential")
                    code = None
                runner.renew(reply["lease_seconds"])
                connected_at = time.monotonic()
                log.info("Connected to hub as machine %s", reply["machine_id"])
                await connected(socket, runner)
                if time.monotonic() - connected_at >= 30:
                    failures = 0
        except asyncio.CancelledError:
            raise
        except PermissionError:
            raise
        except Exception as exc:  # noqa: BLE001 — reconnect is bounded and logged
            log.warning("Connector disconnected: %s", type(exc).__name__)
            if getattr(exc, "code", None) == 4403:
                raise PermissionError("Device was revoked; pair it again") from exc
        failures += 1
        if failures >= 10:
            raise ConnectionError("Ten reconnect attempts failed; check the hub and reconnect")
        await asyncio.sleep(random.SystemRandom().uniform(1, min(60, 2**failures)))
