"""Background agent service — routines and chat channels outlive the window.

The desktop app owns everything while it is open. When the user quits it and
there is work to keep (an armed routine, a running Telegram/Discord channel),
it starts this service: the same backend as ``launcher --headless``, without a
window, microphone, wake word or overlay. The service takes the single-instance
lock and the admin port, so routines fire, channels answer, and the Agentic IDE
re-attaches to its PTY host exactly as in a headless install.

Opening the app again reverses the hand-off. The launcher finds the service as
the lock holder, drops a handover request file, and the service shuts down
cleanly and releases the lock; the desktop then boots normally. A file (not
an HTTP call) carries the request because it also works while the service is
still booting, needs no credentials, and costs one ``stat`` per half second.

Exactly one of the two ever runs: both go through the same lock. Lifecycle
states live under ``user_data_dir()/background`` with the instance suffix, so a
dev instance never touches the default instance's service.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

#: argv flag that turns ``launcher --headless`` into the background service.
SERVICE_FLAG = "--background-service"
#: argv flag: wait for this pid (the quitting desktop app) before booting.
AFTER_PID_FLAG = "--after-pid"

#: How long a booting service waits for the quitting desktop to exit.
PARENT_EXIT_WAIT_S = 90.0
#: A handover request older than this is a leftover from a crashed launch.
HANDOVER_MAX_AGE_S = 120.0
#: How often the service checks for a handover request.
HANDOVER_POLL_S = 0.5
#: How often the service re-checks that it still has work to keep.
IDLE_CHECK_S = 300.0
#: Consecutive empty checks before the service exits on its own.
IDLE_STRIKES = 2
#: How long the desktop waits for the service to hand back before stopping it.
TAKEOVER_WAIT_S = 45.0
#: How long a handing-back service lets routine runs in flight finish.
HANDOVER_DRAIN_S = 30.0


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _state_dir() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "background"


def _suffix() -> str:
    from jarvis.core.instance import current_instance

    return current_instance().state_file_suffix


def marker_path() -> Path:
    """``{pid, port, started_at}`` of the running service (absent = none)."""
    return _state_dir() / f"service{_suffix()}.json"


def handover_path() -> Path:
    """Exists while a desktop launch is waiting for the service to let go."""
    return _state_dir() / f"handover{_suffix()}.request"


def log_path() -> Path:
    """The service's own log, next to ``jarvis_desktop.log``."""
    from jarvis.ui.desktop_log import desktop_log_path

    return desktop_log_path().with_name("jarvis_background.log")


# ---------------------------------------------------------------------------
# What is worth keeping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackgroundWork:
    """The work a closed window would otherwise stop."""

    routines: int = 0
    running: int = 0
    channels: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.routines or self.running or self.channels)

    def describe(self) -> str:
        parts: list[str] = []
        if self.routines:
            parts.append(f"{self.routines} armed routine(s)")
        if self.running:
            parts.append(f"{self.running} run(s) in flight")
        if self.channels:
            parts.append("channels: " + ", ".join(self.channels))
        return "; ".join(parts) or "nothing"


def work_from_state(state: Any) -> BackgroundWork:
    """Read the live work off ``app.state``. Never raises, never awaits."""
    routines = running = 0
    scheduler = getattr(state, "task_scheduler", None)
    pending = getattr(scheduler, "pending_work", None)
    if callable(pending):
        try:
            routines, running = pending()
        except Exception:  # noqa: BLE001 — a broken scheduler means no routines to keep
            logger.opt(exception=True).debug("background: scheduler state unreadable")
    channels: tuple[str, ...] = ()
    manager = getattr(state, "channel_manager", None)
    started = getattr(manager, "started", None)
    if callable(started):
        try:
            channels = tuple(str(name) for name in started())
        except Exception:  # noqa: BLE001 — same: unreadable means none to keep
            logger.opt(exception=True).debug("background: channel state unreadable")
    return BackgroundWork(routines=int(routines), running=int(running), channels=channels)


def keep_running_enabled(cfg: Any) -> bool:
    """``[background] keep_agents_running`` (default on)."""
    section = getattr(cfg, "background", None)
    return bool(getattr(section, "keep_agents_running", True))


# ---------------------------------------------------------------------------
# Marker + handover request
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def write_marker(port: int) -> None:
    _write_json(
        marker_path(),
        {"pid": os.getpid(), "port": int(port), "started_at": time.time()},
    )


def clear_marker() -> None:
    """Remove the marker if it is still ours."""
    path = marker_path()
    data = read_marker()
    if data is not None and int(data.get("pid") or 0) not in (0, os.getpid()):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("background: marker not removed: {}", exc)


def read_marker() -> dict[str, Any] | None:
    try:
        data = json.loads(marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):  # no readable marker means no service
        return None
    return data if isinstance(data, dict) else None


def _is_service_process(pid: int) -> bool:
    """The pid exists AND is a background service (pids are reused)."""
    try:
        import psutil

        cmdline = psutil.Process(int(pid)).cmdline()
    except Exception:  # noqa: BLE001 — gone or unreadable: not our service
        return False
    return SERVICE_FLAG in cmdline


def service_pid(*, is_service: Callable[[int], bool] = _is_service_process) -> int | None:
    """Pid of the running background service of this instance, or ``None``."""
    data = read_marker()
    if not data:
        return None
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):  # a garbled marker names no service
        return None
    if pid <= 0 or pid == os.getpid() or not is_service(pid):
        return None
    return pid


def request_handover() -> None:
    _write_json(handover_path(), {"pid": os.getpid(), "requested_at": time.time()})


def clear_handover_request() -> None:
    try:
        handover_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("background: handover request not removed: {}", exc)


def handover_requested(*, now: Callable[[], float] = time.time) -> bool:
    """A desktop launch is waiting. A request older than two minutes is a
    leftover from a launch that crashed and is ignored."""
    try:
        age = now() - handover_path().stat().st_mtime
    except OSError:  # no request file
        return False
    return age <= HANDOVER_MAX_AGE_S


# ---------------------------------------------------------------------------
# Starting the service
# ---------------------------------------------------------------------------


def _source_root() -> str:
    import jarvis

    return str(Path(jarvis.__file__).resolve().parent.parent)


def _windowless_python(executable: str) -> str:
    """``pythonw.exe`` beside a ``python.exe`` on Windows; unchanged elsewhere."""
    if sys.platform != "win32":
        return executable
    candidate = Path(executable).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else executable


def service_command(
    *,
    after_pid: int | None,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    """Argv that starts the background service for the current instance.

    A source install runs the web launcher through the interpreter; a frozen
    build re-enters its own executable with :data:`SERVICE_FLAG` first (it has
    no ``-m``), which ``jarvis/__main__.py`` routes to the same launcher.
    """
    from jarvis.core.instance import current_instance

    active = sys.executable if executable is None else executable
    if frozen is None:
        from jarvis.core.frozen import is_frozen

        frozen = is_frozen()
    tail = [*current_instance().launcher_args]
    if after_pid is not None:
        tail += [AFTER_PID_FLAG, str(int(after_pid))]
    if frozen:
        return [active, SERVICE_FLAG, *tail]
    return [
        _windowless_python(active),
        "-m",
        "jarvis.ui.web.launcher",
        "--headless",
        SERVICE_FLAG,
        *tail,
    ]


def _spawn_windows_outside_job(argv: list[str], cwd: str, env: dict[str, str]) -> bool:
    """Start ``argv`` so no job object of the app can take it down.

    Same reasoning as the PTY host (``pty_host_client._start_host_windows``):
    a child leaves a kill-on-close job only with ``CREATE_BREAKAWAY_FROM_JOB``,
    and when the job refuses that, WMI starts the process outside every job.
    """
    import subprocess

    from jarvis.ui.relauncher import detached_popen_kwargs

    try:
        subprocess.Popen(argv, **detached_popen_kwargs(cwd=cwd, env=env))  # type: ignore[call-overload]  # noqa: S603
        return True
    except PermissionError:
        logger.info("background: the app's job refuses breakaway — starting the service via WMI")
    except OSError as exc:
        logger.warning("background: service could not be started: {}", exc)
        return False

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    line = subprocess.list2cmdline(argv).replace("'", "''")
    folder = cwd.replace("'", "''")
    script = (
        "$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly "
        "-Property @{ShowWindow=[uint16]0}; "
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
        f"@{{CommandLine='{line}'; CurrentDirectory='{folder}'; ProcessStartupInformation=$si}}; "
        "exit [int]$r.ReturnValue"
    )
    try:
        done = subprocess.run(  # noqa: S603 — fixed PowerShell script, our own argv
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            creationflags=NO_WINDOW_CREATIONFLAGS,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("background: WMI start failed: {}", exc)
        return False
    if done.returncode != 0:
        logger.warning("background: WMI start returned {}", done.returncode)
        return False
    return True


def spawn_service(*, after_pid: int | None) -> bool:
    """Start the service detached from this process. Never raises."""
    try:
        from jarvis.core.instance import current_instance
        from jarvis.ui.relauncher import fresh_user_env, restart_workdir, spawn_detached

        cwd = restart_workdir(_source_root())
        env = current_instance().environ(fresh_user_env())
        env["PYTHONIOENCODING"] = "utf-8"
        argv = service_command(after_pid=after_pid)
        if sys.platform == "win32":
            return _spawn_windows_outside_job(argv, cwd, env)
        spawn_detached(argv, cwd=cwd, env=env)
        return True
    except Exception as exc:  # noqa: BLE001 — a failed hand-off must never block quitting
        logger.opt(exception=exc).warning("background: service could not be started")
        return False


def hand_off_on_quit(state: Any, cfg: Any, *, pid: int | None = None) -> bool:
    """Called by the quitting desktop app: start the service if there is work.

    Returns ``True`` when a service was started. Never raises.
    """
    try:
        from jarvis.core.instance import current_instance

        if not current_instance().owns_ambient_duties:
            # A dev instance carries no channels and must not linger unseen.
            return False
        if not keep_running_enabled(cfg):
            logger.info("background: keep_agents_running is off — everything stops with the app")
            return False
        work = work_from_state(state)
        if not work:
            logger.info("background: no routines or channels to keep — no background service")
            return False
        # A leftover request would make the new service exit at once.
        clear_handover_request()
        started = spawn_service(after_pid=os.getpid() if pid is None else pid)
        logger.info(
            "background: {} the background service to keep {}",
            "started" if started else "could NOT start",
            work.describe(),
        )
        return started
    except Exception as exc:  # noqa: BLE001 — quitting always wins
        logger.opt(exception=exc).warning("background: hand-off skipped")
        return False


# ---------------------------------------------------------------------------
# Taking over from the service (desktop launch)
# ---------------------------------------------------------------------------


def take_over_from_service(
    acquire: Callable[[], Any],
    *,
    busy_error: type[BaseException],
    wait_s: float = TAKEOVER_WAIT_S,
    find_pid: Callable[[], int | None] = service_pid,
    alive: Callable[[int], bool] = _is_service_process,
    terminate: Callable[[int], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Any | None:
    """Ask the running service to hand back, then take the lock.

    Returns the acquired lock, or ``None`` when no service holds it (the
    caller then runs its normal "already running" handling). A service that
    does not let go within ``wait_s`` is stopped: it is our own windowless
    process and the user has just opened the app.
    """
    pid = find_pid()
    if pid is None:
        return None
    logger.info("launcher: background service pid={} holds the app — asking it to hand back", pid)
    request_handover()
    try:
        deadline = now() + wait_s
        while now() < deadline:
            try:
                return acquire()
            except busy_error:  # still shutting down: poll again
                sleep(0.25)
        if alive(pid):
            from jarvis.ui.desktop_app import _terminate_pid

            killer = terminate or _terminate_pid
            logger.warning(
                "launcher: background service pid={} did not hand back in {:.0f}s — stopping it",
                pid,
                wait_s,
            )
            killer(pid)
        settle = now() + 5.0
        while now() < settle:
            try:
                return acquire()
            except busy_error:  # the OS is still releasing the lock: poll again
                sleep(0.25)
        logger.warning("launcher: the lock stayed held after the background service stopped")
        return None
    finally:
        clear_handover_request()


__all__ = [
    "AFTER_PID_FLAG",
    "SERVICE_FLAG",
    "BackgroundWork",
    "clear_handover_request",
    "clear_marker",
    "hand_off_on_quit",
    "handover_requested",
    "keep_running_enabled",
    "log_path",
    "read_marker",
    "request_handover",
    "service_command",
    "service_pid",
    "spawn_service",
    "take_over_from_service",
    "work_from_state",
    "write_marker",
]
