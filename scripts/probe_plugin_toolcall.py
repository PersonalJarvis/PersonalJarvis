"""E2E probe: prove the delegated claude-cli worker really CALLS a plugin MCP tool.

This exercises the exact wiring that ships in ClaudeDirectWorker:

  marketplace connect (token)            <- jarvis.marketplace.mcp_bridge
    -> claude-cli mcpServers map
    -> _build_mcp_config_args writes --mcp-config   <- the real worker helper
    -> `claude` CLI connects to the MCP server and invokes the tool

We point it at the protocol-identical test FastMCP `echo` server (a stand-in
for GitHub/Notion/etc.) and assert the echoed result comes back -> the MCP
plugin tool was actually invoked, end to end.

Run:  python scripts/probe_plugin_toolcall.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from jarvis.missions.workers.claude_direct_worker import (  # noqa: E402
    _build_mcp_config_args,
    _resolve_claude_argv_prefix,
)


def main() -> int:
    fake = _REPO / "tests" / "integration" / "mcp" / "fake_mcp_server.py"
    if not fake.exists():
        print("FAIL: fake MCP server not found:", fake)
        return 1

    work = Path(tempfile.mkdtemp(prefix="plugin_probe_"))
    servers = {
        "fakeprobe": {
            "command": sys.executable,
            "args": [str(fake)],
            "env": {"FAKE_MCP_MODE": "ok"},
        }
    }
    mcp_args = _build_mcp_config_args(work, servers)
    print("[probe] --mcp-config flags:", mcp_args)
    print("[probe] written config:", (work / ".jarvis-mcp.json").read_text(encoding="utf-8"))

    prompt = (
        "Call the tool named mcp__fakeprobe__echo with argument msg='harness-ok'. "
        "After it returns, reply with ONLY the tool's exact return value."
    )
    cmd = [
        *_resolve_claude_argv_prefix(),
        "--print",
        "--permission-mode", "bypassPermissions",
        "--add-dir", str(work),
        *mcp_args,
    ]
    print("[probe] cmd:", cmd)
    # Let claude use the Max OAuth login (~/.claude/.credentials.json) instead of
    # a stale/invalid ANTHROPIC_API_KEY in the ambient shell env. The production
    # worker handles this via build_worker_env; for the probe we just clear the
    # conflicting vars so the logged-in session is used.
    env = dict(os.environ)
    for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(_k, None)
    try:
        r = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            cwd=str(work), timeout=240, env=env,
        )
    except subprocess.TimeoutExpired:
        print("FAIL: claude CLI timed out")
        return 1

    out = (r.stdout or "") + "\n--- stderr ---\n" + (r.stderr or "")
    print(f"[probe] claude rc={r.returncode}")
    print(out[-2500:])
    ok = "echoed:harness-ok" in out
    print("\nPROBE RESULT:",
          "PASS - the MCP plugin tool was actually called end-to-end"
          if ok else "FAIL - echoed result not found in output")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
