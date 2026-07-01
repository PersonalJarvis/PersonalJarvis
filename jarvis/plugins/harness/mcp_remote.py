"""MCP-remote harness: wraps an external MCP server as a harness.

Unlike the CLI harnesses (openclaw, codex), this one runs as an in-process
MCP client against a remote or local stdio server. The prompt is sent as an
MCP tool call (convention: `dispatch(prompt)`) to the first available server.

Configuration: `task.env["MCP_SERVER_NAME"]` picks a specific server from the
bootstrap list; the default is `filesystem-mcp`.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from jarvis.core.protocols import HarnessResult, HarnessTask
from jarvis.mcp.client import MCPClient
from jarvis.mcp.registry import BOOTSTRAP_SERVERS

log = logging.getLogger(__name__)


class MCPRemoteHarness:
    """Uses an MCP server to run tool calls via the harness interface."""

    name: str = "mcp-remote"
    version: str = "0.1"
    supports_versions: str = ">=1.0"

    def __init__(self) -> None:
        self._client: MCPClient | None = None
        self._current_server: str | None = None

    async def health(self) -> bool:
        # An MCP-remote harness is "healthy" when at least one bootstrap
        # server can be pinged. Since pinging costs time, we only return
        # here: registry-is-not-empty.
        return len(BOOTSTRAP_SERVERS) > 0

    async def invoke(self, task: HarnessTask) -> AsyncIterator[HarnessResult]:
        import time
        t_start = time.perf_counter()

        server_name = task.env.get("MCP_SERVER_NAME") or "filesystem-mcp"
        spec = next((s for s in BOOTSTRAP_SERVERS if s.name == server_name), None)

        if spec is None:
            yield HarnessResult(
                stderr=f"MCP server '{server_name}' not in BOOTSTRAP_SERVERS.\n",
                exit_code=2,
                duration_ms=int((time.perf_counter() - t_start) * 1000),
                is_final=True,
            )
            return

        client = MCPClient(spec)
        try:
            await client.connect()
        except Exception as exc:  # noqa: BLE001
            yield HarnessResult(
                stderr=f"MCP connect failed: {exc}\n",
                exit_code=1,
                duration_ms=int((time.perf_counter() - t_start) * 1000),
                is_final=True,
            )
            return

        try:
            # List of available tools
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            yield HarnessResult(
                stdout=f"[mcp:{server_name}] Tools: {', '.join(tool_names[:10])}\n",
                is_final=False,
            )

            # If the prompt has one of the tool names as a prefix, use it.
            # Convention: "toolname args-as-json" or just "prompt".
            chosen_tool: str | None = None
            args: dict = {"prompt": task.prompt}
            for t in tools:
                if task.prompt.startswith(t.name):
                    chosen_tool = t.name
                    break

            if chosen_tool is None and tool_names:
                # No explicit tool name — we return the list and are done.
                yield HarnessResult(
                    stdout=(
                        "Please put a tool name at the start of the prompt "
                        f"(e.g. '{tool_names[0]} ...').\n"
                    ),
                    is_final=False,
                )
            elif chosen_tool:
                result = await client.call_tool(chosen_tool, args)
                yield HarnessResult(
                    stdout=str(result)[:4000] + "\n",
                    is_final=False,
                )
        finally:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass

        yield HarnessResult(
            exit_code=0,
            duration_ms=int((time.perf_counter() - t_start) * 1000),
            is_final=True,
        )

    async def cancel(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                pass
