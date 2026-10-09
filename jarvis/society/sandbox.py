"""Opt-in code sandbox. Only Docker-managed volumes cross command boundaries.

The model/controller stays in Jarvis; all code runs in an unprivileged Linux
container with no host mounts, credentials, network, or Docker socket. This is
container isolation, not a separate kernel per agent. No local fallback exists.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)
IMAGE = "python:3.12-slim"
MAX_FILE_BYTES = 1_000_000
OUTPUT_BYTES = 32_000


class SandboxUnavailable(RuntimeError):
    """The requested sandbox could not execute; never retry on the host."""


def for_agent(runtime: Any, agent_id: str) -> Sandbox:
    """Bind storage to the identity, independent of editable workspace paths."""
    return Sandbox(Path(runtime.data_dir) / "sandboxes" / agent_id)


async def _docker(
    *args: str,
    stdin: bytes = b"",
    timeout_s: float = 30,
) -> tuple[int, bytes]:
    exe = shutil.which("docker")
    if exe is None:
        raise SandboxUnavailable(
            "Install and start Docker with Linux containers to use the sandbox."
        )
    try:
        process = await asyncio.create_subprocess_exec(
            exe,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except OSError as exc:
        raise SandboxUnavailable(f"Docker could not start: {exc}") from exc
    output = bytearray()

    async def exchange() -> None:
        assert process.stdin is not None and process.stdout is not None

        async def feed() -> None:
            try:
                process.stdin.write(stdin)
                await process.stdin.drain()
                process.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                # A rejected Docker launch can close stdin before reading it.
                log.debug("sandbox Docker stdin closed before input was consumed")

        async def read() -> None:
            while chunk := await process.stdout.read(65536):
                # Drain every byte without retaining unbounded model-controlled output.
                remaining = MAX_FILE_BYTES * 2 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])

        await asyncio.gather(feed(), read())
        await process.wait()

    try:
        await asyncio.wait_for(exchange(), timeout_s)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return process.returncode or 0, bytes(output)


async def status() -> dict[str, Any]:
    """Probe only on explicit feature use; never pull an image in a probe."""
    try:
        rc, out = await _docker("info", "--format", "{{.OSType}}", timeout_s=30)
        if rc or out.strip() != b"linux":
            raise SandboxUnavailable("Start Docker with Linux containers.")
        rc, _ = await _docker("image", "inspect", IMAGE, timeout_s=30)
        return {
            "available": not rc,
            "image": IMAGE,
            "needs_image": bool(rc),
            "message": "Download the sandbox image to continue." if rc else "",
        }
    except (SandboxUnavailable, TimeoutError) as exc:
        log.debug("sandbox readiness unavailable: %s", exc)
        return {
            "available": False,
            "image": IMAGE,
            "needs_image": False,
            "message": str(exc) or "Docker did not answer in time.",
        }


async def prepare() -> dict[str, Any]:
    """Explicit user action: download the fixed runtime image."""
    rc, out = await _docker("pull", IMAGE, timeout_s=300)
    if rc:
        raise SandboxUnavailable(out.decode("utf-8", "replace")[-2000:])
    return await status()


class Sandbox:
    """One persistent volume per agent workspace, one disposable container per call."""

    def __init__(self, workspace: Path) -> None:
        namespace = str(workspace.resolve()).encode("utf-8")
        self.volume = "jarvis-sandbox-" + hashlib.sha256(namespace).hexdigest()[:24]

    def _arguments(
        self,
        name: str,
        *,
        initialize: bool = False,
        timeout_s: float = 120,
    ) -> list[str]:
        return [
            "run",
            "--rm",
            "--init",
            "--pull=never",
            "-i",
            "--name",
            name,
            "--label",
            "personal-jarvis.sandbox=true",
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--pids-limit",
            "128",
            "--ulimit",
            "fsize=16777216:16777216",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",  # noqa: S108 — private container tmpfs
            "--user",
            "0:0" if initialize else "65532:65532",
            "--mount",
            f"type=volume,src={self.volume},dst=/workspace,volume-nocopy",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/workspace",
            "--env",
            "PYTHONIOENCODING=utf-8",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            # An in-container deadline also ends work after a host crash.
            "--entrypoint",
            "/usr/bin/timeout",
            IMAGE,
            "--kill-after=2s",
            f"{math.ceil(timeout_s)}s",
            "/bin/sh",
            "-s",
        ]

    async def _run(
        self,
        command: bytes,
        timeout_s: float,
        *,
        initialize: bool = False,
    ) -> tuple[int, bytes]:
        name = "jarvis-sandbox-run-" + uuid.uuid4().hex
        try:
            return await _docker(
                *self._arguments(name, initialize=initialize, timeout_s=timeout_s),
                stdin=command,
                timeout_s=timeout_s + 10,
            )
        finally:
            # Killing the CLI alone leaves its container alive. Remove exactly our
            # own container on success, cancellation, disconnect and timeout.
            cleanup = asyncio.create_task(_docker("rm", "--force", name, timeout_s=15))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
            except (SandboxUnavailable, TimeoutError):
                log.warning("could not reap sandbox container %s", name, exc_info=True)

    async def execute(self, command: str, timeout_s: float = 120) -> tuple[int, bytes]:
        if not math.isfinite(timeout_s):
            timeout_s = 120
        timeout_s = max(1, min(timeout_s, 900))
        # This fixed bootstrap cannot execute workspace contents. Volumes are
        # mounted without copying image content and never bind a host directory.
        rc, out = await self._run(b"chmod 777 /workspace\n", 30, initialize=True)
        if rc:
            raise SandboxUnavailable(out.decode("utf-8", "replace")[-2000:])
        result = await self._run(command.encode("utf-8"), timeout_s)
        if result[0] == 124:
            raise TimeoutError("Sandbox command timed out.")
        return result

    async def file(self, action: str, path: str = ".", content: str = "") -> tuple[int, bytes]:
        payload = json.dumps({"action": action, "path": path, "content": content})
        # JSON is Python source only as a quoted string literal, never shell code.
        program = _FILE_PROGRAM.replace("PAYLOAD", repr(payload))
        return await self.execute(
            "python -I - <<'JARVIS_SANDBOX_FILE'\n" + program + "\nJARVIS_SANDBOX_FILE\n", 30
        )


_FILE_PROGRAM = """import base64, json
from pathlib import Path
p = json.loads(PAYLOAD)
root = Path('/workspace')
target = (root / p['path']).resolve()
if not target.is_relative_to(root):
    raise ValueError('Path must stay inside the sandbox workspace')
if p['action'] == 'write':
    data = p['content'].encode('utf-8')
    if len(data) > 1000000:
        raise ValueError('File exceeds 1 MB')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(json.dumps({'path': str(target.relative_to(root)), 'bytes': len(data)}))
elif p['action'] == 'list':
    print(json.dumps([{'name': x.name, 'directory': x.is_dir()}
                      for x in sorted(target.iterdir())[:200]]))
else:
    if not target.is_file():
        raise ValueError('Choose a regular file')
    with target.open('rb') as f:
        data = f.read(1000001)
    if len(data) > 1000000:
        raise ValueError('File exceeds 1 MB')
    if p['action'] == 'download':
        print(base64.b64encode(data).decode('ascii'))
    else:
        print(data.decode('utf-8', errors='replace'))
"""


class SandboxTool:
    """The only machine-facing hand exposed to a sandbox agent."""

    name = "society_sandbox"
    description = (
        "Run code and tests or write/read/list files in your isolated Linux sandbox. "
        "Python 3.12 is available. Files persist between calls. No network, host files, "
        "credentials, browser or plugins. Use relative paths under /workspace. "
        "Return file paths so the user can download results from the agent settings."
    )
    risk_tier = "monitor"
    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["run", "write", "read", "list"]},
            "command": {"type": "string"},
            "path": {"type": "string"},
            "content": {"type": "string"},
            "timeout_s": {"type": "number"},
        },
        "required": ["action"],
    }

    def __init__(self, runtime: Any, agent_id: str, workspace: Path) -> None:
        self.runtime, self.agent_id = runtime, agent_id
        self.sandbox = for_agent(runtime, agent_id)

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        return "safe" if args.get("action") in ("read", "list") else "monitor"

    def read_only_for_args(self, args: dict[str, Any]) -> bool:
        return args.get("action") in ("read", "list")

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        agent = await self.runtime.roster.get(self.agent_id)
        if (
            agent is None
            or str(agent.state) != "active"
            or getattr(agent, "execution_environment", "local") != "sandbox"
            or await self.runtime.store.kill_switch()
        ):
            return ToolResult(False, None, "Sandbox execution is no longer enabled.")
        action = args.get("action")
        started = time.monotonic()
        try:
            if action == "run":
                command = str(args.get("command") or "")
                if not command.strip() or len(command) > MAX_FILE_BYTES:
                    raise ValueError("Supply a command shorter than 1 MB.")
                rc, out = await self.sandbox.execute(command, float(args.get("timeout_s") or 120))
            elif action in ("read", "write", "list"):
                content = str(args.get("content") or "")
                if len(content.encode("utf-8")) > MAX_FILE_BYTES:
                    raise ValueError("File exceeds 1 MB.")
                rc, out = await self.sandbox.file(
                    str(action), str(args.get("path") or "."), content
                )
            else:
                raise ValueError("Unknown sandbox action.")
        except (SandboxUnavailable, TimeoutError, ValueError) as exc:
            log.info("sandbox action %s for %s failed: %s", action, self.agent_id, exc)
            return ToolResult(False, None, str(exc) or "Sandbox command timed out.")
        return ToolResult(
            rc == 0,
            {
                "output": out[:OUTPUT_BYTES].decode("utf-8", "replace"),
                "truncated": len(out) > OUTPUT_BYTES,
                "exit_code": rc,
                "seconds": round(time.monotonic() - started, 2),
                "environment": "sandbox",
            },
            None if rc == 0 else f"Sandbox exited with status {rc}.",
        )
