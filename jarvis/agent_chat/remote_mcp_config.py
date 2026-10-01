"""Temporary MCP configuration for coding CLIs on a connected computer.

Only the turn's scoped token enters these files. Existing project settings
are preserved, and a file changed by someone else is never overwritten during
cleanup. Unsupported transports fail before a model turn starts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from .remote_mcp import RemoteToolsUnavailable

log = logging.getLogger(__name__)

SUPPORTED_RUNNERS = frozenset(
    {
        "claude-cli",
        "codex-cli",
        "agy-cli",
        "grok-cli",
        "opencode-cli",
        "kimi-cli",
        "cursor-cli",
    }
)
TOKEN_ENV = "JARVIS_REMOTE_MCP_TOKEN"  # noqa: S105 — environment name, not a credential
_project_configs: set[tuple[str, str]] = set()


@dataclass
class RemoteToolConfig:
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def apply(self, argv: list[str]) -> list[str]:
        return [argv[0], *self.args, *argv[1:]]


class ConfigFiles:
    def __init__(self, sftp: Any, host: Any) -> None:
        self.sftp = sftp
        self.host = host
        self.written: list[tuple[str, bytes | None, bytes]] = []

    async def read(self, path: str) -> bytes | None:
        import asyncssh

        try:
            async with self.sftp.open(self.host.sftp_path(path), "rb") as handle:
                return await handle.read()
        except asyncssh.SFTPNoSuchFile:
            log.debug("remote tools: no existing project MCP config")
            return None

    async def write(self, path: str, text: str) -> None:
        import asyncssh

        remote = self.host.sftp_path(path)
        old = await self.read(path)
        data = text.encode("utf-8")
        await self.sftp.makedirs(remote.rsplit("/", 1)[0], exist_ok=True)
        # Record before the write so a partially failed transfer is cleaned up.
        self.written.append((path, old, data))
        async with self.sftp.open(
            remote, "wb", attrs=asyncssh.SFTPAttrs(permissions=0o600)
        ) as handle:
            await handle.write(data)

    async def aclose(self) -> None:
        for path, old, expected in reversed(self.written):
            try:
                async with asyncio.timeout(5):
                    current = await self.read(path)
                    if current != expected:
                        # A new owner edited this file. The expired token no longer
                        # grants anything; leave their edits intact.
                        continue
                    remote = self.host.sftp_path(path)
                    if old is None:
                        await self.sftp.remove(remote)
                    else:
                        async with self.sftp.open(remote, "wb") as handle:
                            await handle.write(old)
            except Exception:  # noqa: BLE001 — revocation does not depend on SFTP cleanup
                log.warning("remote tools: temporary config cleanup failed", exc_info=True)
        self.written.clear()


async def configure(
    opened: Any,
    host: Any,
    stack: Any,
    *,
    runner: str,
    cwd: str,
    url: str,
    token: str,
) -> RemoteToolConfig:
    if runner not in SUPPORTED_RUNNERS:
        raise RemoteToolsUnavailable(
            "This coding CLI has no supported Jarvis tool transport on another computer. "
            "Choose a CLI with MCP support in the agent's model settings."
        )
    config = RemoteToolConfig()
    if runner in {"grok-cli", "cursor-cli", "agy-cli"}:
        # These CLIs discover project files. Two simultaneous turns in the
        # same workspace must never pick each other's session capability.
        lease = (str(opened.conn.get_extra_info("peername")), cwd)
        if lease in _project_configs:
            raise RemoteToolsUnavailable(
                "Another turn is using this agent's project MCP settings. "
                "Wait for it to finish before retrying."
            )
        _project_configs.add(lease)
        stack.callback(_project_configs.discard, lease)
    auth = {"Authorization": "Bearer " + token}
    server = {"url": url, "headers": auth}
    if runner == "claude-cli":
        server["type"] = "http"
    config.env[TOKEN_ENV] = token
    if runner == "codex-cli":
        values = {
            "url": json.dumps(url),
            "bearer_token_env_var": json.dumps(TOKEN_ENV),
            "required": "true",
            "tool_timeout_sec": "600",
            "default_tools_approval_mode": '"approve"',
        }
        config.args = [
            part for k, v in values.items() for part in ("-c", f"mcp_servers.jarvis.{k}={v}")
        ]
        return config
    if runner == "opencode-cli":
        config.env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            {
                "mcp": {
                    "jarvis": {
                        "type": "remote",
                        "url": url,
                        "headers": auth,
                        "enabled": True,
                        "oauth": False,
                        "timeout": 600000,
                    }
                },
                # Jarvis' executor owns approval for this one server.
                "permission": {"jarvis_*": "allow"},
            }
        )
        return config
    sftp = await stack.enter_async_context(opened.conn.start_sftp_client())
    files = ConfigFiles(sftp, host)
    stack.push_async_callback(files.aclose)
    suffix = secrets.token_hex(8)
    if runner in {"claude-cli", "kimi-cli"}:
        path = f"{cwd}/.jarvis-mcp-{suffix}.json"
        await files.write(path, json.dumps({"mcpServers": {"jarvis": server}}))
        flag = "--mcp-config" if runner == "claude-cli" else "--mcp-config-file"
        config.args = [flag, path]
    elif runner == "agy-cli":
        folder = f"{cwd}/.agents/plugins/jarvis-remote-{suffix}"
        await files.write(f"{folder}/plugin.json", json.dumps({"name": "jarvis-hands"}))
        await files.write(
            f"{folder}/mcp_config.json",
            json.dumps(
                {
                    "mcpServers": {"jarvis": {"serverUrl": url, "headers": auth}},
                }
            ),
        )
    elif runner == "grok-cli":
        from .jarvis_harness import _grok_jarvis_block, _merge_grok_jarvis_block

        path = f"{cwd}/.grok/config.toml"
        old = await files.read(path)
        previous = old.decode("utf-8") if old is not None else ""
        block = _grok_jarvis_block({"url": url, "bearer_token_env_var": TOKEN_ENV})
        await files.write(path, _merge_grok_jarvis_block(previous, block))
    elif runner == "cursor-cli":
        path = f"{cwd}/.cursor/mcp.json"
        old = await files.read(path)
        try:
            previous = json.loads(old) if old is not None else {}
            previous.setdefault("mcpServers", {})["jarvis"] = server
        except (ValueError, TypeError, AttributeError) as exc:
            raise RemoteToolsUnavailable(
                "The computer's project MCP settings need repair in Cursor."
            ) from exc
        await files.write(path, json.dumps(previous))
        config.args = ["--approve-mcps"]
    return config
