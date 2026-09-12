"""Read-only AMD SMI bridge with an explicit optional-runtime capability probe."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

_PCI_DEVICES = Path("/sys/bus/pci/devices")
_DRM_DEVICES = Path("/sys/class/drm")


def _amd_hardware_unavailable_reason() -> str | None:
    """Read Linux device metadata only; an installed CLI is not hardware evidence.

    PCI display/accelerator class filtering avoids treating AMD CPU chipsets as
    GPUs. DRM is a fallback for hosts exposing graphics devices but not the
    complete PCI tree. Restricted/container sysfs is explicitly unverifiable,
    not evidence that the physical host has no AMD GPU.
    """
    readable_root = False
    incomplete = False
    for root, drm in ((_PCI_DEVICES, False), (_DRM_DEVICES, True)):
        try:
            devices = list(root.iterdir())
            readable_root = True
        except OSError:
            # An alternative sysfs view can still prove GPU presence.
            continue
        for entry in devices:
            if drm and not (entry.name.startswith("card") and entry.name[4:].isdigit()):
                continue
            device = entry / "device" if drm else entry
            try:
                vendor = (device / "vendor").read_text(encoding="ascii").strip().lower()
                if vendor != "0x1002":
                    continue
                if drm:
                    return None
                device_class = int((device / "class").read_text(encoding="ascii").strip(), 16)
                if device_class >> 16 in (0x03, 0x12):
                    return None
            except (OSError, UnicodeError, ValueError):
                # Hot unplug and unreadable sysfs must not escape into the UI.
                incomplete = True
    if not readable_root or incomplete:
        return (
            "AMD GPU availability could not be verified from Linux device information. "
            "Check hardware access and permissions, including container device access."
        )
    return (
        "No AMD GPU is visible to this connector. A compatible AMD GPU must be exposed "
        "to this Linux environment before connecting."
    )


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
    return _amd_hardware_unavailable_reason()


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
