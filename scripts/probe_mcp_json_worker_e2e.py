"""E2E: a self-added mcp.json MCP server reaches + is called by the worker.

Drives the FULL self-MCP path: an mcp.json-style entry ->
`_assemble_worker_mcp_servers(mcp_json_servers=...)` (the real bootstrap glue,
which runs the mcp.json->claude converter) -> production `ClaudeDirectWorker.spawn()`
-> real `claude` CLI -> the test FastMCP `echo` tool. Token store is empty, so
ONLY the mcp.json server can satisfy the request — proving the "MCPs" section
path, not the Marketplace path.

Run:  python scripts/probe_mcp_json_worker_e2e.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from jarvis.marketplace.token_store import InMemoryBackend, TokenStore  # noqa: E402
from jarvis.missions.init import _assemble_worker_mcp_servers  # noqa: E402
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker  # noqa: E402
from jarvis.missions.workers.stream_consumer import (  # noqa: E402
    ClaudeAssistantMessage,
    ClaudeResult,
)


class _NoJob:
    def assign(self, pid: int) -> None:  # noqa: ARG002
        return None


async def _run() -> int:
    fake = _REPO / "tests" / "integration" / "mcp" / "fake_mcp_server.py"
    if not fake.exists():
        print("FAIL: fake MCP server missing:", fake)
        return 1

    # A self-added MCP server exactly as it would live in mcp.json:
    mcp_json = {
        "fakeprobe": {
            "command": sys.executable,
            "args": [str(fake)],
            "env": {"FAKE_MCP_MODE": "ok"},
            "enabled": True,
            "description": "test server added via the MCPs section",
            "transport": "stdio",
        }
    }
    # Empty token store -> NO marketplace plugins; only the mcp.json path can win.
    servers = _assemble_worker_mcp_servers(
        token_store=TokenStore(InMemoryBackend()), mcp_json_servers=mcp_json
    )
    print("[probe] assembled worker servers:", list(servers.keys()))
    if "fakeprobe" not in servers:
        print("FAIL: mcp.json server did not reach the worker config")
        return 1

    work = Path(tempfile.mkdtemp(prefix="mcpjson_work_"))
    logs = Path(tempfile.mkdtemp(prefix="mcpjson_logs_"))
    worker = ClaudeDirectWorker(mcp_servers=servers)

    env = dict(os.environ)
    for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(_k, None)

    prompt = (
        "Call the tool mcp__fakeprobe__echo with msg='mcpjson-ok'. "
        "After it returns, reply with ONLY the tool's exact return value."
    )
    tool_names: list[str] = []
    result_text = ""
    async for ev in worker.spawn(
        prompt, worktree=work, env=env, job=_NoJob(),
        worker_id="mcpjson-probe", log_dir=logs, timeout_s=180.0,
    ):
        if isinstance(ev, ClaudeAssistantMessage):
            for blk in (ev.message.get("content") or []):
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    tool_names.append(str(blk.get("name", "")))
        elif isinstance(ev, ClaudeResult):
            result_text = ev.result or ""

    print(f"[probe] tools invoked: {tool_names}")
    print(f"[probe] worker reply: {result_text[:160]!r}")
    ok = "echoed:mcpjson-ok" in result_text and any(
        "fakeprobe" in t for t in tool_names
    )
    print("\nPROBE RESULT:",
          "PASS - a self-added mcp.json MCP server was called end-to-end "
          "through the production worker"
          if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
