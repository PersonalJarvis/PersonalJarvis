"""The app's side of the PTY host — a drop-in for :class:`PtyManager`.

:mod:`jarvis.terminal.pty_host` explains why the Agentic IDE's terminals live
in a process of their own. This module is how the app talks to it:
:class:`RemotePtyManager` answers the same calls ``PtyManager`` does (``spawn``,
``write``, ``resize``, ``close``, ``has``), so the registry that drives the
panes does not care which of the two it holds — plus the two things only a
remote pool can do: list the terminals that were already running when the app
started (:meth:`RemotePtyManager.hosted`) and take one of them over
(:meth:`RemotePtyManager.adopt`).

**Quitting the app detaches, it does not kill.** ``close_all`` on this class
only lets go of the connection; the terminals keep running in the host and the
next app start adopts them. Ending an agent is ``close(id)`` — what closing a
pane or a workspace does.

Output is coalesced exactly the way ``PtyManager`` coalesces it (one pump per
terminal, everything that arrived while a send was in flight leaves as one
chunk), for the same reason: a busy pane must cost frame COUNT, never queue
depth. The difference is only where the bytes come from — a socket instead of
a reader thread.

:func:`connect` finds the running host or starts one. It never raises: a host
that cannot be reached or started returns ``None``, and the caller keeps the
in-process ``PtyManager`` — terminals that die with the app are worse than
terminals that survive it, and far better than no terminals.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import sys
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from .pty_host import MAX_FRAME_BYTES, PROTOCOL_VERSION, TOKEN_ENV
from .pty_manager import MAX_PENDING_CHARS, UNKNOWN_EXIT_CODE

OutputCallback = Callable[[str, str], Awaitable[None]]
ClosedCallback = Callable[[str, int], Awaitable[None]]

#: How long a request may wait for the host's answer. A spawn is the slowest
#: one (the host starts a process and a console), and it is still well under.
REQUEST_TIMEOUT_S = 30.0

#: How long a freshly started host gets to publish its port.
START_TIMEOUT_S = 15.0


@dataclass(slots=True)
class HostedInfo:
    """A terminal the host was already running when this client connected."""

    terminal_id: str
    pid: int
    meta: dict[str, Any]
    cols: int
    rows: int
    started_at: float


@dataclass(slots=True)
class AdoptResult:
    """What adopting a hosted terminal handed back."""

    alive: bool
    replay: str = ""
    truncated: bool = False
    cols: int = 0
    rows: int = 0
    exit_code: int | None = None


@dataclass(slots=True)
class RemoteSession:
    """The client-side half of one hosted terminal: a pump, like ``PtySession``."""

    terminal_id: str
    pid: int
    on_output: OutputCallback
    on_closed: ClosedCallback
    _pending: deque[str] = field(default_factory=deque, init=False)
    _pending_chars: int = field(default=0, init=False)
    _wake: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _finished: bool = field(default=False, init=False)
    _exit_code: int | None = field(default=None, init=False)
    _pump: asyncio.Task[None] | None = field(default=None, init=False)

    def offer(self, text: str) -> None:
        if not text or self._finished:
            return
        self._pending.append(text)
        self._pending_chars += len(text)
        # Oldest first, for the reason PtyManager gives: a TUI's newest bytes
        # ARE its screen.
        while self._pending_chars > MAX_PENDING_CHARS and len(self._pending) > 1:
            self._pending_chars -= len(self._pending.popleft())
        self._wake.set()

    def finish(self, code: int) -> None:
        if self._finished:
            return
        self._finished = True
        self._exit_code = code
        self._wake.set()

    def take(self) -> str:
        if not self._pending:
            return ""
        text = "".join(self._pending)
        self._pending.clear()
        self._pending_chars = 0
        return text

    async def run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                text = self.take()
                if not text:
                    break
                try:
                    await self.on_output(self.terminal_id, text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the terminal outlives its viewer
                    logger.debug(
                        "PTY host output callback failed: terminal={} error={}",
                        self.terminal_id,
                        exc,
                    )
            if self._finished:
                break
        code = UNKNOWN_EXIT_CODE if self._exit_code is None else self._exit_code
        try:
            await self.on_closed(self.terminal_id, code)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the viewer may be gone
            logger.debug(
                "PTY host close callback failed: terminal={} error={}", self.terminal_id, exc
            )


class HostConnectionError(RuntimeError):
    """The host could not be reached, or dropped the connection."""


class HostUnreachable(RuntimeError):
    """A PTY host IS running for this user and boot, but did not answer.

    Raised instead of returning ``None`` because the two mean opposite things
    to the caller. ``None`` says "no host holds any agent": starting agents is
    safe. This says "a host holds agents right now": starting one would run a
    second copy of a live agent on the same conversation — the 2026-09-28
    failure where panes came back doubled and a stray "continue" was typed
    into agents that had never stopped. The caller waits and tries again.
    """


class RemotePtyManager:
    """A ``PtyManager`` whose terminals live in the PTY host process."""

    #: Lets the registry tell the two pools apart without an isinstance check
    #: against a class it would otherwise have to import eagerly.
    persistent = True

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        hosted: list[HostedInfo],
        host_pid: int,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        self._sessions: dict[str, RemoteSession] = {}
        self._futures: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_rid = 1
        self._hosted = {info.terminal_id: info for info in hosted}
        self._closed = False
        self.host_pid = host_pid
        self._reader_task = self._loop.create_task(self._read_loop(), name="pty-host-client")

    # ---------------------------------------------------------- properties
    @property
    def connected(self) -> bool:
        return not self._closed

    def hosted(self) -> list[HostedInfo]:
        """Terminals that were running before this client attached, not yet adopted."""
        return list(self._hosted.values())

    # ----------------------------------------------------- PtyManager API
    async def spawn(
        self,
        shell_argv: tuple[str, ...],
        shell_id: str,
        cwd: str | None,
        cols: int,
        rows: int,
        on_output: OutputCallback,
        on_closed: ClosedCallback,
        env: Mapping[str, str] | None = None,
        on_probe: Callable[[str], str] | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> RemoteSession:
        """Start a terminal in the host. Same contract as ``PtyManager.spawn``.

        ``on_probe`` cannot cross a process boundary — the host answers the
        emulator queries itself, from the appearance handed over here (read
        off the responder ``on_probe`` is bound to, which is what the registry
        passes) and kept current by :meth:`set_appearance`.

        ``meta`` travels with the terminal and comes back from
        :meth:`hosted` after an app restart; it is how the registry recognises
        which pane a still-running terminal belongs to.
        """
        responder = getattr(on_probe, "__self__", None)
        appearance = getattr(responder, "appearance", None)
        reply = await self._request(
            {
                "op": "spawn",
                "argv": list(shell_argv),
                "shell_id": shell_id,
                "cwd": cwd,
                "cols": cols,
                "rows": rows,
                "env": dict(env) if env is not None else None,
                "appearance": appearance if isinstance(appearance, str) else None,
                "meta": dict(meta or {}),
            }
        )
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error") or "The terminal host refused the spawn."))
        session = self._register(str(reply["id"]), int(reply.get("pid") or 0), on_output, on_closed)
        logger.info(
            "PTY spawned in host: shell={} terminal={} pid={} size={}x{}",
            shell_id,
            session.terminal_id,
            session.pid,
            cols,
            rows,
        )
        return session

    async def adopt(
        self, terminal_id: str, on_output: OutputCallback, on_closed: ClosedCallback
    ) -> AdoptResult:
        """Take over a terminal that was running before this client attached.

        The host hands back the tail of its output (the screen, for a
        viewer to rebuild) and streams everything after it — the two in one
        step on the host, so no byte is lost or doubled in between.
        """
        info = self._hosted.pop(terminal_id, None)
        reply = await self._request({"op": "adopt", "id": terminal_id})
        if not reply.get("ok") or not reply.get("alive"):
            code = reply.get("code")
            return AdoptResult(alive=False, exit_code=int(code) if code is not None else None)
        pid = info.pid if info is not None else 0
        self._register(terminal_id, pid, on_output, on_closed)
        return AdoptResult(
            alive=True,
            replay=str(reply.get("replay") or ""),
            truncated=bool(reply.get("truncated")),
            cols=int(reply.get("cols") or 0),
            rows=int(reply.get("rows") or 0),
        )

    def write(self, terminal_id: str, data: str) -> bool:
        if terminal_id not in self._sessions or self._closed:
            return False
        return self._send({"op": "write", "id": terminal_id, "d": data})

    def resize(self, terminal_id: str, cols: int, rows: int) -> bool:
        if terminal_id not in self._sessions or self._closed:
            return False
        return self._send({"op": "resize", "id": terminal_id, "cols": cols, "rows": rows})

    def set_appearance(self, terminal_id: str, appearance: str) -> None:
        """Keep the host's emulator-query answers on the viewer's light/dark ground."""
        if terminal_id in self._sessions:
            self._send({"op": "appearance", "id": terminal_id, "appearance": appearance})

    def swap_sessions(self, id_a: str, id_b: str) -> bool:
        """Not offered by the host; refused, leaving both routes untouched."""
        return id_a == id_b

    def close(self, terminal_id: str) -> bool:
        """End a terminal for good. Its exit still arrives through ``on_closed``."""
        known = terminal_id in self._sessions or terminal_id in self._hosted
        self._hosted.pop(terminal_id, None)
        if not known or self._closed:
            return False
        return self._send({"op": "close", "id": terminal_id})

    def kill_hosted(self, terminal_id: str) -> bool:
        """End a still-unadopted hosted terminal nobody is going to claim."""
        return self.close(terminal_id)

    def close_all(self) -> None:
        """Detach from the host. The terminals keep running (module docstring)."""
        self.detach()

    def has(self, terminal_id: str) -> bool:
        return terminal_id in self._sessions and not self._closed

    def detach(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self._writer.close()
        self._reader_task.cancel()
        logger.info(
            "PTY host: detached — {} terminal(s) keep running in the host", len(self._sessions)
        )

    # ------------------------------------------------------------ internals
    def _register(
        self, terminal_id: str, pid: int, on_output: OutputCallback, on_closed: ClosedCallback
    ) -> RemoteSession:
        session = RemoteSession(
            terminal_id=terminal_id, pid=pid, on_output=on_output, on_closed=on_closed
        )
        self._sessions[terminal_id] = session
        session._pump = self._loop.create_task(
            self._pump(session), name=f"pty-host-pump-{terminal_id[:8]}"
        )
        return session

    async def _pump(self, session: RemoteSession) -> None:
        try:
            await session.run()
        finally:
            if self._sessions.get(session.terminal_id) is session:
                del self._sessions[session.terminal_id]

    def _send(self, frame: dict[str, Any]) -> bool:
        """Queue one frame. Safe from any thread; never blocks."""
        if self._closed:
            return False
        data = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            if threading.get_ident() == self._loop_thread:
                self._writer.write(data)
            else:
                self._loop.call_soon_threadsafe(self._writer.write, data)
        except (ConnectionError, OSError, RuntimeError) as exc:
            logger.warning("PTY host: send failed: {}", exc)
            self._lost(exc)
            return False
        return True

    async def _request(self, frame: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise HostConnectionError("The terminal host is not connected.")
        rid = self._next_rid
        self._next_rid += 1
        future: asyncio.Future[dict[str, Any]] = self._loop.create_future()
        self._futures[rid] = future
        frame = {**frame, "rid": rid}
        if not self._send(frame):
            self._futures.pop(rid, None)
            raise HostConnectionError("The terminal host is not connected.")
        try:
            await self._writer.drain()
            return await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT_S)
        except TimeoutError as exc:
            raise HostConnectionError("The terminal host did not answer in time.") from exc
        finally:
            self._futures.pop(rid, None)

    async def _read_loop(self) -> None:
        error: BaseException | None = None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    frame = json.loads(line.decode("utf-8"))
                except ValueError as exc:
                    logger.warning("PTY host: unreadable frame dropped: {}", exc)
                    continue
                if not isinstance(frame, dict):
                    continue
                self._dispatch(frame)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError, ValueError) as exc:
            # ANY read error ends the loop (AP-20): a socket that failed once is
            # not going to produce a valid frame on the next read either.
            error = exc
        self._lost(error)

    def _dispatch(self, frame: dict[str, Any]) -> None:
        rid = frame.get("rid")
        if rid is not None:
            future = self._futures.get(int(rid))
            if future is not None and not future.done():
                future.set_result(frame)
            return
        event = frame.get("ev")
        session = self._sessions.get(str(frame.get("id", "")))
        if session is None:
            return
        if event == "o":
            session.offer(str(frame.get("d", "")))
        elif event == "exit":
            code = frame.get("code")
            session.finish(int(code) if code is not None else UNKNOWN_EXIT_CODE)

    def _lost(self, error: BaseException | None) -> None:
        """The host is gone: every terminal it held is gone with it."""
        was_open = not self._closed
        self._closed = True
        for future in self._futures.values():
            if not future.done():
                future.set_exception(HostConnectionError("The terminal host went away."))
        self._futures.clear()
        if was_open:
            logger.warning(
                "PTY host connection lost ({}) — {} terminal(s) reported as ended",
                error or "end of stream",
                len(self._sessions),
            )
            for session in list(self._sessions.values()):
                session.finish(UNKNOWN_EXIT_CODE)
        self._hosted.clear()


# --------------------------------------------------------------- discovery
def _state_path() -> Path:
    from jarvis.core.instance import current_instance
    from jarvis.core.paths import user_data_dir

    suffix = current_instance().state_file_suffix
    return user_data_dir() / "agentic_ide" / f"pty_host{suffix}.json"


def _log_path() -> Path:
    from jarvis.core.instance import current_instance
    from jarvis.core.paths import user_data_dir

    suffix = current_instance().state_file_suffix
    return user_data_dir() / "logs" / f"pty_host{suffix}.log"


#: How long ``connect`` keeps trying a host that is alive but not answering
#: (a busy machine right after login, a host mid-way through a large replay).
LIVE_HOST_PATIENCE_S = 20.0


def _current_boot_time() -> float:
    try:
        import psutil

        return float(psutil.boot_time())
    except Exception as exc:  # noqa: BLE001 - optional evidence
        logger.debug("PTY host: boot time unavailable: {}", exc)
        return 0.0


def host_is_alive(state: dict[str, Any] | None) -> bool:
    """Is the host this state file describes still running, in THIS boot?

    Three facts, all checked: the process exists, it is a PTY host (a pid is
    reused after it dies), and it started after the machine last booted. A
    state file from before a reboot therefore reads as dead however its pid
    happens to be reused now — which is what makes "the machine restarted"
    a measurement instead of a guess.
    """
    if not state or not state.get("pid"):
        return False
    try:
        import psutil

        proc = psutil.Process(int(state["pid"]))
        cmdline = " ".join(proc.cmdline())
        created = float(proc.create_time())
    except Exception as exc:  # noqa: BLE001 - gone, not ours, or unreadable: not alive
        logger.debug("PTY host pid {} is not a live host: {}", state.get("pid"), exc)
        return False
    if "jarvis.terminal.pty_host" not in cmdline:
        return False
    boot = _current_boot_time()
    recorded = float(state.get("boot_time") or 0.0)
    if boot and recorded and abs(recorded - boot) > 5.0:
        return False
    return not boot or created >= boot - 5.0


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # no readable state file means no host to attach to
        return None
    return data if isinstance(data, dict) else None


async def _handshake(
    port: int, token: str, wait_s: float
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, dict[str, Any]] | None:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port, limit=MAX_FRAME_BYTES), timeout=wait_s
        )
    except (OSError, TimeoutError) as exc:
        logger.debug("PTY host: no answer on port {}: {}", port, exc)
        return None
    try:
        hello = {"op": "hello", "token": token, "proto": PROTOCOL_VERSION, "rid": 0}
        writer.write((json.dumps(hello) + "\n").encode("utf-8"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=wait_s)
        reply = json.loads(line.decode("utf-8")) if line else None
    except (OSError, TimeoutError, ValueError) as exc:
        logger.info("PTY host: handshake on port {} failed: {}", port, exc)
        writer.close()
        return None
    if not isinstance(reply, dict) or not reply.get("ok"):
        logger.warning("PTY host: handshake refused on port {}: {}", port, reply)
        writer.close()
        return None
    return reader, writer, reply


def _manager_from(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, reply: dict[str, Any]
) -> RemotePtyManager:
    hosted = [
        HostedInfo(
            terminal_id=str(item.get("id", "")),
            pid=int(item.get("pid") or 0),
            meta=dict(item.get("meta") or {}),
            cols=int(item.get("cols") or 0),
            rows=int(item.get("rows") or 0),
            started_at=float(item.get("started_at") or 0.0),
        )
        for item in reply.get("terminals") or ()
        if isinstance(item, dict) and item.get("id")
    ]
    return RemotePtyManager(reader, writer, hosted, int(reply.get("pid") or 0))


def host_available() -> bool:
    """Can this install run a PTY host at all?

    A frozen build has no ``python -m`` to start one with, and a host without a
    PTY backend would only answer every spawn with the same error the in-process
    pool gives.
    """
    if getattr(sys, "frozen", False):
        return False
    from jarvis.platform.capabilities import detect_capabilities

    return bool(detect_capabilities().has_pty)


def _start_host(state_path: Path, token: str) -> bool:
    import jarvis
    from jarvis.ui.relauncher import spawn_detached

    # The host must import THIS jarvis, whichever interpreter path found it.
    package_root = str(Path(jarvis.__file__).resolve().parent.parent)
    env = dict(os.environ)
    env[TOKEN_ENV] = token
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (package_root, env.get("PYTHONPATH", "")) if part
    )
    env["PYTHONIOENCODING"] = "utf-8"
    argv = [
        sys.executable,
        "-m",
        "jarvis.terminal.pty_host",
        "--state",
        str(state_path),
        "--log",
        str(_log_path()),
    ]
    if sys.platform == "win32":
        return _start_host_windows(argv, package_root, env, token, state_path)
    try:
        spawn_detached(argv, cwd=package_root, env=env)
    except OSError as exc:
        logger.warning("PTY host could not be started: {}", exc)
        return False
    return True


def _start_host_windows(
    argv: list[str], cwd: str, env: dict[str, str], token: str, state_path: Path
) -> bool:
    """Start the host so that NO job object of the app can take it down.

    Windows kills every member of a job created with KILL_ON_JOB_CLOSE when
    the job's last handle closes — and the app itself may sit in one (started
    from a terminal, an IDE or an agent shell that uses jobs). A child only
    leaves such a job with ``CREATE_BREAKAWAY_FROM_JOB``, and only when the job
    allows it. ``relauncher.spawn_detached`` falls back to starting INSIDE the
    job when breakaway is refused, which for the host means dying with the app
    it exists to outlive. So a refused breakaway goes through WMI instead:
    ``Win32_Process.Create`` starts the process under the WMI service, outside
    every job of ours (the approach cmux and others document). WMI cannot pass
    an environment, so the token travels in a user-only file the host deletes.
    """
    import subprocess

    flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    )
    try:
        subprocess.Popen(  # noqa: S603 - our own interpreter, fixed arguments
            argv,
            cwd=cwd,
            env=env,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except PermissionError:
        logger.info("PTY host: the app's job refuses breakaway — starting it through WMI")
    except OSError as exc:
        logger.warning("PTY host could not be started: {}", exc)
        return False
    return _start_host_via_wmi(argv, cwd, token, state_path)


def _start_host_via_wmi(argv: list[str], cwd: str, token: str, state_path: Path) -> bool:
    import subprocess

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    token_file = state_path.with_name(state_path.stem + f".{os.getpid()}.token")
    try:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token, encoding="utf-8")
    except OSError as exc:
        logger.warning("PTY host token file could not be written: {}", exc)
        return False
    # python.exe, not pythonw: ConPTY needs a console of its own to attach the
    # pseudo-console to (a pythonw host died on its first spawn, measured
    # 2026-09-28), even though the app itself may run under pythonw. Its window
    # is hidden through ``ShowWindow=0`` below.
    exe = Path(argv[0])
    console = exe.with_name("python.exe")
    command = [str(console if console.exists() else exe), *argv[1:]]
    command += ["--token-file", str(token_file)]
    line = subprocess.list2cmdline(command).replace("'", "''")
    folder = cwd.replace("'", "''")
    script = (
        "$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly "
        "-Property @{ShowWindow=[uint16]0}; "
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
        f"@{{CommandLine='{line}'; CurrentDirectory='{folder}'; ProcessStartupInformation=$si}}; "
        "exit [int]$r.ReturnValue"
    )
    try:
        done = subprocess.run(  # noqa: S603 - fixed PowerShell script, our own arguments
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            creationflags=NO_WINDOW_CREATIONFLAGS,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("PTY host could not be started through WMI: {}", exc)
        return False
    if done.returncode != 0:
        logger.warning("PTY host WMI start returned {}", done.returncode)
        return False
    return True


async def connect(*, start: bool = True) -> RemotePtyManager | None:
    """Attach to the running PTY host, starting one only when none is running.

    ``None`` means "no host holds any agent — keep terminals in this process"
    (module docstring). A host that is running but does not answer raises
    :class:`HostUnreachable` after :data:`LIVE_HOST_PATIENCE_S`: it is never
    mistaken for a missing one, and a second host is never started beside it.
    """
    if not host_available():
        return None
    state_path = _state_path()
    state = await asyncio.to_thread(_read_state, state_path)
    alive = await asyncio.to_thread(host_is_alive, state)
    if alive and state is not None and state.get("proto") == PROTOCOL_VERSION:
        deadline = time.monotonic() + LIVE_HOST_PATIENCE_S
        delay = 0.25
        while True:
            found = await _handshake(int(state["port"]), str(state["token"]), wait_s=3.0)
            if found is not None:
                manager = _manager_from(*found)
                logger.info(
                    "PTY host attached (pid {}) — {} terminal(s) still running",
                    manager.host_pid,
                    len(manager.hosted()),
                )
                return manager
            if time.monotonic() >= deadline or not await asyncio.to_thread(host_is_alive, state):
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 2.0)
        if await asyncio.to_thread(host_is_alive, state):
            raise HostUnreachable(
                f"The terminal host (pid {state.get('pid')}) is running but not answering."
            )
        state = None  # it died while we waited: treat it as gone
    if alive and state is not None:
        # Alive, but speaking another protocol: a host from another build that
        # still holds its agents. Starting ours beside it would run a second
        # copy of each of them, so it is reported as unreachable instead; it
        # exits on its own once its agents are gone.
        raise HostUnreachable(
            f"The terminal host (pid {state.get('pid')}) speaks protocol "
            f"{state.get('proto')}, this build speaks {PROTOCOL_VERSION}."
        )
    if not start:
        return None

    token = secrets.token_urlsafe(32)
    if not await asyncio.to_thread(_start_host, state_path, token):
        return None
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        state = await asyncio.to_thread(_read_state, state_path)
        if not state or state.get("token") != token or not state.get("port"):
            continue
        found = await _handshake(int(state["port"]), token, wait_s=3.0)
        if found is not None:
            manager = _manager_from(*found)
            logger.info("PTY host started (pid {})", manager.host_pid)
            return manager
    logger.warning(
        "PTY host did not come up within {:.0f} s — terminals stay in-process", START_TIMEOUT_S
    )
    return None


__all__ = [
    "AdoptResult",
    "HostConnectionError",
    "HostUnreachable",
    "host_is_alive",
    "HostedInfo",
    "RemotePtyManager",
    "RemoteSession",
    "connect",
    "host_available",
]
