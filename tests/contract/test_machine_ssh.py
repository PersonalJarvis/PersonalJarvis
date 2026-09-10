"""Real loopback SSH handshakes prove host-key checks before shell execution."""
# ruff: noqa: S604 -- MachineGrant.shell is a permission flag, not subprocess shell=True.

import pytest

from jarvis.machines.models import MachineCommand, MachineGrant
from jarvis.machines.ssh import execute_ssh, probe_key

ssh = pytest.importorskip("asyncssh")


@pytest.mark.asyncio
async def test_pinned_host_key_and_changed_key_refusal(monkeypatch):
    from jarvis.core import config

    host_key = ssh.generate_private_key("ssh-ed25519")
    user_key = ssh.generate_private_key("ssh-ed25519")
    monkeypatch.setattr(config, "get_secret", lambda name: user_key.export_private_key().decode())
    calls = []

    class Server(ssh.SSHServer):
        def begin_auth(self, username):
            return False

    async def process(proc):
        calls.append(proc.command)
        proc.stdout.write("remote-proof")
        proc.exit(0)

    async with ssh.create_server(
        Server, "127.0.0.1", 0, server_host_keys=[host_key], process_factory=process
    ) as listener:
        port = listener.get_port()
        found = await probe_key("127.0.0.1", port)
        assert found["fingerprint"] == host_key.get_fingerprint()
        machine = {
            "id": "m",
            "capabilities": {"os": "linux"},
            "settings": {
                "host": "127.0.0.1",
                "port": port,
                "username": "test",
                "host_key": found["host_key"],
            },
        }
        command = MachineCommand(
            job_id="j",
            agent_id="a",
            trace_id="t",
            operation="shell",
            args={"command": "echo hello"},
            grant=MachineGrant(
                agent_id="a",
                machine_id="m",
                scope="account",
                workspace="/srv/space folder",
                shell=True,
            ),
        )
        result = await execute_ssh(machine, command)
        assert result.success and result.output["output"] == "remote-proof"
        assert calls == ["cd '/srv/space folder' && sh -c 'echo hello'"]
        machine["settings"]["host_key"] = (
            ssh.generate_private_key("ssh-ed25519").export_public_key().decode()
        )
        with pytest.raises(ssh.HostKeyNotVerifiable):
            await execute_ssh(machine, command)
        assert len(calls) == 1
