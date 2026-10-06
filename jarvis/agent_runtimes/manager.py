"""Install and update Hermes / OpenClaw with their own official tools.

Only ever started by the person (a button in an agent's Brain settings): an
install downloads and runs the project's own installer, an update runs the
runtime's own updater. One job per runtime at a time; its output goes to a
log file whose tail the UI shows. Before an update every running process of
that runtime is stopped — on Windows a running Hermes venv or OpenClaw
Gateway would hold files the updater must replace.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from jarvis.agent_runtimes import RUNTIME_NAMES, driver
from jarvis.agent_runtimes.base import child_env, runtimes_root

log = logging.getLogger(__name__)

JobKind = Literal["install", "update"]
JobState = Literal["running", "done", "failed"]

#: An installer that has not finished in this long is stopped.
_JOB_TIMEOUT_S: Final[float] = 30 * 60


@dataclass(slots=True)
class RuntimeJob:
    runtime: str
    kind: JobKind
    state: JobState = "running"
    started_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    finished_ms: int | None = None
    exit_code: int | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "kind": self.kind,
            "state": self.state,
            "started_ms": self.started_ms,
            "finished_ms": self.finished_ms,
            "exit_code": self.exit_code,
            "message": self.message,
            "log_tail": log_tail(self.runtime),
        }


_JOBS: dict[str, RuntimeJob] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}


def _log_path(runtime: str) -> Any:
    root = runtimes_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{runtime}-setup.log"


def log_tail(runtime: str, lines: int = 12) -> list[str]:
    try:
        text = _log_path(runtime).read_text(encoding="utf-8", errors="replace")
    except OSError:  # no setup has written a log yet: nothing to show
        return []
    return [line for line in text.splitlines() if line.strip()][-lines:]


def job(runtime: str) -> RuntimeJob | None:
    return _JOBS.get(runtime)


async def statuses(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Every runtime's status plus its running or last setup job."""
    out: list[dict[str, Any]] = []
    for name in RUNTIME_NAMES:
        status = await asyncio.to_thread(driver(name).detect, refresh=refresh)
        row = status.to_dict()
        current = _JOBS.get(name)
        row["job"] = current.to_dict() if current is not None else None
        out.append(row)
    return out


async def start(runtime: str, kind: JobKind) -> RuntimeJob:
    """Start an install or update job; returns the running job if one exists."""
    if runtime not in RUNTIME_NAMES:
        raise KeyError(runtime)
    current = _JOBS.get(runtime)
    if current is not None and current.state == "running":
        return current
    target = driver(runtime)
    argv = target.install_command() if kind == "install" else target.update_command()
    if not argv:
        failed = RuntimeJob(
            runtime, kind, state="failed", message=f"{target.label} is not installed."
        )
        failed.finished_ms = failed.started_ms
        _JOBS[runtime] = failed
        return failed
    new_job = RuntimeJob(runtime, kind)
    _JOBS[runtime] = new_job
    _TASKS[runtime] = asyncio.get_running_loop().create_task(_run(new_job, argv))
    return new_job


async def _run(current: RuntimeJob, argv: list[str]) -> None:
    from jarvis.core.process_tree import make_process_tree
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    target = driver(current.runtime)
    if current.kind == "update":
        await target.stop()
    path = _log_path(current.runtime)
    tree = make_process_tree(f"{current.runtime}-{current.kind}")
    try:
        with path.open("wb") as handle:
            handle.write(f"$ {' '.join(argv)}\n".encode())
            handle.flush()
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                env=child_env(),
                creationflags=NO_WINDOW_CREATIONFLAGS,
                start_new_session=True,
            )
            tree.assign(proc.pid)
            try:
                code = await asyncio.wait_for(proc.wait(), timeout=_JOB_TIMEOUT_S)
            except TimeoutError:  # reported through the job's state and message below
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
                code = -1
                current.message = "Stopped after 30 minutes."
        current.exit_code = code
        current.state = "done" if code == 0 else "failed"
        if code != 0 and not current.message:
            current.message = f"{target.label} {current.kind} failed (exit {code})."
    except OSError as exc:
        current.state = "failed"
        current.message = f"Could not start the {current.kind}: {exc}"
        log.warning("agent runtimes: %s %s could not start: %s", current.runtime, current.kind, exc)
    finally:
        tree.close()
        current.finished_ms = int(time.time() * 1000)
        if current.state == "running":
            # Cancelled (app shutdown): never leave a job that looks alive.
            current.state = "failed"
            current.message = current.message or f"The {current.kind} was interrupted."
        _TASKS.pop(current.runtime, None)
    # ``--version`` spawns a process: off the event loop.
    await asyncio.to_thread(target.detect, refresh=True)
