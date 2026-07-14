"""ClaudeDirectWorker MCP-config wiring.

The worker must turn an assembled ``mcpServers`` dict into a claude-cli
``--mcp-config <file>`` flag so the delegated worker can call the connected
plugins. An empty explicit config still activates strict mode so project/global
MCP settings cannot inject undeclared worker tools.
"""

from __future__ import annotations

import json
from pathlib import Path

from jarvis.missions.workers.claude_direct_worker import _build_mcp_config_args


def test_no_servers_still_produces_a_strict_empty_config(tmp_path: Path) -> None:
    for servers in ({}, None):
        args = _build_mcp_config_args(tmp_path, servers)
        assert "--strict-mcp-config" in args
        cfg_path = Path(args[args.index("--mcp-config") + 1])
        assert json.loads(cfg_path.read_text(encoding="utf-8")) == {"mcpServers": {}}


def test_servers_write_config_and_emit_flag(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    servers = {
        "github": {"command": "docker", "args": ["run"], "env": {"X": "y"}},
        "notion": {"type": "http", "url": "https://mcp.notion.com/mcp"},
    }
    args = _build_mcp_config_args(log_dir, servers)

    assert "--mcp-config" in args
    cfg_path = Path(args[args.index("--mcp-config") + 1])
    assert cfg_path.exists()
    assert json.loads(cfg_path.read_text(encoding="utf-8")) == {"mcpServers": servers}
    # restrict to our config so a stray project .mcp.json can't interfere
    assert "--strict-mcp-config" in args
    # SECURITY: the resolved token must NOT land in the git worktree (it would
    # leak into _capture_diff / safety scan / archived diff.patch).
    assert not (worktree / ".jarvis-mcp.json").exists()
    assert cfg_path.parent == log_dir
