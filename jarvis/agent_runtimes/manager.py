"""Install and keep Hermes / OpenClaw current with their own official tools.

Nobody sets either runtime up by hand. Choosing Hermes or OpenClaw for an
agent starts :func:`ensure`: the project's own installer when it is missing,
an update when it is too old, and (Hermes) a one-time preparation of the
data root Jarvis' agents share, so no turn pays for it. A turn that finds its
runtime not ready waits for that job (:func:`wait_ready`).

Every install and update goes to the release the agent-runtimes canary
verified (``runtime-versions.json``, see ``versions``), never to upstream
latest. While any agent uses a runtime, :func:`keep_current` brings an older
install up to that release once a day when no turn of it is running. A setup
that leaves the runtime unable to start is repaired by a fresh install, and
when that fails too, by reinstalling the last release that worked.

One job per runtime at a time, holding the runtime alone (``RuntimeGate``):
it waits for running turns and new turns wait for it — an updater replaces
files a running turn holds (on Windows it cannot). Its output goes to a log
file whose tail the UI and the turn's error show. None of this calls a model,
so nothing here spends a key.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from jarvis.agent_runtimes import RUNTIME_NAMES, base, driver, versions
from jarvis.agent_runtimes.base import (
    SETUP_MISSING_TOOL_EXIT,
    RuntimeStatus,
    child_env,
    parse_version,
)

log = logging.getLogger(__name__)

JobKind = Literal["install", "update", "prepare"]
JobState = Literal["running", "done", "failed"]

#: An installer that has not finished in this long is stopped.
_JOB_TIMEOUT_S: Final[float] = 30 * 60

#: How long a setup job waits for running turns before it gives up.
_GATE_WAIT_S: Final[float] = 20 * 60

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


def _log_path(runtime: str) -> Path:
    root = base.runtimes_root()
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


def _command(target: Any, kind: JobKind) -> tuple[list[str], dict[str, str] | None] | None:
    if kind == "prepare":
        prepare = getattr(target, "prepare_command", None)
        return prepare() if prepare is not None else None
    argv = target.install_command() if kind == "install" else target.update_command()
    return (argv, None) if argv else None


async def start(runtime: str, kind: JobKind) -> RuntimeJob:
    """Start an install, update or prepare job; returns the running job if one exists."""
    if runtime not in RUNTIME_NAMES:
        raise KeyError(runtime)
    current = _JOBS.get(runtime)
    if current is not None and current.state == "running":
        return current
    target = driver(runtime)
    command = await asyncio.to_thread(_command, target, kind)
    if not command:
        failed = RuntimeJob(
            runtime, kind, state="failed", message=f"{target.label} is not installed."
        )
        failed.finished_ms = failed.started_ms
        _JOBS[runtime] = failed
        return failed
    new_job = RuntimeJob(runtime, kind)
    _JOBS[runtime] = new_job
    _TASKS[runtime] = asyncio.get_running_loop().create_task(_run_job(new_job, *command))
    return new_job


def needed(target: Any, status: RuntimeStatus) -> JobKind | None:
    """Which job makes ``status`` ready to run turns; ``None`` when it already is."""
    if status.ready:
        prepare = getattr(target, "needs_prepare", None)
        return "prepare" if prepare is not None and prepare(status) else None
    if not status.installed or status.problem_kind == "node":
        # Jarvis' own OpenClaw copy brings the Node.js it needs.
        return "install"
    return "update"


async def ensure(runtime: str) -> RuntimeJob | None:
    """Start the job ``runtime`` needs; ``None`` when it is ready."""
    current = _JOBS.get(runtime)
    if current is not None and current.state == "running":
        return current
    target = driver(runtime)
    status = await asyncio.to_thread(target.detect, refresh=True)
    kind = needed(target, status)
    if kind is None:
        return None
    log.info(
        "agent runtimes: %s is not ready (%s), starting its %s",
        runtime,
        status.problem_kind or "not prepared",
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


def failure_reason(runtime: str) -> str:
    """Why the last setup of ``runtime`` failed, for the chat ("" when it did not)."""
    current = _JOBS.get(runtime)
    if current is None or current.state != "failed":
        return ""
    return current.message


@contextlib.asynccontextmanager
async def _alone(target: Any) -> AsyncIterator[None]:
    gate = getattr(target, "gate", None)
    if gate is None:
        yield
        return
    async with gate.exclusive(_GATE_WAIT_S):
        yield


async def _run_job(current: RuntimeJob, argv: list[str], env: dict[str, str] | None) -> None:
    target = driver(current.runtime)
    try:
        try:
            async with _alone(target):
                await _run_setup(current, argv, env)
        except TimeoutError:
            current.state = "failed"
            current.finished_ms = int(time.time() * 1000)
            current.message = (
                f"{target.label} could not be set up while its agents kept working. "
                "It is tried again when they are done."
            )
    finally:
        _TASKS.pop(current.runtime, None)


async def _run_setup(current: RuntimeJob, argv: list[str], env: dict[str, str] | None) -> None:
    """The job, then whatever it still owes: repair, rollback, prepare."""
    target = driver(current.runtime)
    await _run(current, argv, env)
    status = await asyncio.to_thread(target.detect, refresh=True)
    if current.kind == "prepare":
        if current.state == "done":
            await asyncio.to_thread(target.mark_prepared, status)
        return
    if not status.ready and current.kind == "update":
        # The update left a runtime that cannot start: a fresh install.
        status = await _follow_up(current.runtime, "install", target.install_command())
    if not status.ready:
        status = await _roll_back(current.runtime, target, status)
    if status.ready:
        revision = getattr(target, "revision", None)
        await asyncio.to_thread(
            versions.remember_good,
            current.runtime,
            status.version,
            revision(status) if revision is not None else "",
        )
        if needed(target, status) == "prepare":
            command = target.prepare_command()
            if command:
                await _follow_up(current.runtime, "prepare", *command)


async def _follow_up(
    runtime: str, kind: JobKind, argv: list[str] | None, env: dict[str, str] | None = None
) -> RuntimeStatus:
    target = driver(runtime)
    if argv:
        log.info("agent runtimes: %s %s follows", runtime, kind)
        follow = RuntimeJob(runtime, kind)
        _JOBS[runtime] = follow
        await _run(follow, argv, env)
        status = await asyncio.to_thread(target.detect, refresh=True)
        if kind == "prepare" and follow.state == "done":
            await asyncio.to_thread(target.mark_prepared, status)
        return status
    return await asyncio.to_thread(target.detect, refresh=True)


async def _roll_back(runtime: str, target: Any, status: RuntimeStatus) -> RuntimeStatus:
    """Reinstall the last release that finished a setup ready, when it differs."""
    good = await asyncio.to_thread(versions.last_good, runtime)
    revision = str((good or {}).get("revision") or "")
    if not good or not revision:
        return status
    if parse_version(str(good.get("version"))) == versions.pin(runtime).tested:
        return status  # the release that just failed: nothing older to go back to
    argv = target.install_command(revision)
    log.warning("agent runtimes: %s rolls back to %s", runtime, good.get("version"))
    return await _follow_up(runtime, "install", argv)


async def keep_current(in_use: Callable[[], Awaitable[set[str]]]) -> None:
    """Bring the runtimes agents use up to the tested release, once a day.

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
    kind = needed(target, status)
    if kind is None:
        tested = versions.pin(name).tested
        installed = parse_version(status.version)
        if tested is None or installed is None or installed >= tested:
            return  # at (or past) the tested release: nothing to do
        kind = "update"
    await start(name, kind)
    task = _TASKS.get(name)
    if task is not None:
        await asyncio.shield(task)


def _missing_launcher(argv: list[str]) -> str:
    """A plain reason when the shell an installer needs is missing ("" otherwise)."""
    head = Path(argv[0]).name.lower() if argv else ""
    if head in {"bash", "powershell", "powershell.exe", "pwsh"} and shutil.which(argv[0]) is None:
        return f"Setup needs {argv[0]}, which is not installed on this computer."
    return ""


def _failure_message(label: str, kind: str, code: int, runtime: str) -> str:
    tail = [line.strip() for line in log_tail(runtime, 3) if line.strip()]
    last = tail[-1][:300] if tail else ""
    if code == SETUP_MISSING_TOOL_EXIT and last:
        return last
    message = f"{label} {kind} failed (exit {code})."
    return f"{message} {last}" if last else message


async def _run(current: RuntimeJob, argv: list[str], env: dict[str, str] | None = None) -> None:
    from jarvis.core.process_tree import make_process_tree
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    target = driver(current.runtime)
    if current.kind == "update":
        await target.stop()
    path = _log_path(current.runtime)
    missing = _missing_launcher(argv)
    tree = make_process_tree(f"{current.runtime}-{current.kind}")
    try:
        if missing:
            path.write_text(missing + "\n", encoding="utf-8")
            raise OSError(missing)
        with path.open("wb") as handle:
            # Only the program and its first argument: an inline script body
            # is long and says nothing the log below does not.
            handle.write(f"$ {' '.join(argv[:2])} ...\n".encode())
            handle.flush()
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                env=env if env is not None else child_env(),
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
            current.message = _failure_message(target.label, current.kind, code, current.runtime)
    except OSError as exc:
        current.state = "failed"
        current.message = missing or f"Could not start the {current.kind}: {exc}"
        log.warning("agent runtimes: %s %s could not start: %s", current.runtime, current.kind, exc)
    finally:
        tree.close()
        current.finished_ms = int(time.time() * 1000)
        if current.state == "running":
            # Cancelled (app shutdown): never leave a job that looks alive.
            current.state = "failed"
            current.message = current.message or f"The {current.kind} was interrupted."
    # What the installer registered on the persistent PATH is visible from now
    # on (never a Jarvis agent folder); ``--version`` spawns: off the loop.
    await asyncio.to_thread(_refresh_path)
    await asyncio.to_thread(target.detect, refresh=True)


def _refresh_path() -> None:
    from jarvis.agent_runtimes.path_cleanup import is_jarvis_hermes_bin
    from jarvis.core.path_augment import refresh_from_persistent_path

    try:
        refresh_from_persistent_path(skip=is_jarvis_hermes_bin)
    except OSError as exc:  # the registry is unreadable: the well-known folders still apply
        log.debug("agent runtimes: persistent PATH not read: %s", exc)
