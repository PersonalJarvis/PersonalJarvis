"""A society agent placed on another computer: data, shell and CLI over SSH.

Runs against the real in-process SSH server (``tests/fakes/fake_ssh_server``):
the roster stores and validates ``computer_id``, the agent's shell commands
run inside ``~/jarvis-agents/<agent>`` on that machine, and a CLI turn is
started there with a rewritten argv and read back line by line.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat import remote_cli
from jarvis.computers import identity, service
from jarvis.computers.service import ComputerService
from jarvis.computers.store import ComputerStore
from jarvis.society import remote
from jarvis.society.failure_reasons import FailureReason
from jarvis.society.roster import Roster, RosterError
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.store import SocietyStore
from jarvis.ui.web.society_routes import router
from tests.fakes.fake_ssh_server import TEST_PASSWORD, FakeSshServer


class _SecretBox:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str, env_fallback: str | None = None) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True

    def delete(self, key: str) -> bool:
        self.values.pop(key, None)
        return True


@pytest.fixture
def computers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ComputerService:
    """A sandboxed Computers service shared by the roster check and the SSH path."""
    box = _SecretBox()
    for module in (identity, service):
        monkeypatch.setattr(module, "get_secret", box.get)
        monkeypatch.setattr(module, "set_secret", box.set)
        if hasattr(module, "delete_secret"):
            monkeypatch.setattr(module, "delete_secret", box.delete)
    path = tmp_path / "computers.json"
    monkeypatch.setattr("jarvis.computers.store.default_path", lambda: path)
    svc = ComputerService(ComputerStore(path))
    monkeypatch.setattr(service, "_SERVICE", svc)
    return svc


@pytest.fixture
async def ssh_server():  # noqa: ANN201
    server = FakeSshServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def vps(computers: ComputerService, ssh_server: FakeSshServer):  # noqa: ANN201
    return await computers.add_server(
        name="VPS", host="127.0.0.1", port=ssh_server.port, auth="password", password=TEST_PASSWORD
    )


@pytest.fixture
async def roster(tmp_path: Path):  # noqa: ANN201
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    try:
        yield Roster(store)
    finally:
        await store.close()


# -- data -----------------------------------------------------------------------


async def test_computer_id_round_trips_and_clears(roster: Roster, vps) -> None:  # noqa: ANN001
    scout, _ = await roster.create(name="Scout", computer_id=vps.id)
    assert scout.computer_id == vps.id
    assert scout.to_dict()["computer_id"] == vps.id

    again = await roster.get("scout")
    assert again is not None and again.computer_id == vps.id

    moved = await roster.update("scout", {"computer_id": ""})
    assert moved.computer_id is None


async def test_unknown_computer_is_refused(roster: Roster, computers) -> None:  # noqa: ANN001
    with pytest.raises(RosterError) as caught:
        await roster.create(name="Scout", computer_id="c_nope")
    assert caught.value.reason is FailureReason.TARGET_UNKNOWN


async def test_the_lead_always_stays_here(roster: Roster, vps) -> None:  # noqa: ANN001
    await roster.list()  # seeds nothing, but the lead can be created explicitly
    lead, _ = await roster.create(name="Jarvis", tier="lead")
    with pytest.raises(RosterError):
        await roster.update(lead.agent_id, {"computer_id": vps.id})


def test_create_route_validates_the_computer(tmp_path: Path, computers) -> None:  # noqa: ANN001
    runtime = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        mission_manager=lambda: None,
        mission_bus=lambda: SimpleNamespace(subscribe_all=lambda h: lambda: None),
        brain_tools=dict,
    )
    app = FastAPI()
    app.include_router(router)
    app.state.society = None
    app.state.society_factory = lambda: runtime
    with TestClient(app) as client:
        try:
            refused = client.post(
                "/api/society/agents", json={"name": "Scout", "computer_id": "c_x"}
            )
            assert refused.status_code == 404
            local = client.post("/api/society/agents", json={"name": "Scout"})
            assert local.json()["agent"]["computer_id"] is None
        finally:
            assert client.portal is not None
            client.portal.call(runtime.close)


# -- shell ------------------------------------------------------------------------


async def test_shell_runs_inside_the_remote_workspace(
    tmp_path: Path, vps, ssh_server: FakeSshServer
) -> None:  # noqa: ANN001
    async def handler(command: str, process: Any) -> bool:
        if command.startswith("mkdir -p"):
            process.stdout.write("hello from the vps\n")
            process.stderr.write("a warning\n")
            process.exit(0)
            return True
        return False

    ssh_server.state.handler = handler
    workspace = tmp_path / "ws"
    (workspace / "sub").mkdir(parents=True)
    agent = SimpleNamespace(agent_id="scout", computer_id=vps.id)
    backend = remote.backend_for(agent, workspace)
    assert backend.name == "ssh"

    result = await backend.run("ls -la", cwd=workspace / "sub", timeout_s=30)

    assert result.ok
    assert "hello from the vps" in result.output and "a warning" in result.output
    sent = ssh_server.state.commands[-1]
    assert '"$HOME"/jarvis-agents/scout/sub' in sent
    assert sent.endswith("&& ls -la")


async def test_shell_reports_an_unreachable_computer(tmp_path: Path, computers) -> None:  # noqa: ANN001
    gone = await computers.add_server(name="Gone", host="127.0.0.1", port=1)
    backend = remote.SshShellBackend(gone.id, "scout", workspace=tmp_path)

    result = await backend.run("true", cwd=tmp_path, timeout_s=10)

    assert result.failed_to_start and "Could not reach" in result.output


def test_local_agents_keep_the_local_shell(tmp_path: Path) -> None:
    agent = SimpleNamespace(agent_id="scout", computer_id=None)
    assert remote.backend_for(agent, tmp_path).name == "local"


# -- CLI ----------------------------------------------------------------------------


def test_argv_is_rewritten_for_the_remote_machine() -> None:
    argv = [
        "node",
        r"C:\npm\node_modules\@anthropic-ai\claude-code\cli.js",
        "--print",
        "--mcp-config",
        '{"mcpServers": {}}',
        "--append-system-prompt-file",
        r"C:\tmp\identity.md",
        "exec",
        "--cd",
        r"C:\data\society\scout\workspace",
        "-c",
        'mcp_servers.jarvis.url="http://127.0.0.1:47821"',
        "-c",
        'sandbox_mode="workspace-write"',
    ]
    out = remote_cli.remote_argv(
        argv,
        binary="claude",
        local_cwd=r"C:\data\society\scout\workspace",
        remote_cwd="/root/jarvis-agents/scout",
        system_prompt_files={r"C:\tmp\identity.md": "You are Scout."},
    )
    assert out == [
        "claude",
        "--print",
        "--append-system-prompt",
        "You are Scout.",
        "exec",
        "--cd",
        "/root/jarvis-agents/scout",
        "-c",
        'sandbox_mode="workspace-write"',
    ]


def test_remote_env_carries_no_local_secrets() -> None:
    env = remote_cli.remote_env(
        {"ANTHROPIC_API_KEY": "sk-x", "CLAUDE_CONFIG_DIR": r"C:\x", "NO_COLOR": "1"}
    )
    assert env == {"NO_COLOR": "1", "CI": "1"}


async def test_cli_turn_streams_from_the_remote_process(vps, ssh_server: FakeSshServer) -> None:  # noqa: ANN001
    async def handler(command: str, process: Any) -> bool:
        if "command -v" in command:
            process.stdout.write("/root/jarvis-agents/scout\nfound\n")
            process.exit(0)
            return True
        if "exec env" in command:
            prompt = await process.stdin.readline()
            process.stdout.write(json.dumps({"type": "echo", "got": prompt.strip()}) + "\n")
            process.stdout.write(json.dumps({"type": "result", "result": "done"}) + "\n")
            process.exit(0)
            return True
        return False

    ssh_server.state.handler = handler
    proc = await remote_cli.spawn(
        vps.id,
        agent_id="scout",
        runner="claude-cli",
        binary="claude",
        argv=[r"C:\bin\claude.exe", "--print", "--output-format", "stream-json"],
        local_cwd=r"C:\ws",
        env={"PATH": r"C:\secret-path"},
    )
    proc.stdin.write(b"hello there\n")
    await proc.stdin.drain()
    first = json.loads(await proc.stdout.readline())
    second = json.loads(await proc.stdout.readline())
    proc.stdin.close()
    code = await proc.wait()

    assert first == {"type": "echo", "got": "hello there"}
    assert second["type"] == "result"
    assert code == 0
    launched = ssh_server.state.commands[-1]
    assert "cd /root/jarvis-agents/scout && exec env CI=1 NO_COLOR=1 claude --print" in launched
    assert "secret-path" not in launched


async def test_missing_cli_on_the_remote_is_a_clear_sentence(
    vps, ssh_server: FakeSshServer
) -> None:  # noqa: ANN001
    async def handler(command: str, process: Any) -> bool:
        if "command -v" in command:
            process.stdout.write("/root/jarvis-agents/scout\nmissing\n")
            process.exit(0)
            return True
        return False

    ssh_server.state.handler = handler
    with pytest.raises(remote_cli.RemoteCliUnavailable) as caught:
        await remote_cli.spawn(
            vps.id,
            agent_id="scout",
            runner="codex-cli",
            binary="codex",
            argv=["codex", "exec"],
            local_cwd="/work",
            env={},
        )
    assert "Install codex on VPS" in str(caught.value)


async def test_placement_follows_the_society_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    agents = {
        "scout": SimpleNamespace(computer_id="c_1"),
        "here": SimpleNamespace(computer_id=None),
    }

    async def get(agent_id: str) -> Any:
        return agents.get(agent_id)

    fake_runtime = SimpleNamespace(roster=SimpleNamespace(get=get))
    monkeypatch.setattr("jarvis.society.runtime.current_runtime", lambda: fake_runtime)

    def chat(surface: str, sid: str) -> SimpleNamespace:
        return SimpleNamespace(surface=surface, session_id=sid)

    assert await remote.placement_for_session(chat("society", "society:scout")) == ("c_1", "scout")
    assert await remote.placement_for_session(chat("society", "society:here")) is None
    assert await remote.placement_for_session(chat("jarvis", "society:scout")) is None
