"""REAL end-to-end test: production worker -> claude -> connected GitHub plugin.

Uses the ACTUAL connected plugin config (`_assemble_worker_mcp_servers`, which
reads the real saved token), drives the production `ClaudeDirectWorker.spawn()`,
and asks it to call a READ-ONLY GitHub MCP tool (the authenticated-user / "get
me" tool). Records which tool names were actually invoked so we can prove a
``github`` MCP tool ran (not a built-in). The returned GitHub login is public
profile info, not a secret.

Run:  python scripts/probe_github_plugin_e2e.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

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
    servers = _assemble_worker_mcp_servers()
    if "github" not in servers:
        print("FAIL: github not connected (no token). Connect it in the Plugins UI.")
        return 1
    print("[probe] using REAL connected plugin: github (docker MCP)")

    work = Path(tempfile.mkdtemp(prefix="gh_work_"))
    logs = Path(tempfile.mkdtemp(prefix="gh_logs_"))
    worker = ClaudeDirectWorker(mcp_servers=servers)

    env = dict(os.environ)
    for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(_k, None)

    prompt = (
        "Use the GitHub MCP server's authenticated-user tool (the 'get me' / "
        "current user endpoint) to look up my own GitHub account. "
        "Then reply with ONLY my GitHub login (username), nothing else."
    )

    tool_names: list[str] = []
    result_text = ""
    async for ev in worker.spawn(
        prompt,
        worktree=work,
        env=env,
        job=_NoJob(),
        worker_id="ghprobe",
        log_dir=logs,
        timeout_s=180.0,
    ):
        if isinstance(ev, ClaudeAssistantMessage):
            for blk in (ev.message.get("content") or []):
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    tool_names.append(str(blk.get("name", "")))
        elif isinstance(ev, ClaudeResult):
            result_text = ev.result or ""

    github_tool_used = any("github" in t.lower() for t in tool_names)
    print(f"[probe] tools invoked: {tool_names}")
    print(f"[probe] worker reply (your GitHub login): {result_text.strip()[:200]!r}")

    # tail of stderr for diagnostics if it failed
    stderr_log = logs / "stderr.log"
    if not github_tool_used and stderr_log.exists():
        print("[probe] stderr tail:\n" + stderr_log.read_text(encoding="utf-8", errors="replace")[-800:])

    ok = github_tool_used and bool(result_text.strip())
    print("\nPROBE RESULT:",
          "PASS - the real connected GitHub plugin tool was actually called "
          "end-to-end through the production worker"
          if ok else "FAIL - no github MCP tool call detected")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
