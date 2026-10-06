"""Smoke: the real runtime drivers launch real Hermes / OpenClaw turns.

Unlike ``agent_runtimes_probe.py`` this goes through Jarvis' own drivers
(``jarvis.agent_runtimes.hermes`` / ``openclaw``): profile and config
writing, process environment, the OpenClaw Gateway supervisor, and the ACP
turn. The model is ``tests/fakes/fake_openai_server.py``; no paid key.
All runtime state lands in a temporary folder.

    python scripts/spikes/agent_runtimes_e2e.py hermes
    python scripts/spikes/agent_runtimes_e2e.py openclaw
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.agent_runtimes import base, driver  # noqa: E402
from jarvis.agent_runtimes.acp import AcpTurn, frame_line  # noqa: E402
from jarvis.agent_runtimes.base import RuntimeTurn  # noqa: E402
from jarvis.agent_runtimes.model_map import ModelRoute  # noqa: E402
from jarvis.core.control_key import get_control_key  # noqa: E402
from tests.fakes.fake_openai_server import MODEL_ID, FakeOpenAIServer  # noqa: E402

_STDERR = Path(tempfile.gettempdir()) / "jarvis-runtime-e2e-stderr.log"


class _IO:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.events: list[dict[str, Any]] = []

    async def write(self, frame: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(frame_line(frame).encode("utf-8"))
        await self.proc.stdin.drain()

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def ask(self, call_id: str, name: str, args: dict[str, Any], summary: str) -> str:
        return "allow"


async def _run(name: str, turn: RuntimeTurn, text: str) -> tuple[AcpTurn, _IO]:
    runtime = driver(name)
    launch = await runtime.launch(turn)
    acp = AcpTurn(
        turn_id="e2e",
        cwd=str(launch.cwd),
        prompt_text=text,
        resume=launch.acp_resume,
        mcp_servers=launch.mcp_servers,
        auto_allow=True,
        report_session=launch.vendor_session,
    )
    proc = await asyncio.create_subprocess_exec(
        *launch.argv,
        cwd=str(launch.cwd),
        env=launch.env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=_STDERR.open("ab"),
        limit=16 * 1024 * 1024,
    )
    io = _IO(proc)
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(acp.opening_frame().encode("utf-8"))
    await proc.stdin.drain()

    async def pump() -> None:
        assert proc.stdout is not None and proc.stdin is not None
        while raw := await proc.stdout.readline():
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict):
                await acp.on_message(obj, io)
            if acp.saw_result and not proc.stdin.is_closing():
                proc.stdin.close()

    await asyncio.wait_for(pump(), timeout=180)
    await asyncio.wait_for(proc.wait(), timeout=30)
    if launch.release is not None:
        launch.release()
    return acp, io


async def main(name: str) -> int:
    tmp = Path(tempfile.mkdtemp(prefix=f"jarvis-{name}-e2e-"))
    base.runtimes_root = lambda: tmp / "agent_runtimes"  # keep the real data dir clean
    workspace = tmp / "workspace"
    workspace.mkdir()
    status = driver(name).detect(refresh=True)
    print("detect:", status.to_dict())
    if not status.ready:
        return 2
    with FakeOpenAIServer() as server:
        key = "sk-e2e-" + uuid.uuid4().hex
        route = ModelRoute("local-openai", MODEL_ID, server.base_url, "chat_completions", None)
        # JARVIS_E2E_MCP=<url>|<agent id>: hand the runtime a running Jarvis'
        # MCP server (a dev instance) and make the fake model call a real tool.
        mcp = os.environ.get("JARVIS_E2E_MCP", "")
        mcp_url, _, mcp_agent = mcp.partition("|")
        agent_id = mcp_agent or "e2e-agent"
        turn = RuntimeTurn(
            agent_id=agent_id,
            agent_name="Probe",
            session_id=f"society:{agent_id}",
            workspace=workspace,
            route=route,
            resume=None,
            auto_approve=True,
            mcp_url=mcp_url or None,
            control_key=get_control_key() if mcp_url else None,
        )
        opening = (
            'CALL_TOOL society_wiki_note {"kind": "memory", "target": "user", '
            '"text": "Favourite food: lasagne (runtime e2e)"}'
            if mcp_url
            else "first message"
        )
        first, io1 = await _run(name, turn, opening)
        if server.requests:
            offered = [
                (t.get("function") or {}).get("name", "")
                for t in server.requests[0].get("tools") or []
            ]
            print("  offered jarvis tools:", [n for n in offered if "jarvis" in n][:8])
            print("  offered total:", len(offered), "tool_search" in offered)
        calls = [e["payload"] for e in io1.events if e["kind"] in ("tool_call", "tool_result")]
        for call in calls:
            print("  ", {k: str(v)[-700:] for k, v in call.items() if k in ("name", "output")})
        print("turn 1:", first.status, first.error, repr(first.result_text[:80]))
        print("  vendor session:", first.vendor_session)
        turn.resume = first.vendor_session
        second, io2 = await _run(name, turn, "second message")
        print("turn 2:", second.status, second.error, repr(second.result_text[:80]))
        history = len(server.requests[-1].get("messages", [])) if server.requests else 0
        print("  model saw", history, "messages on turn 2")
        home = tmp / "agent_runtimes" / name / agent_id
        written = sorted(p.name for p in home.iterdir())
        print("  runtime home:", written)
        secret_leak = any(
            p.is_file()
            and p.stat().st_size < 5_000_000
            and key in p.read_text(encoding="utf-8", errors="replace")
            for p in home.rglob("*")
        )
        # Only a fixed word is printed: never anything derived from the key.
        print("  key written to disk:", "YES" if secret_leak else "no")
    await driver(name).stop()
    ok = first.status == "done" and second.status == "done" and history > 2 and not secret_leak
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "hermes")))
