"""CodexWorker — wraps `codex exec --json` as a Phase-6 worker subprocess.

Command layout (Research-Doc §B line ~92):

    codex exec --json
        --sandbox workspace-write
        --ask-for-approval never
        [--output-schema <path>]
        <prompt>

Authentication via `OPENAI_API_KEY` from T1 `build_worker_env`. Per-worker
`CODEX_HOME` points to `<run_dir>/.codex` so that parallel Codex workers do
not write to the same config cache (cross-talk risk, Research-Doc §B line 105).

Spawn discipline: NO shell=True, NO PTY, Win32 creationflags including
CREATE_BREAKAWAY_FROM_JOB.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from .process_utils import (
    contextlib_suppress,
    drain_stderr as _drain_stderr,
    worker_creationflags as _win32_creationflags,
)
from .stream_consumer import CodexStreamEvent, parse_codex_stream_json, read_ndjson_stream

logger = logging.getLogger(__name__)


def _build_codex_cmd(
    prompt: str,
    *,
    sandbox: str,
    ask_for_approval: str,
    output_schema: str | None,
    extra_args: tuple[str, ...] = (),
) -> list[str]:
    """Assembles the Codex CLI argv. Stable ordering for tests."""
    cmd: list[str] = [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        sandbox,
        "--ask-for-approval",
        ask_for_approval,
    ]
    if output_schema:
        cmd += ["--output-schema", output_schema]
    cmd += list(extra_args)
    cmd.append(prompt)  # Prompt is a positional arg at the end.
    return cmd


class CodexWorker:
    """Phase-6 worker that encapsulates `codex exec --json` as a subprocess."""

    cli: Literal["codex"] = "codex"

    def __init__(self) -> None:
        self.last_pid: int | None = None
        self.last_thread_id: str | None = None

    async def spawn(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        sandbox: str = "workspace-write",
        ask_for_approval: str = "never",
        output_schema: str | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> AsyncIterator[Any]:
        """Spawn `codex exec --json ...` and yield CodexStreamEvent instances.

        Args (Codex-specific):
            sandbox: 'workspace-write' (default) allows writes under cwd;
                'read-only' turns the worker into a critic equivalent.
            ask_for_approval: 'never' (default for non-interactive); 'ask'
                would produce stdin prompts — not suitable with pipes.
            output_schema: optional path to a JSON schema file against which
                the Codex output is validated.
            extra_args: escape hatch.

        Yields:
            CodexStreamEvent instances. Terminal events: `turn.completed`,
            `turn.failed`, `error`.
        """
        cmd = _build_codex_cmd(
            prompt,
            sandbox=sandbox,
            ask_for_approval=ask_for_approval,
            output_schema=output_schema,
            extra_args=extra_args,
        )

        log_dir.mkdir(parents=True, exist_ok=True)
        stream_log = log_dir / "stream.jsonl"
        stderr_log = log_dir / "stderr.log"
        creationflags = _win32_creationflags()

        logger.info(
            "CodexWorker[%s] spawn: cwd=%s sandbox=%s",
            worker_id,
            worktree,
            sandbox,
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(worktree),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        self.last_pid = proc.pid

        try:
            job.assign(proc.pid)
        except Exception:  # noqa: BLE001
            logger.warning(
                "CodexWorker[%s]: job.assign(pid=%d) failed",
                worker_id,
                proc.pid,
                exc_info=True,
            )

        stderr_task = asyncio.create_task(
            _drain_stderr(proc.stderr, stderr_log),
            name=f"codex-stderr-{worker_id}",
        )

        try:
            assert proc.stdout is not None  # noqa: S101
            async for event in read_ndjson_stream(
                proc.stdout,
                parser=parse_codex_stream_json,
                tee_path=stream_log,
            ):
                # First thread.started delivers the thread_id (resume anchor).
                if (
                    self.last_thread_id is None
                    and getattr(event, "type", None) == "thread.started"
                ):
                    self.last_thread_id = getattr(event, "thread_id", None)
                yield event
                # Terminal Codex events.
                etype = getattr(event, "type", None)
                if etype in ("turn.completed", "turn.failed", "error"):
                    break
        finally:
            try:
                await asyncio.wait_for(stderr_task, timeout=1.0)
            except asyncio.TimeoutError:
                stderr_task.cancel()
            with contextlib_suppress(ProcessLookupError):
                if proc.returncode is None:
                    proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "CodexWorker[%s]: subprocess wait() timeout",
                    worker_id,
                )


__all__ = ["CodexWorker", "CodexStreamEvent"]
