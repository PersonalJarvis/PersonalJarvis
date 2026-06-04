"""MCP-Remote-Harness: wrappt externe MCP-Server als Harness.

Im Gegensatz zu den CLI-Harnesses (openclaw, codex) läuft das hier
als In-Process MCP-Client gegen einen Remote- oder lokalen Stdio-Server.
Der Prompt wird als MCP-Tool-Call (Convention: `dispatch(prompt)`) an den
ersten zur-Verfügung-stehenden Server gesendet.

Konfiguration: `task.env["MCP_SERVER_NAME"]` wählt einen konkreten Server
aus der Bootstrap-Liste; default ist `filesystem-mcp`.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from jarvis.core.protocols import HarnessResult, HarnessTask
from jarvis.mcp.client import MCPClient
from jarvis.mcp.registry import BOOTSTRAP_SERVERS

log = logging.getLogger(__name__)


class MCPRemoteHarness:
    """Nutzt einen MCP-Server zum Ausführen von Tool-Calls via Harness-Interface."""

    name: str = "mcp-remote"
    version: str = "0.1"
    supports_versions: str = ">=1.0"

    def __init__(self) -> None:
        self._client: MCPClient | None = None
        self._current_server: str | None = None

    async def health(self) -> bool:
        # Ein MCP-Remote-Harness ist "healthy" wenn mindestens ein
        # Bootstrap-Server gepingt werden kann. Da das Pinging Zeit
        # kostet, returnen wir hier nur: registry-is-not-empty.
        return len(BOOTSTRAP_SERVERS) > 0

    async def invoke(self, task: HarnessTask) -> AsyncIterator[HarnessResult]:
        import time
        t_start = time.perf_counter()

        server_name = task.env.get("MCP_SERVER_NAME") or "filesystem-mcp"
        spec = next((s for s in BOOTSTRAP_SERVERS if s.name == server_name), None)

        if spec is None:
            yield HarnessResult(
                stderr=f"MCP-Server '{server_name}' nicht in BOOTSTRAP_SERVERS.\n",
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
                stderr=f"MCP-Connect fehlgeschlagen: {exc}\n",
                exit_code=1,
                duration_ms=int((time.perf_counter() - t_start) * 1000),
                is_final=True,
            )
            return

        try:
            # Liste verfügbarer Tools
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            yield HarnessResult(
                stdout=f"[mcp:{server_name}] Tools: {', '.join(tool_names[:10])}\n",
                is_final=False,
            )

            # Wenn der Prompt einen der Tool-Namen als Prefix hat, nutze den.
            # Convention: "toolname args-as-json" oder bloß "prompt".
            chosen_tool: str | None = None
            args: dict = {"prompt": task.prompt}
            for t in tools:
                if task.prompt.startswith(t.name):
                    chosen_tool = t.name
                    break

            if chosen_tool is None and tool_names:
                # Kein expliziter Tool-Name — wir geben die Liste zurück und fertig.
                yield HarnessResult(
                    stdout=(
                        "Bitte Tool-Name an den Anfang des Prompts setzen "
                        f"(z.B. '{tool_names[0]} ...').\n"
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
