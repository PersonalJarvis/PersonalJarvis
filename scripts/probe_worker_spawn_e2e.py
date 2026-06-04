"""E2E: drive the REAL production ClaudeDirectWorker.spawn() and prove it calls
a plugin MCP tool.

Unlike probe_plugin_toolcall.py (which reuses only the helper), this exercises
the actual worker object: ``ClaudeDirectWorker(mcp_servers=...).spawn(...)`` ->
argv assembly -> claude subprocess -> stream parsing. We point it at the test
FastMCP ``echo`` server (a protocol-identical stand-in for a stdio marketplace
plugin like Supabase). ``echo`` is deliberately chosen because claude has NO
built-in echo tool, so a returned ``echoed:...`` proves the MCP path was used —
nothing else could produce it.

Also asserts the token-bearing config does NOT leak into the git worktree.

Run:  python scripts/probe_worker_spawn_e2e.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker  # noqa: E402
from jarvis.missions.workers.stream_consumer import (  # noqa: E402
    ClaudeAssistantMessage,
    ClaudeResult,
)


class _NoJob:
    """Minimal stand-in for the per-mission Job Object (spawn calls .assign)."""

    def assign(self, pid: int) -> None:  # noqa: D401, ARG002
        return None


async def _run() -> int:
    fake = _REPO / "tests" / "integration" / "mcp" / "fake_mcp_server.py"
    if not fake.exists():
        print("FAIL: fake MCP server missing:", fake)
        return 1

    work = Path(tempfile.mkdtemp(prefix="wspawn_work_"))
    logs = Path(tempfile.mkdtemp(prefix="wspawn_logs_"))
    servers = {
        "fakeprobe": {
            "command": sys.executable,
            "args": [str(fake)],
            "env": {"FAKE_MCP_MODE": "ok"},
        }
    }
    worker = ClaudeDirectWorker(mcp_servers=servers)

    env = dict(os.environ)  # let claude use the Max OAuth login
    for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(_k, None)

    prompt = (
        "Call the tool mcp__fakeprobe__echo with msg='worker-spawn-ok'. "
        "After it returns, reply with ONLY the tool's exact return value."
    )

    result_text = ""
    tool_used = False
    events = 0
    async for ev in worker.spawn(
        prompt,
        worktree=work,
        env=env,
        job=_NoJob(),
        worker_id="probe",
        log_dir=logs,
        timeout_s=180.0,
    ):
        events += 1
        if isinstance(ev, ClaudeAssistantMessage):
            for blk in (ev.message.get("content") or []):
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    tool_used = True
        elif isinstance(ev, ClaudeResult):
            result_text = ev.result or ""

    leaked = (work / ".jarvis-mcp.json").exists()
    cfg_in_logs = (logs / ".jarvis-mcp.json").exists()

    print(f"[probe] events={events} tool_used={tool_used}")
    print(f"[probe] worker result: {result_text[:300]!r}")
    print(f"[probe] config in worktree (MUST be False): {leaked}")
    print(f"[probe] config in log_dir (MUST be True):  {cfg_in_logs}")

    ok = (
        "echoed:worker-spawn-ok" in result_text
        and tool_used
        and not leaked
        and cfg_in_logs
    )
    print("\nPROBE RESULT:",
          "PASS - production worker.spawn() called the MCP plugin tool, "
          "no token leaked into the worktree"
          if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
