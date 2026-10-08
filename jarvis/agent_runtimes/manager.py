"""Install and keep Hermes / OpenClaw current with their own official tools.

Nobody sets either runtime up by hand. Choosing Hermes or OpenClaw for an
agent starts :func:`ensure`: the project's own installer when it is missing
(OpenClaw's also adds the Node.js it needs), its own updater when it is too
old. A turn that finds its runtime not ready waits for that job
(:func:`wait_ready`). While any agent uses a runtime, :func:`keep_current`
runs the updater once a day when no turn of it is running; an update that
leaves the runtime unable to start is repaired by a fresh install.

One job per runtime at a time; its output goes to a log file whose tail the
UI can show. Before an update every running process of that runtime is
stopped — on Windows a running Hermes venv or OpenClaw Gateway would hold
files the updater must replace. None of this calls a model, so nothing here
spends a key.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from jarvis.agent_runtimes import RUNTIME_NAMES, driver
from jarvis.agent_runtimes.base import RuntimeStatus, child_env, runtimes_root

log = logging.getLogger(__name__)

JobKind = Literal["install", "update"]
JobState = Literal["running", "done", "failed"]

#: An installer that has not finished in this long is stopped.
_JOB_TIMEOUT_S: Final[float] = 30 * 60

#: First automatic update round after the app starts, then one per day.
_FIRST_UPDATE_DELAY_S: Final[float] = 10 * 60
_UPDATE_EVERY_S: Final[float] = 24 * 60 * 60


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
    _TASKS[runtime] = asyncio.get_running_loop().create_task(_run_job(new_job, argv))
    return new_job


def _needed(status: RuntimeStatus) -> JobKind | None:
    """Which job makes ``status`` ready; ``None`` when it already is."""
    if status.ready:
        return None
    if not status.installed or status.problem_kind == "node":
        # OpenClaw's own installer is also what adds the Node.js it needs.
        return "install"
    return "update"


async def ensure(runtime: str) -> RuntimeJob | None:
    """Start the install or update ``runtime`` needs; ``None`` when it is ready."""
    current = _JOBS.get(runtime)
    if current is not None and current.state == "running":
        return current
    status = await asyncio.to_thread(driver(runtime).detect, refresh=True)
    kind = _needed(status)
    if kind is None:
        return None
    log.info(
        "agent runtimes: %s is not ready (%s), starting its %s",
        runtime,
        status.problem_kind,
        kind,
    )
    return await start(runtime, kind)


async def wait_ready(runtime: str, *, timeout_s: float = _JOB_TIMEOUT_S) -> RuntimeStatus:
    """Set ``runtime`` up when it needs it, wait for that, and return its status."""
    await ensure(runtime)
    task = _TASKS.get(runtime)
    if task is not None:
        # Shielded: a turn that gives up must not cancel a setup others wait for.
        with contextlib.suppress(TimeoutError):  # the status below says it is not ready
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
    return await asyncio.to_thread(driver(runtime).detect)


async def _run_job(current: RuntimeJob, argv: list[str]) -> None:
    try:
        await _run(current, argv)
        if current.kind != "update":
            return
        status = await asyncio.to_thread(driver(current.runtime).detect, refresh=True)
        if status.ready:
            return
        # The updater left a runtime that cannot start: the project's own
        # installer replaces it.
        install = driver(current.runtime).install_command()
        if not install:
            return
        log.warning(
            "agent runtimes: %s is not ready after its update (%s); reinstalling",
            current.runtime,
            status.problem_kind,
        )
        repair = RuntimeJob(current.runtime, "install")
        _JOBS[current.runtime] = repair
        await _run(repair, install)
    finally:
        _TASKS.pop(current.runtime, None)


async def keep_current(in_use: Callable[[], Awaitable[set[str]]]) -> None:
    """Update the runtimes agents use once a day, while none of their turns runs.

    ``in_use`` answers which runtimes at least one agent is set to. Runs until
    cancelled (the society runtime owns the task); a failed round is logged
    and the next one comes a day later.
    """
    await asyncio.sleep(_FIRST_UPDATE_DELAY_S)
    while True:
        try:
            used = await in_use()
            for name in RUNTIME_NAMES:
                if name in used:
                    await _update_when_idle(name)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — logged; the next daily round tries again
            log.warning("agent runtimes: the daily update round failed", exc_info=True)
        await asyncio.sleep(_UPDATE_EVERY_S)


async def _update_when_idle(name: str) -> None:
    target = driver(name)
    if target.busy():
        log.info("agent runtimes: %s is busy; its update waits for the next round", name)
        return
    current = _JOBS.get(name)
    if current is not None and current.state == "running":
        return
    status = await asyncio.to_thread(target.detect, refresh=True)
    kind: JobKind = "update" if status.installed and status.problem_kind != "node" else "install"
    await start(name, kind)
    task = _TASKS.get(name)
    if task is not None:
        await asyncio.shield(task)


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
    # ``--version`` spawns a process: off the event loop.
    await asyncio.to_thread(target.detect, refresh=True)
