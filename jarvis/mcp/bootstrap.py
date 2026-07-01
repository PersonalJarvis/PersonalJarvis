"""Bootstrap helper for the setup wizard (Phase 1c Builder B3 calls this).

Provides functions for installing, verifying, and persisting user
selections. No UI — pure data flow.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

from .client import MCPClient
from .registry import MCPServerSpec

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Result types
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class InstallResult:
    """Result of an installation attempt."""
    spec_name: str
    success: bool
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


# ----------------------------------------------------------------------
# Prerequisite check
# ----------------------------------------------------------------------

def check_prerequisites() -> dict[str, bool]:
    """Check which launchers are available on PATH (uv/uvx/npx/python)."""
    return {
        "uv": shutil.which("uv") is not None,
        "uvx": shutil.which("uvx") is not None,
        "npx": shutil.which("npx") is not None,
        "python": shutil.which("python") is not None or shutil.which("python3") is not None,
        "node": shutil.which("node") is not None,
    }


# ----------------------------------------------------------------------
# Install
# ----------------------------------------------------------------------

async def install_server(spec: MCPServerSpec) -> InstallResult:
    """Install an MCP server by probe-running its install_command with ``--help``.

    Background: ``uvx <pkg>`` and ``npx -y <pkg>`` download and cache the
    package on first run. A help invocation is enough to trigger the
    download and cache while simultaneously testing availability.
    """
    if not spec.install_command:
        return InstallResult(spec.name, False, error="install_command leer")

    argv = list(spec.install_command)
    # Expand placeholders (the probe run does not need real values, but
    # npx/uvx should not see path patterns as package names).
    from jarvis.core.config import PROJECT_ROOT
    argv = [a.replace("{PROJECT_ROOT}", str(PROJECT_ROOT)) for a in argv]

    # If the launcher (uvx/npx) is missing: fail cleanly.
    if shutil.which(argv[0]) is None:
        return InstallResult(
            spec.name, False,
            error=f"Launcher {argv[0]!r} not on PATH",
        )

    # Probe: get the package argument, then append --help.
    probe_argv = _build_probe_argv(argv)

    try:
        proc = await asyncio.create_subprocess_exec(
            *probe_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return InstallResult(
                spec.name, False, error="install timeout (180s)"
            )
        ok = proc.returncode == 0
        return InstallResult(
            spec_name=spec.name,
            success=ok,
            stdout=stdout.decode("utf-8", errors="replace")[:4000],
            stderr=stderr.decode("utf-8", errors="replace")[:4000],
            error=None if ok else f"exit code {proc.returncode}",
        )
    except FileNotFoundError as e:
        return InstallResult(spec.name, False, error=str(e))
    except Exception as e:  # noqa: BLE001
        return InstallResult(spec.name, False, error=str(e))


def _build_probe_argv(argv: list[str]) -> list[str]:
    """Append ``--help`` (or a suitable probe flag) to the install_command."""
    # For uvx/npx: launcher + package[+flags]. We append --help at the end
    # so the process does not start the real server.
    return [*argv, "--help"]


# ----------------------------------------------------------------------
# Verify
# ----------------------------------------------------------------------

async def verify_server(spec: MCPServerSpec) -> bool:
    """Start the MCP server briefly, list its tools, then stop it.

    Returns True if the tool list was loaded successfully.
    """
    client = MCPClient(spec)
    try:
        await asyncio.wait_for(client.start(), timeout=60)
        tools = await client.list_tools()
        return isinstance(tools, list)
    except Exception as e:  # noqa: BLE001
        log.warning("verify_server[%s] failed: %s", spec.name, e)
        return False
    finally:
        await client.stop()


# ----------------------------------------------------------------------
# Persist user selection
# ----------------------------------------------------------------------

async def record_user_selection(
    selected: list[str], config_path: Path
) -> None:
    """Write the server names checked by the user to ``config_path``.

    Format: TOML fragment ``[mcp] enabled = ["name1", "name2"]`` — merged
    into the main config. An existing ``[mcp]`` table is replaced entirely
    (the wizard is considered the source of truth for the enabled list).
    """
    config_path = Path(config_path)
    selected_sorted = sorted(set(selected))

    existing_lines: list[str] = []
    if config_path.exists():
        existing_lines = config_path.read_text(encoding="utf-8").splitlines()

    # Filter out the existing [mcp] block
    out_lines: list[str] = []
    skip = False
    for line in existing_lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skip = stripped == "[mcp]"
            if not skip:
                out_lines.append(line)
            continue
        if not skip:
            out_lines.append(line)

    if out_lines and out_lines[-1].strip() != "":
        out_lines.append("")
    out_lines.append("[mcp]")
    formatted = ", ".join(f'"{n}"' for n in selected_sorted)
    out_lines.append(f"enabled = [{formatted}]")
    out_lines.append("")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(out_lines), encoding="utf-8")


# ----------------------------------------------------------------------
# Aggregate summary (useful for the wizard UI)
# ----------------------------------------------------------------------

async def install_and_verify(
    specs: list[MCPServerSpec],
) -> list[dict[str, Any]]:
    """Install and verify a list of specs sequentially.

    Parallel installation competes for uvx/npm cache locks — serial is safer.
    """
    results: list[dict[str, Any]] = []
    for spec in specs:
        install = await install_server(spec)
        verified = False
        if install.success:
            verified = await verify_server(spec)
        results.append(
            {
                "name": spec.name,
                "installed": install.success,
                "verified": verified,
                "error": install.error,
            }
        )
    return results
