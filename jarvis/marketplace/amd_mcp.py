"""Read-only AMD SMI bridge with an explicit optional-runtime capability probe."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS


def amd_unavailable_reason() -> str | None:
    """Cheap preflight for UI availability; connect still checks actual device data."""
    if sys.platform != "linux":
        return (
            "AMD GPU telemetry is unavailable on this operating system. "
            "This connector requires a compatible Linux AMD host with AMD SMI."
        )
    if not shutil.which("amd-smi"):
        return (
            "AMD SMI is unavailable. Install AMD's supported SMI/ROCm tooling "
            "on a compatible AMD host. Missing metrics are never reported as zero."
        )
    return None


def read_amd_status() -> dict[str, Any]:
    unavailable = amd_unavailable_reason()
    if unavailable:
        raise RuntimeError(unavailable)
    executable = shutil.which("amd-smi")
    if not executable:
        raise RuntimeError("AMD SMI is unavailable. Check the supported CLI installation.")
    result = {}
    for command in ("static", "metric"):
        try:
            completed = subprocess.run(
                [executable, command, "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "AMD SMI did not respond. Check the GPU driver and try again."
            ) from None
        except OSError:
            raise RuntimeError(
                "AMD SMI could not start. Check the supported CLI installation."
            ) from None
        if completed.returncode:
            raise RuntimeError(f"AMD SMI {command} failed; check supported hardware and drivers")
        try:
            data = json.loads(completed.stdout)
        except ValueError:
            raise RuntimeError("AMD SMI returned invalid JSON; update the supported CLI") from None
        devices = data.get("gpu_data", data) if isinstance(data, dict) else data
        if (
            not isinstance(devices, list)
            or not devices
            or not all(
                isinstance(device, dict) and device and not device.get("error")
                for device in devices
            )
        ):
            raise RuntimeError("AMD SMI returned no GPU data")
        result[command] = data
    return result


async def serve() -> None:
    import mcp.server.stdio
    import mcp.types as types
    from mcp.server import Server

    server = Server("amd_gpu")

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name="read_status",
                description=(
                    "Read AMD GPU utilization, temperature and driver information from AMD "
                    "SMI. Unsupported metrics remain unavailable."
                ),
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
                annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False),
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]):
        if name != "read_status" or arguments:
            raise ValueError("Only read_status without arguments is supported")
        data = await asyncio.to_thread(read_amd_status)
        return [types.TextContent(type="text", text=json.dumps(data))]

    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(serve())
