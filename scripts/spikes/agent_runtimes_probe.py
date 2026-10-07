"""Spike: drive Hermes / OpenClaw over ACP against a fake model.

Answers the open questions behind ``docs/agent-runtimes.md`` without a paid
key: every model call goes to ``tests/fakes/fake_openai_server.py``.

    python scripts/spikes/agent_runtimes_probe.py hermes
    python scripts/spikes/agent_runtimes_probe.py openclaw

Prints every raw ACP line plus the translated agent-chat events, then a
summary: session id, whether ``session/load`` restored it in a NEW process,
how an MCP tool is named in ``tool_call``, and whether stdin EOF ends the
process.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.agent_runtimes.acp import AcpTurn, McpServer, frame_line  # noqa: E402
from tests.fakes.fake_openai_server import MODEL_ID, FakeOpenAIServer  # noqa: E402

MCP_SCRIPT = textwrap.dedent(
    """
    from mcp.server.fastmcp import FastMCP
    app = FastMCP("jarvis")

    @app.tool()
    def society_memory_recall(query: str = "") -> str:
        '''Recall the agent's own notes.'''
        import os
        return "recalled: " + query + " session=" + os.environ.get("PROBE_SESSION", "?")

    app.run()
    """
)


class _IO:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.events: list[dict[str, Any]] = []

    async def write(self, frame: dict[str, Any]) -> None:
        print(">>", json.dumps(frame)[:300])
        assert self.proc.stdin is not None
        self.proc.stdin.write(frame_line(frame).encode("utf-8"))
        await self.proc.stdin.drain()

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if event["kind"] not in {"text_delta", "reasoning_delta"}:
            print("EV", event["kind"], json.dumps(event["payload"])[:300])

    async def ask(self, call_id: str, name: str, args: dict[str, Any], summary: str) -> str:
        print("ASK", name, summary)
        return "allow"


async def run_turn(
    argv: list[str], env: dict[str, str], cwd: Path, turn: AcpTurn, limit_s: float = 120
) -> tuple[AcpTurn, _IO, int | None]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )
    io = _IO(proc)
    stderr_tail: list[str] = []

    async def drain() -> None:
        assert proc.stderr is not None
        while line := await proc.stderr.readline():
            stderr_tail.append(line.decode("utf-8", "replace").rstrip())
            del stderr_tail[:-30]

    async def pump() -> None:
        assert proc.stdout is not None
        while raw := await proc.stdout.readline():
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            print("<<", line[:400])
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                await turn.on_message(obj, io)
            if turn.saw_result and proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()

    drainer = asyncio.create_task(drain())
    assert proc.stdin is not None
    proc.stdin.write(turn.opening_frame().encode("utf-8"))
    await proc.stdin.drain()
    try:
        await asyncio.wait_for(pump(), timeout=limit_s)
        rc = await asyncio.wait_for(proc.wait(), timeout=20)
    except TimeoutError:
        print("!! timeout; process still running =", proc.returncode is None)
        proc.kill()
        rc = None
    drainer.cancel()
    print("-- stderr tail:\n  " + "\n  ".join(stderr_tail[-12:]))
    return turn, io, rc


def hermes_home(base: Path, server: FakeOpenAIServer, workspace: Path) -> Path:
    home = base / "hermes-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        textwrap.dedent(
            f"""
            model:
              default: {MODEL_ID}
              provider: custom
              base_url: {server.base_url}
              api_key: fake
            terminal:
              cwd: {workspace.as_posix()}
            memory:
              memory_enabled: false
              user_profile_enabled: false
            skills:
              creation_nudge_interval: 0
            tools:
              tool_search: false
            """
        ),
        encoding="utf-8",
    )
    (home / "SOUL.md").write_text("You are Probe, a Jarvis agent.\n", encoding="utf-8")
    return home


async def probe_hermes() -> None:
    binary = shutil.which("hermes") or str(
        Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "bin" / "hermes.exe"
    )
    base = Path(tempfile.mkdtemp(prefix="jarvis-acp-probe-"))
    workspace = base / "workspace"
    workspace.mkdir()
    mcp_script = base / "mcp_stub.py"
    mcp_script.write_text(MCP_SCRIPT, encoding="utf-8")
    with FakeOpenAIServer() as server:
        home = hermes_home(base, server, workspace)
        env = dict(os.environ)
        env.update(
            {
                "HERMES_HOME": str(home),
                "HERMES_ACP_SKIP_CONFIGURED_MCP": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        mcp = McpServer(name="jarvis", command=sys.executable, args=[str(mcp_script)])
        print("== turn 1: new session + MCP tool call")
        t1, io1, rc1 = await run_turn(
            [binary, "acp"],
            env,
            workspace,
            AcpTurn(
                turn_id="t1",
                cwd=str(workspace),
                prompt_text='CALL_TOOL society_memory_recall {"query": "hello"}',
                mcp_servers=[mcp],
                auto_allow=True,
            ),
        )
        print("== turn 2: session/load in a new process")
        t2, io2, rc2 = await run_turn(
            [binary, "acp"],
            env,
            workspace,
            AcpTurn(
                turn_id="t2",
                cwd=str(workspace),
                prompt_text="second message",
                resume=t1.vendor_session,
                mcp_servers=[mcp],
                auto_allow=True,
            ),
        )
        print("== turn 3: unknown session id")
        t3, _io3, _rc3 = await run_turn(
            [binary, "acp"],
            env,
            workspace,
            AcpTurn(turn_id="t3", cwd=str(workspace), prompt_text="x", resume="nope-123"),
        )
        print("\n== SUMMARY (hermes)")
        print("agent:", t1.agent_name, t1.agent_version, "load:", t1.can_load)
        print("t1 session:", t1.vendor_session, "status:", t1.status, t1.error, "rc:", rc1)
        print("t1 tools:", [e["payload"]["name"] for e in io1.events if e["kind"] == "tool_call"])
        print("t1 text:", t1.result_text[:120])
        print("t2 session:", t2.vendor_session, "status:", t2.status, t2.error, "rc:", rc2)
        print("t2 text:", t2.result_text[:120])
        print("t2 model saw history msgs:", len(server.requests[-1].get("messages", [])))
        print("t3 status:", t3.status, t3.error)
        print("fake model requests:", len(server.requests))


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def probe_openclaw() -> None:
    """Gateway-backed ``openclaw acp`` on an isolated state dir and port.

    ``OPENCLAW_ENTRY`` = path to ``openclaw.mjs`` (a fresh npm install);
    ``OPENCLAW_NODE`` = the node binary that satisfies its engine range.
    """
    entry = os.environ["OPENCLAW_ENTRY"]
    node = os.environ.get("OPENCLAW_NODE", "node")
    base = Path(tempfile.mkdtemp(prefix="jarvis-openclaw-probe-"))
    state = base / "state"
    workspace = base / "workspace"
    state.mkdir()
    workspace.mkdir()
    mcp_script = base / "mcp_stub.py"
    mcp_script.write_text(MCP_SCRIPT, encoding="utf-8")
    port = _free_port()
    token = "probe-" + os.urandom(6).hex()
    with FakeOpenAIServer() as server:
        config = {
            "gateway": {
                "mode": "local",
                "port": port,
                "bind": "loopback",
                "auth": {"mode": "token", "token": token},
            },
            "models": {
                "mode": "replace",
                "providers": {
                    "fake": {
                        "baseUrl": server.base_url,
                        "apiKey": "fake",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": MODEL_ID,
                                "name": "Fake",
                                "input": ["text"],
                                "contextWindow": 128000,
                                "maxTokens": 4096,
                            }
                        ],
                    }
                },
            },
            "agents": {
                "defaults": {
                    "model": {"primary": f"fake/{MODEL_ID}"},
                    "heartbeat": {"every": "0m"},
                    "workspace": str(workspace),
                },
            },
            "tools": {"toolSearch": False},
            "mcp": {
                "servers": {
                    "jarvis": {
                        "command": sys.executable,
                        "args": [str(mcp_script)],
                        "env": {"PROBE_SESSION": "${JARVIS_CHAT_SESSION}"},
                    }
                }
            },
            "session": {"reset": {"mode": "none"}},
        }
        config_path = state / "openclaw.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        env = dict(os.environ)
        env.update(
            {
                "OPENCLAW_STATE_DIR": str(state),
                "OPENCLAW_CONFIG_PATH": str(config_path),
                "JARVIS_CHAT_SESSION": "society:probe",
                "OPENCLAW_DISABLE_BONJOUR": "1",
                "OPENCLAW_NO_RESPAWN": "1",
                "OPENCLAW_SKIP_CHANNELS": "1",
                "OPENCLAW_EXEC_SHELL_SNAPSHOT": "0",
            }
        )
        gw_log = base / "gateway.log"
        with gw_log.open("wb") as log_file:
            gateway = await asyncio.create_subprocess_exec(
                node,
                entry,
                "gateway",
                "run",
                "--port",
                str(port),
                "--allow-unconfigured",
                cwd=str(workspace),
                env=env,
                stdout=log_file,
                stderr=log_file,
            )
            try:
                started = asyncio.get_running_loop().time()
                ready = False
                for _ in range(120):
                    await asyncio.sleep(1)
                    text = gw_log.read_text(encoding="utf-8", errors="replace")
                    if "listening" in text.lower():
                        ready = True
                        break
                    if gateway.returncode is not None:
                        break
                elapsed = asyncio.get_running_loop().time() - started
                print("gateway ready:", ready, "rc:", gateway.returncode, f"after {elapsed:.0f}s")
                print("gateway log tail:\n  " + "\n  ".join(text.splitlines()[-15:]))
                if not ready:
                    return
                argv = [
                    node,
                    entry,
                    "acp",
                    "--url",
                    f"ws://127.0.0.1:{port}",
                    "--token",
                    token,
                    "--session",
                    "agent:main:main",
                ]
                print("== turn 1")
                t1, io1, rc1 = await run_turn(
                    argv,
                    env,
                    workspace,
                    AcpTurn(
                        turn_id="t1",
                        cwd=str(workspace),
                        prompt_text='CALL_TOOL society_memory_recall {"query": "hi"}',
                        auto_allow=True,
                    ),
                )
                print("== turn 2 (same session key, new bridge process)")
                t2, io2, rc2 = await run_turn(
                    argv,
                    env,
                    workspace,
                    AcpTurn(
                        turn_id="t2", cwd=str(workspace), prompt_text="second", auto_allow=True
                    ),
                )
                print("\n== SUMMARY (openclaw)")
                print("agent:", t1.agent_name, t1.agent_version, "load:", t1.can_load)
                print("t1 session:", t1.vendor_session, t1.status, t1.error, "rc:", rc1)
                tools = [e["payload"]["name"] for e in io1.events if e["kind"] == "tool_call"]
                print("t1 tools:", tools)
                print("t1 text:", t1.result_text[:160])
                print("t2 session:", t2.vendor_session, t2.status, t2.error, "rc:", rc2)
                print("t2 text:", t2.result_text[:160])
                if server.requests:
                    print("t2 history msgs:", len(server.requests[-1].get("messages", [])))
                    offered = server.requests[0].get("tools") or []
                    names = [(t.get("function") or {}).get("name") for t in offered]
                    print("tools offered:", names)
                print("fake model requests:", len(server.requests))
            finally:
                gateway.terminate()
                try:
                    await asyncio.wait_for(gateway.wait(), timeout=15)
                except TimeoutError:
                    gateway.kill()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "hermes"
    asyncio.run(probe_hermes() if target == "hermes" else probe_openclaw())
