"""ShellHandler — starts a subprocess, captures stdout/stderr."""
from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import time
from typing import Any

from .base import HandlerResult

_STDOUT_CAP = 64 * 1024       # 64 KB
_STDERR_CAP = 16 * 1024       # 16 KB


class ShellHandler:
    async def execute(
        self,
        spec: Any,
        input_data: dict[str, Any],  # noqa: ARG002 — for a uniform protocol
    ) -> HandlerResult:
        try:
            argv = shlex.split(spec.command, posix=False)
        except ValueError as exc:
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error=f"command parse error: {exc}",
            )
        # On Windows, shlex posix=False leaves the outer quotes in the
        # token — strip them, otherwise subprocess treats the filename
        # as including the "".
        argv = [
            a[1:-1] if len(a) >= 2 and a[0] == a[-1] and a[0] in ('"', "'")
            else a
            for a in argv
        ]
        if not argv:
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error="empty command",
            )

        env = os.environ.copy()
        if spec.env:
            env.update(spec.env)

        cwd = spec.cwd or None

        start = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except FileNotFoundError as exc:
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error=f"command not found: {exc}",
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=spec.timeout_s,
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            duration_ms = int((time.perf_counter() - start) * 1000)
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error=f"timeout after {spec.timeout_s}s",
                metrics={"duration_ms": duration_ms},
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        if len(stdout) > _STDOUT_CAP:
            stdout = stdout[:_STDOUT_CAP] + "\n…(truncated)"
        if len(stderr) > _STDERR_CAP:
            stderr = stderr[:_STDERR_CAP] + "\n…(truncated)"

        rc = proc.returncode if proc.returncode is not None else -1
        success = rc == 0
        return HandlerResult(
            success=success,
            output=stdout.strip(),
            exit_code=rc,
            error=stderr.strip() if not success else None,
            metrics={
                "duration_ms": duration_ms,
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
            },
        )
