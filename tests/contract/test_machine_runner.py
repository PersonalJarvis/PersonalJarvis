"""Lease expiry, path boundaries and persistent duplicate handling on the connector."""
# ruff: noqa: S604 -- MachineGrant.shell is a permission flag, not subprocess shell=True.

import asyncio
import time

import pytest

from jarvis.machines.connector import validate_hub
from jarvis.machines.models import MachineCommand, MachineGrant
from jarvis.machines.runner import ConnectorRunner


def command(tmp_path, **values):
    return MachineCommand(
        job_id="job",
        agent_id="a",
        trace_id="t",
        operation="write",
        args={"path": "file.txt", "text": "hello"},
        grant=MachineGrant(agent_id="a", machine_id="m", workspace=str(tmp_path), files=True),
        **values,
    )


@pytest.mark.asyncio
async def test_restart_does_not_repeat_effect(tmp_path):
    runner = ConnectorRunner(tmp_path / "state")
    runner.renew(30)
    job = command(tmp_path)
    assert (await runner.run(job)).success
    (tmp_path / "file.txt").write_text("subsequent edit")
    restarted = ConnectorRunner(tmp_path / "state")
    restarted.renew(30)
    assert (await restarted.run(job)).success
    assert (tmp_path / "file.txt").read_text() == "subsequent edit"
    changed = job.model_copy(update={"args": {"path": "file.txt", "text": "bad"}})
    with pytest.raises(PermissionError, match="different arguments"):
        await restarted.run(changed)


@pytest.mark.asyncio
async def test_expired_lease_and_parent_escape(tmp_path):
    runner = ConnectorRunner(tmp_path / "state")
    with pytest.raises(PermissionError, match="expired"):
        await runner.run(command(tmp_path))
    runner.renew(30)
    job = command(tmp_path).model_copy(update={"args": {"path": "../escape.txt", "text": "bad"}})
    result = await runner.run(job)
    assert not result.success
    assert "workspace" in result.error
    assert not (tmp_path.parent / "escape.txt").exists()


@pytest.mark.asyncio
async def test_real_shell_output_and_timeout(tmp_path):
    runner = ConnectorRunner(tmp_path / "state")
    runner.renew(30)
    job = command(tmp_path).model_copy(
        update={
            "operation": "shell",
            "args": {"command": "echo remote-proof"},
            "grant": MachineGrant(
                agent_id="a", machine_id="m", workspace=str(tmp_path), scope="account", shell=True
            ),
        }
    )
    result = await runner.run(job)
    assert result.success, result.error
    assert "remote-proof" in result.output["output"]
    runner.deadline = time.monotonic() + 0.2
    long_job = job.model_copy(update={"job_id": "long", "args": {"command": "sleep 10"}})
    result = await asyncio.wait_for(runner.run(long_job), 6)
    assert not result.success


@pytest.mark.parametrize(
    "url",
    [
        "ws://example.com",
        "wss://user:secret@example.com",
        "wss://example.com?token=x",
        "file:///tmp/x",
    ],
)
def test_remote_hub_requires_tls(url):
    with pytest.raises(ValueError):
        validate_hub(url)


def test_loopback_test_transport_and_secure_remote():
    assert validate_hub("ws://127.0.0.1:8000") == "ws://127.0.0.1:8000/api/machines/connect"
    assert validate_hub("wss://hub.example") == "wss://hub.example/api/machines/connect"
