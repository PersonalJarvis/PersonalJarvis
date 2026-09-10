"""Machine trust and dispatch contracts are independent of the host OS."""
# ruff: noqa: S604 -- MachineGrant.shell is a permission flag, not subprocess shell=True.

import asyncio

import pytest

from jarvis.machines.models import MachineCapabilities, MachineGrant, MachineResult
from jarvis.machines.service import MachineService


@pytest.mark.asyncio
@pytest.mark.parametrize("os_name", ["windows", "macos", "linux"])
async def test_pair_once_and_revoke(tmp_path, os_name):
    service = MachineService(tmp_path)
    await service.start()
    caps = MachineCapabilities(os=os_name)
    code = await service.store.pair_code("Test host")
    machine_id, token = await service.store.enroll(code, caps)
    assert await service.store.authenticate(token) == machine_id
    with pytest.raises(PermissionError):
        await service.store.enroll(code, caps)
    await service.revoke(machine_id)
    assert await service.store.authenticate(token) is None


class Socket:
    def __init__(self):
        self.messages = asyncio.Queue()

    async def send_json(self, value):
        await self.messages.put(value)

    async def close(self, **kwargs):
        pass  # Fake transport records only dispatch; revocation is asserted in the store.


@pytest.mark.asyncio
async def test_disconnect_never_replays_and_result_is_device_bound(tmp_path):
    service = MachineService(tmp_path)
    await service.start()
    caps = MachineCapabilities(os="linux")
    first, _ = await service.store.enroll(await service.store.pair_code("one"), caps)
    other, _ = await service.store.enroll(await service.store.pair_code("two"), caps)
    await service.store.put_grant(
        MachineGrant(agent_id="a", machine_id=first, workspace="/work", scope="account", shell=True)
    )
    socket = Socket()
    await service.attach(first, socket, caps)
    run = asyncio.create_task(
        service.execute_on_machine(
            agent_id="a",
            machine_id=first,
            operation="shell",
            args={"command": "echo hi"},
            trace_id="t",
        )
    )
    sent = await asyncio.wait_for(socket.messages.get(), 5)
    job_id = sent["command"]["job_id"]
    await service.complete(other, MachineResult(job_id=job_id, success=True))
    assert not run.done()
    await service.detach(first, socket)
    assert not (await run)["success"]
    next_socket = Socket()
    await service.attach(first, next_socket, caps)
    assert next_socket.messages.empty()
    await service.complete(first, MachineResult(job_id=job_id, success=True, output="hi"))
    assert (await service.store.rows("SELECT state FROM machine_jobs"))[0]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_working_directory_is_not_shell_isolation(tmp_path):
    service = MachineService(tmp_path)
    await service.start()
    caps = MachineCapabilities(os="windows")
    machine_id, _ = await service.store.enroll(await service.store.pair_code("host"), caps)
    await service.store.put_grant(
        MachineGrant(agent_id="a", machine_id=machine_id, workspace="C:/work", shell=True)
    )
    await service.attach(machine_id, Socket(), caps)
    with pytest.raises(PermissionError, match="OS isolation"):
        await service.execute_on_machine(
            agent_id="a",
            machine_id=machine_id,
            operation="shell",
            args={"command": "echo hello"},
            trace_id="t",
        )
    assert not await service.store.rows("SELECT * FROM machine_jobs")


@pytest.mark.asyncio
async def test_rights_revocation_follows_dispatch_and_cancels_the_run(tmp_path):
    class PausingSocket(Socket):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def send_json(self, value):
            if value["type"] == "command":
                self.entered.set()
                await self.release.wait()
            await super().send_json(value)

    service = MachineService(tmp_path)
    await service.start()
    caps = MachineCapabilities(os="linux")
    machine_id, _ = await service.store.enroll(await service.store.pair_code("host"), caps)
    grant = MachineGrant(
        agent_id="a", machine_id=machine_id, workspace="/work", scope="account", shell=True
    )
    await service.set_grant(grant)
    socket = PausingSocket()
    await service.attach(machine_id, socket, caps)
    run = asyncio.create_task(
        service.execute_on_machine(
            agent_id="a",
            machine_id=machine_id,
            operation="shell",
            args={"command": "echo hello"},
            trace_id="t",
        )
    )
    await asyncio.wait_for(socket.entered.wait(), 2)
    revoke = asyncio.create_task(service.set_grant(grant.model_copy(update={"shell": False})))
    await asyncio.sleep(0)
    assert not revoke.done()
    socket.release.set()
    await asyncio.wait_for(revoke, 2)
    with pytest.raises(asyncio.CancelledError):
        await run
    assert (await socket.messages.get())["type"] == "command"
    assert (await socket.messages.get())["type"] == "cancel"
    assert (await service.store.rows("SELECT state FROM machine_jobs"))[0]["state"] == "uncertain"
