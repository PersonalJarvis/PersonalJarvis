"""The app's side of the turn host — CLI processes that outlive the app.

:mod:`jarvis.agent_chat.turn_host` explains why a thread's coding CLI runs in a
process of its own. This module is how ``runner_cli`` talks to it:

* :func:`spawn` starts a CLI in the host and hands back a :class:`HostedCli`,
  which answers the same calls ``runner_cli`` makes on an
  ``asyncio.subprocess.Process`` (``stdout.readline``, ``stderr.readline``,
  ``stdin.write``/``drain``/``close``, ``wait``, ``kill``, ``returncode``,
  ``pid``) — so the stream translation, approvals and outcome are the exact
  code a child process gets.
* Reading the next stdout line acknowledges the previous one: by then the
  runner has emitted everything it made of it. After an app restart
  :func:`attach` replays every line again, and ``stdout.replaying`` says which
  ones were already handled — the runner rebuilds its translator state from
  them without emitting a second copy.
* :meth:`HostedCli.detach` lets go of a CLI without ending it (app shutdown).
  ``kill`` ends it (Stop).

Nothing here raises when the host is missing: :func:`spawn` returns ``None``
and the caller starts the CLI as its own child, as before. A turn that dies
with the app is worse than one that survives it, and far better than none.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .turn_host import MAX_FRAME_BYTES, PROTOCOL_VERSION, TOKEN_ENV

log = logging.getLogger(__name__)

#: The host's ``python -m`` module — also how a live host is recognised.
MODULE = "jarvis.agent_chat.turn_host"

REQUEST_TIMEOUT_S = 30.0
START_TIMEOUT_S = 15.0
#: How long a host that is alive but silent gets before this start gives up
#: on it (its turns are then collected on a later attempt).
LIVE_HOST_PATIENCE_S = 10.0

#: The exit code a turn reports when the host itself went away under it.
HOST_LOST_CODE = -1


class TurnHandedOver(RuntimeError):
    """Another app process attached to the turn host and carries this turn on.

    The runner raises it instead of judging an outcome: the turn is neither
    done nor failed here, and must stay open for the process that took over.
    """


# --------------------------------------------------------------- locations
def _suffix() -> str:
    from jarvis.core.instance import current_instance

    return current_instance().state_file_suffix


def _state_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agent_chat" / f"turn_host{_suffix()}.json"


def spool_dir() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agent_chat" / f"turn_host{_suffix()}_spool"


def _log_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "logs" / f"turn_host{_suffix()}.log"


def host_available() -> bool:
    """Does this process keep thread CLIs in the turn host?

    Only a real app process does: the desktop shell and the web launcher turn
    on ``agentic_ide.host_mode`` (the same switch that keeps the terminal
    grid's agents in the PTY host). A test, a script or the CLI building an
    agent-chat service never starts or attaches to the user's host. A frozen
    build has no ``python -m`` to start one with.
    """
    from jarvis.agentic_ide import host_mode

    return host_mode.enabled() and not getattr(sys, "frozen", False)


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # no readable state file means no host
        return None
    return data if isinstance(data, dict) else None


def _host_alive(state: dict[str, Any] | None) -> bool:
    from jarvis.terminal.pty_host_client import host_is_alive

    return host_is_alive(state, module=MODULE)


def may_hold_turns() -> bool:
    """Is there anything to collect: a live host, or a turn it spooled to disk?

    Synchronous and cheap (a file read and a process lookup), so a service can
    ask it while it decides which open turns a restart really orphaned.
    """
    if not host_available():
        return False
    try:
        if any(spool_dir().glob("*.json")):
            return True
    except OSError as exc:
        log.debug("turn host: spool unreadable: %s", exc)
    return _host_alive(_read_state(_state_path()))


# ---------------------------------------------------------- the process API
class _LineStream:
    """``readline`` over lines the host sends, numbered from 1."""

    def __init__(self, before_read: Callable[[], None] | None = None) -> None:
        self._queue: asyncio.Queue[tuple[int, bytes] | None] = asyncio.Queue()
        self._before_read = before_read
        self._eof = False
        #: Lines up to this ``seq`` were handled before an app restart.
        self.replay_upto = 0
        #: Whether the line ``readline`` returned last is such a replay.
        self.replaying = False
        self.last_seq = 0

    def feed(self, seq: int, text: str) -> None:
        if not self._eof:
            self._queue.put_nowait((seq, (text + "\n").encode("utf-8")))

    def feed_eof(self) -> None:
        if not self._eof:
            self._eof = True
            self._queue.put_nowait(None)

    async def readline(self) -> bytes:
        if self._before_read is not None:
            self._before_read()
        item = await self._queue.get()
        if item is None:
            self._queue.put_nowait(None)  # every later read sees the end too
            self.replaying = False
            return b""
        seq, data = item
        self.last_seq = seq
        self.replaying = 0 < seq <= self.replay_upto
        return data


class _HostStdin:
    """The CLI's stdin, written through the host."""

    def __init__(self, cli: HostedCli) -> None:
        self._cli = cli
        self._closed = False

    def write(self, data: bytes) -> None:
        if not self._closed:
            self._cli._send({"op": "write", "d": data.decode("utf-8", errors="replace")})

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def is_closing(self) -> bool:
        return self._closed or self._cli.returncode is not None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._cli._send({"op": "close_stdin"})


class HostedCli:
    """One CLI in the turn host, shaped like ``asyncio.subprocess.Process``."""

    hosted = True

    def __init__(
        self,
        client: TurnHostClient | None,
        host_id: str,
        pid: int,
        meta: dict[str, Any],
        *,
        has_stdin: bool,
        spool_path: Path | None = None,
    ) -> None:
        self._client = client
        self.host_id = host_id
        self.pid = pid
        self.meta = meta
        self.stdout = _LineStream(self._ack_previous)
        self.stderr = _LineStream()
        self.stdin: _HostStdin | None = _HostStdin(self) if has_stdin else None
        self._exit: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._fed = 0
        self._acked = 0
        self.detached = False
        self.host_lost = False
        #: Another app process took the host over and carries this turn on.
        self.handed_over = False
        self._spool_path = spool_path

    # ------------------------------------------------------- process calls
    @property
    def returncode(self) -> int | None:
        return self._exit.result() if self._exit.done() else None

    async def wait(self) -> int:
        return await asyncio.shield(self._exit)

    def kill(self) -> None:
        if self.returncode is None:
            self._send({"op": "kill"})

    def detach(self) -> None:
        """Stop reading without ending the CLI: it keeps running in the host."""
        if self.detached or self.returncode is not None:
            return
        self.detached = True
        if self._client is not None:
            self._client._forget(self.host_id)
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    def release(self) -> None:
        """The turn is over and handled: the host (or the spool) may drop it."""
        if self._spool_path is not None:
            with contextlib.suppress(OSError):
                self._spool_path.unlink()
            return
        if self._client is not None:
            self._send({"op": "release"})
            self._client._forget(self.host_id)

    # ----------------------------------------------------------- internals
    def _send(self, frame: dict[str, Any]) -> None:
        if self._client is not None and not self.detached:
            self._client._send({**frame, "id": self.host_id})

    def _ack_previous(self) -> None:
        seq = self.stdout.last_seq
        if seq > self._acked:
            self._acked = seq
            self._send({"op": "ack", "seq": seq})

    def _on_line(self, seq: int, text: str) -> None:
        if seq <= self._fed:
            return
        self._fed = seq
        self.stdout.feed(seq, text)

    def _on_err(self, text: str) -> None:
        self.stderr.feed(0, text)

    def _on_exit(self, code: int) -> None:
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        if not self._exit.done():
            self._exit.set_result(int(code))

    def _on_lost(self) -> None:
        self.host_lost = True
        self._on_exit(HOST_LOST_CODE)

    def _on_handed_over(self) -> None:
        self.handed_over = True
        self.detached = True
        self._on_exit(HOST_LOST_CODE)


# ------------------------------------------------------------ the connection
class TurnHostClient:
    """One authenticated connection to the turn host."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        turns: list[dict[str, Any]],
        host_pid: int,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._loop = asyncio.get_running_loop()
        self._procs: dict[str, HostedCli] = {}
        # Events for an id whose spawn reply has not been handled yet.
        self._early: dict[str, list[dict[str, Any]]] = {}
        self._futures: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_rid = 1
        self._closed = False
        self.host_pid = host_pid
        self.turns = {str(t.get("id")): t for t in turns if isinstance(t, dict) and t.get("id")}
        self._reader_task = self._loop.create_task(self._read_loop(), name="turn-host-client")

    @property
    def connected(self) -> bool:
        return not self._closed

    async def spawn(
        self,
        argv: list[str],
        *,
        cwd: str,
        env: dict[str, str] | None,
        stdin: str | None,
        keep_stdin: bool,
        meta: dict[str, Any],
    ) -> HostedCli:
        reply = await self._request(
            {
                "op": "spawn",
                "argv": list(argv),
                "cwd": cwd,
                "env": dict(env) if env is not None else None,
                "stdin": stdin,
                "keep_stdin": keep_stdin,
                "meta": meta,
            }
        )
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error") or "The turn host refused the spawn."))
        cli = HostedCli(
            self, str(reply["id"]), int(reply.get("pid") or 0), meta, has_stdin=stdin is not None
        )
        self._register(cli)
        return cli

    async def attach(self, host_id: str) -> HostedCli | None:
        """Take over a turn that was running (or ended) before this client attached."""
        info = self.turns.get(host_id) or {}
        meta = dict(info.get("meta") or {})
        cli = HostedCli(
            self,
            host_id,
            int(info.get("pid") or 0),
            meta,
            has_stdin=bool(meta.get("keep_stdin")),
        )
        # Registered BEFORE the request: the replayed lines follow the reply
        # immediately and must find their reader.
        self._register(cli)
        reply = await self._request({"op": "attach", "id": host_id})
        if not reply.get("ok") or not reply.get("known"):
            self._forget(host_id)
            return None
        acked = int(reply.get("acked") or 0)
        cli.stdout.replay_upto = acked
        cli._acked = acked
        for line in reply.get("stderr") or ():
            cli._on_err(str(line))
        return cli

    def _register(self, cli: HostedCli) -> None:
        self._procs[cli.host_id] = cli
        for frame in self._early.pop(cli.host_id, []):
            self._deliver(cli, frame)

    def _forget(self, host_id: str) -> None:
        self._procs.pop(host_id, None)
        self.turns.pop(host_id, None)

    def live(self) -> list[HostedCli]:
        return [cli for cli in self._procs.values() if cli.returncode is None]

    def _send(self, frame: dict[str, Any]) -> bool:
        if self._closed:
            return False
        data = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self._writer.write(data)
        except (ConnectionError, OSError, RuntimeError) as exc:
            log.warning("turn host: send failed: %s", exc)
            self._lost(exc)
            return False
        return True

    async def _request(self, frame: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise ConnectionError("The turn host is not connected.")
        rid = self._next_rid
        self._next_rid += 1
        future: asyncio.Future[dict[str, Any]] = self._loop.create_future()
        self._futures[rid] = future
        if not self._send({**frame, "rid": rid}):
            self._futures.pop(rid, None)
            raise ConnectionError("The turn host is not connected.")
        try:
            await self._writer.drain()
            return await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT_S)
        except TimeoutError as exc:
            raise ConnectionError("The turn host did not answer in time.") from exc
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
                    log.warning("turn host: unreadable frame dropped: %s", exc)
                    continue
                if isinstance(frame, dict):
                    self._dispatch(frame)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError, ValueError) as exc:
            # ANY read error ends the loop (AP-20).
            error = exc
        self._lost(error)

    def _dispatch(self, frame: dict[str, Any]) -> None:
        rid = frame.get("rid")
        if rid is not None:
            future = self._futures.get(int(rid))
            if future is not None and not future.done():
                future.set_result(frame)
            return
        if frame.get("ev") == "replaced":
            self._handed_over()
            return
        host_id = str(frame.get("id", ""))
        cli = self._procs.get(host_id)
        if cli is None:
            early = self._early.setdefault(host_id, [])
            if len(early) < 100_000:
                early.append(frame)
            return
        self._deliver(cli, frame)

    @staticmethod
    def _deliver(cli: HostedCli, frame: dict[str, Any]) -> None:
        event = frame.get("ev")
        if event == "line":
            cli._on_line(int(frame.get("seq") or 0), str(frame.get("d", "")))
        elif event == "err":
            cli._on_err(str(frame.get("d", "")))
        elif event == "exit":
            code = frame.get("code")
            cli._on_exit(int(code) if code is not None else HOST_LOST_CODE)

    def _lost(self, error: BaseException | None) -> None:
        was_open = not self._closed
        self._closed = True
        for future in self._futures.values():
            if not future.done():
                future.set_exception(ConnectionError("The turn host went away."))
        self._futures.clear()
        if was_open:
            log.warning(
                "turn host connection lost (%s) — %d turn(s) reported as ended",
                error or "end of stream",
                len(self._procs),
            )
            for cli in list(self._procs.values()):
                cli._on_lost()
        self._procs.clear()
        self._early.clear()

    def _handed_over(self) -> None:
        log.info(
            "turn host: another app process took over — handing %d turn(s) to it",
            len(self._procs),
        )
        procs = list(self._procs.values())
        self._procs.clear()
        self._closed = True
        for future in self._futures.values():
            if not future.done():
                future.set_exception(ConnectionError("Another app took the turn host over."))
        self._futures.clear()
        for cli in procs:
            cli._on_handed_over()

    def detach(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self._writer.close()
        self._reader_task.cancel()


# --------------------------------------------------------------- discovery
_client: TurnHostClient | None = None
_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None


def _connect_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


async def _open(*, start: bool) -> TurnHostClient | None:
    from jarvis.terminal.pty_host_client import _handshake, _start_host

    state_path = _state_path()
    state = await asyncio.to_thread(_read_state, state_path)
    alive = await asyncio.to_thread(_host_alive, state)
    if alive and state is not None:
        if state.get("proto") != PROTOCOL_VERSION:
            # A host from another build still holding its turns: never start a
            # second one beside it. Turns run in-process until it exits.
            log.info("turn host: a host speaking protocol %s is running", state.get("proto"))
            return None
        deadline = time.monotonic() + LIVE_HOST_PATIENCE_S
        delay = 0.25
        while True:
            found = await _handshake(
                int(state["port"]),
                str(state["token"]),
                3.0,
                proto=PROTOCOL_VERSION,
                limit=MAX_FRAME_BYTES,
            )
            if found is not None:
                return _client_from(*found)
            if time.monotonic() >= deadline or not await asyncio.to_thread(_host_alive, state):
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 2.0)
        if await asyncio.to_thread(_host_alive, state):
            log.warning("turn host (pid %s) is running but not answering", state.get("pid"))
            return None
    if not start:
        return None
    token = secrets.token_urlsafe(32)
    started = await asyncio.to_thread(
        _start_host,
        state_path,
        token,
        module=MODULE,
        token_env=TOKEN_ENV,
        log_path=_log_path(),
        extra_args=("--spool", str(spool_dir())),
    )
    if not started:
        return None
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        state = await asyncio.to_thread(_read_state, state_path)
        if not state or state.get("token") != token or not state.get("port"):
            continue
        found = await _handshake(
            int(state["port"]), token, 3.0, proto=PROTOCOL_VERSION, limit=MAX_FRAME_BYTES
        )
        if found is not None:
            return _client_from(*found)
    log.warning("turn host did not come up within %.0f s", START_TIMEOUT_S)
    return None


def _client_from(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, reply: dict[str, Any]
) -> TurnHostClient:
    turns = [t for t in reply.get("turns") or () if isinstance(t, dict)]
    client = TurnHostClient(reader, writer, turns, int(reply.get("pid") or 0))
    log.info("turn host attached (pid %s) — %d turn(s) held", client.host_pid, len(turns))
    return client


async def get_client(*, start: bool = True) -> TurnHostClient | None:
    """The connected client, attaching to (or starting) the host on first use."""
    global _client
    if not host_available():
        return None
    if _client is not None and _client.connected:
        return _client
    async with _connect_lock():
        if _client is not None and _client.connected:
            return _client
        try:
            _client = await _open(start=start)
        except Exception:  # noqa: BLE001 - no host means the CLI runs in-process
            log.warning("turn host: could not attach", exc_info=True)
            _client = None
        return _client


async def spawn(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None,
    stdin: str | None,
    keep_stdin: bool,
    meta: dict[str, Any],
) -> HostedCli | None:
    """Start ``argv`` in the turn host, or ``None`` when no host can take it."""
    client = await get_client(start=True)
    if client is None:
        return None
    try:
        return await client.spawn(
            argv, cwd=cwd, env=env, stdin=stdin, keep_stdin=keep_stdin, meta=meta
        )
    except (ConnectionError, RuntimeError, OSError) as exc:
        log.warning("turn host: spawn refused (%s) — the CLI runs in-process", exc)
        return None


def live_turn_ids() -> set[str]:
    """Chat turn ids whose CLI runs in the host and is still being read."""
    client = _client
    if client is None or not client.connected:
        return set()
    return {str(cli.meta.get("turn_id") or "") for cli in client.live()} - {""}


def detach_all() -> None:
    """App shutdown: let go of every hosted CLI and the connection. Nothing ends."""
    global _client
    client = _client
    if client is None:
        return
    for cli in client.live():
        cli.detach()
    client.detach()
    _client = None


def read_spool() -> list[dict[str, Any]]:
    """Turns the host wrote to disk because they ended while no app was attached."""
    records: list[dict[str, Any]] = []
    try:
        paths = sorted(spool_dir().glob("*.json"))
    except OSError:
        return records
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("turn host: spooled turn %s unreadable: %s", path.name, exc)
            continue
        if isinstance(record, dict):
            record["spool_path"] = str(path)
            records.append(record)
    return records


def spooled_cli(record: dict[str, Any]) -> HostedCli:
    """A spooled turn, read back as an ended process with all its lines."""
    cli = HostedCli(
        None,
        str(record.get("id") or ""),
        int(record.get("pid") or 0),
        dict(record.get("meta") or {}),
        has_stdin=False,
        spool_path=Path(str(record["spool_path"])),
    )
    acked = int(record.get("acked") or 0)
    cli.stdout.replay_upto = acked
    cli._acked = acked
    for seq, line in enumerate(record.get("stdout") or (), start=1):
        cli._on_line(seq, str(line))
    for line in record.get("stderr") or ():
        cli._on_err(str(line))
    code = record.get("code")
    cli._on_exit(int(code) if code is not None else HOST_LOST_CODE)
    return cli


__all__ = [
    "HOST_LOST_CODE",
    "HostedCli",
    "TurnHandedOver",
    "TurnHostClient",
    "detach_all",
    "get_client",
    "host_available",
    "live_turn_ids",
    "may_hold_turns",
    "read_spool",
    "spawn",
    "spool_dir",
    "spooled_cli",
]
