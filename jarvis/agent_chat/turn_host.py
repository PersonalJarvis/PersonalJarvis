"""The turn host — a small process that owns the coding CLIs of thread turns.

Run as ``python -m jarvis.agent_chat.turn_host --state <file> --spool <dir>``
(a frozen build: ``<app executable> --turn-host --state ...``, routed by
``jarvis/__main__.py``). Started on demand by
:mod:`jarvis.agent_chat.turn_host_client`, never by hand.

## Why a thread's CLI lives in its own process

A thread in the Agentic IDE (agent-chat surface ``agent``) is answered by a
coding CLI in print mode (``runner_cli``). That CLI used to be a child of the
app: a restart to load a fix, an update or a crash closed its pipes and the
turn ended as "Jarvis restarted while this turn was running" — while the
agent was in the middle of its work.

The terminal grid solved the same problem with the PTY host
(:mod:`jarvis.terminal.pty_host`): a long-lived server owns the processes and
the app is a client that attaches to them. This is the same design for pipe
processes. The host starts the CLI, keeps every line it prints, and streams
them to the attached app. When the app goes away the CLI keeps working and
its lines keep collecting here; the next app start attaches again, replays
what it already handled into a fresh translator (without emitting it twice)
and carries the turn on from the first line it has not seen.

Everything that is policy — prompts, translation, approvals, the event log —
stays in the app. The host knows turn ids, argv, the lines and an opaque
``meta`` dict the client attached at spawn.

## Lifetime

The kill-on-close container (a Job Object on Windows, process groups on
POSIX) belongs to THIS process, so the CLIs live exactly as long as the host
does. With no CLI running and no client attached for :data:`IDLE_EXIT_S` the
host exits. Turns that ended while nobody was attached are written to the
spool folder first (one JSON file each), so the next app start can still
collect their answer.

## Wire protocol

Newline-delimited JSON over one loopback TCP connection, authenticated by a
random token the client handed over in the environment. One client at a time;
a new authenticated client replaces the old one (an app restart).

Requests carry ``rid`` and are answered ``{"rid", "ok", ...}``. Unprompted
events carry ``ev``: ``line`` (one stdout line, numbered by ``seq`` from 1),
``err`` (one stderr line), ``exit``, and ``replaced`` (another client took
over; the old one hands its turns over without ending them). A client acknowledges every stdout
line it has fully handled (``ack``); after a reconnect, ``attach`` answers
with the acknowledged ``seq`` and then replays all lines in order before
streaming live ones, so translator state can be rebuilt exactly.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

#: Bumped only for an incompatible change; a client leaves a host speaking
#: another version alone (it may hold turns from before an update).
PROTOCOL_VERSION = 1

#: How a frozen build re-enters its own executable as the turn host.
FROZEN_FLAG = "--turn-host"

#: Environment variable carrying the auth token. Never on the command line.
TOKEN_ENV = "JARVIS_TURN_HOST_TOKEN"  # noqa: S105 - a variable name, not a secret

#: With no CLI running and no client attached for this long, the host exits.
IDLE_EXIT_S = 60.0

_IDLE_POLL_S = 5.0

#: Largest frame a client may send (a spawn carries the prompt and the env).
MAX_FRAME_BYTES = 64 * 1024 * 1024

#: Longest stdout line read from a CLI (a large tool result is one JSON line).
LINE_LIMIT = 16 * 1024 * 1024

#: Total stdout a turn may keep for replay. Lines past it still stream live
#: but are not kept, so a runaway CLI cannot exhaust memory.
MAX_KEPT_BYTES = 256 * 1024 * 1024

_STDERR_TAIL = 40


@dataclass(slots=True)
class HostedTurn:
    """One CLI process the host owns."""

    turn_id: str
    proc: asyncio.subprocess.Process
    meta: dict[str, Any]
    started_at: float
    lines: list[str] = field(default_factory=list)
    kept_bytes: int = 0
    truncated: bool = False
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=_STDERR_TAIL))
    acked: int = 0
    exit_code: int | None = None
    streaming: bool = False
    pumps: list[asyncio.Task[None]] = field(default_factory=list)
    #: A stdout JSON line of this ``type`` ends the turn: stdin is closed so
    #: the CLI exits, even with no app attached to do it (Claude Code's
    #: ``result``; it otherwise waits on its open stdin for ever).
    close_stdin_on: str = ""

    @property
    def alive(self) -> bool:
        return self.exit_code is None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.turn_id,
            "pid": self.proc.pid,
            "meta": self.meta,
            "started_at": self.started_at,
            "alive": self.alive,
            "code": self.exit_code,
            "lines": len(self.lines),
            "acked": self.acked,
        }

    def spool_record(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "stdout": self.lines,
            "stderr": list(self.stderr_tail),
            "truncated": self.truncated,
        }


@dataclass
class _Client:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def send(self, frame: dict[str, Any]) -> None:
        if self.closed:
            return
        data = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
        async with self.send_lock:
            if self.closed:
                return
            try:
                self.writer.write(data)
                await self.writer.drain()
            except (ConnectionError, OSError, RuntimeError) as exc:
                # The client went away mid-send; its turns keep running and
                # keep collecting lines for the next client.
                logger.info("Turn host: client connection lost while sending: {}", exc)
                self.closed = True

    def close(self) -> None:
        self.closed = True
        try:
            self.writer.close()
        except Exception as exc:  # noqa: BLE001 - already closed is the goal
            logger.debug("Turn host: closing a client socket failed: {}", exc)


class TurnHost:
    """The server: CLI processes, their kept output, and one client."""

    def __init__(
        self, token: str, spool_dir: Path, *, idle_exit_s: float = IDLE_EXIT_S
    ) -> None:
        from jarvis.core.process_tree import make_process_tree

        self._token = token
        self._spool_dir = spool_dir
        self._turns: dict[str, HostedTurn] = {}
        self._client: _Client | None = None
        self._idle_exit_s = idle_exit_s
        self._idle_since: float | None = time.monotonic()
        self._stop = asyncio.Event()
        # One kill-on-close container for every CLI: if the host dies, its
        # CLIs die with it instead of running on with nobody to read them.
        self._tree = make_process_tree("jarvis-turn-host")

    # ------------------------------------------------------------ lifecycle
    async def serve(self, state_path: Path) -> None:
        server = await asyncio.start_server(
            self._on_connect, host="127.0.0.1", port=0, limit=MAX_FRAME_BYTES
        )
        port = int(server.sockets[0].getsockname()[1])
        _write_state(
            state_path,
            {
                "pid": os.getpid(),
                "port": port,
                "token": self._token,
                "proto": PROTOCOL_VERSION,
                "started_at": time.time(),
                "boot_time": _boot_time(),
            },
        )
        logger.info("Turn host listening on 127.0.0.1:{} (pid {})", port, os.getpid())
        idle = asyncio.create_task(self._idle_watch(), name="turn-host-idle")
        try:
            async with server:
                await self._stop.wait()
        finally:
            idle.cancel()
            self._spool_unclaimed()
            _remove_state(state_path, os.getpid())
            for turn in self._turns.values():
                _kill(turn.proc)
            self._tree.close()
            logger.info("Turn host stopped")

    async def _idle_watch(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(_IDLE_POLL_S)
            running = any(turn.alive for turn in self._turns.values())
            attached = self._client is not None and not self._client.closed
            now = time.monotonic()
            if running or attached:
                self._idle_since = None
                continue
            if self._idle_since is None:
                self._idle_since = now
            elif now - self._idle_since >= self._idle_exit_s:
                logger.info("Turn host idle for {:.0f} s — exiting", now - self._idle_since)
                self._stop.set()

    def _spool_unclaimed(self) -> None:
        """Write every ended turn nobody collected to disk for the next app start."""
        for turn in list(self._turns.values()):
            if turn.alive:
                continue
            try:
                self._spool_dir.mkdir(parents=True, exist_ok=True)
                target = self._spool_dir / f"{turn.turn_id}.json"
                tmp = target.with_name(target.name + ".tmp")
                tmp.write_text(json.dumps(turn.spool_record(), ensure_ascii=False), "utf-8")
                os.replace(tmp, target)
                logger.info("Turn host: spooled ended turn {} for the next app start", turn.turn_id)
            except OSError as exc:
                logger.warning("Turn host: could not spool turn {}: {}", turn.turn_id, exc)

    # ----------------------------------------------------------- connection
    async def _on_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        client = _Client(reader, writer)
        try:
            hello = await asyncio.wait_for(_read_frame(reader), timeout=10.0)
        except (TimeoutError, ValueError, ConnectionError, OSError) as exc:
            logger.info("Turn host: connection dropped before hello: {}", exc)
            client.close()
            return
        if (
            not isinstance(hello, dict)
            or hello.get("op") != "hello"
            or not hmac.compare_digest(str(hello.get("token", "")), self._token)
        ):
            logger.warning("Turn host: refused a connection with a bad hello")
            client.close()
            return
        if hello.get("proto") != PROTOCOL_VERSION:
            await client.send(
                {
                    "rid": hello.get("rid"),
                    "ok": False,
                    "error": "protocol",
                    "proto": PROTOCOL_VERSION,
                }
            )
            client.close()
            return
        previous = self._client
        if previous is not None and not previous.closed:
            logger.info("Turn host: a new client replaces the attached one")
            # Told first, so the old app hands its turns over instead of
            # reporting them as failed when its connection drops.
            await previous.send({"ev": "replaced"})
            previous.close()
        for turn in self._turns.values():
            turn.streaming = False
        self._client = client
        await client.send(
            {
                "rid": hello.get("rid"),
                "ok": True,
                "pid": os.getpid(),
                "proto": PROTOCOL_VERSION,
                "turns": [turn.summary() for turn in self._turns.values()],
            }
        )
        try:
            while not client.closed:
                try:
                    frame = await _read_frame(reader)
                except (ValueError, ConnectionError, OSError) as exc:
                    logger.info("Turn host: client disconnected: {}", exc)
                    break
                if frame is None:
                    break
                await self._handle(client, frame)
        finally:
            client.close()
            if self._client is client:
                self._client = None
                for turn in self._turns.values():
                    turn.streaming = False
            logger.info(
                "Turn host: client detached — {} CLI(s) keep running",
                sum(1 for turn in self._turns.values() if turn.alive),
            )

    async def _handle(self, client: _Client, frame: dict[str, Any]) -> None:
        op = frame.get("op")
        rid = frame.get("rid")
        turn = self._turns.get(str(frame.get("id", "")))
        reply: dict[str, Any] | None
        try:
            if op == "spawn":
                reply = await self._spawn(frame)
            elif op == "attach":
                # Answers itself: the reply must precede the replayed lines.
                await self._attach(client, rid, turn)
                return
            elif op == "write":
                if turn is not None:
                    await _write_stdin(turn, str(frame.get("d", "")))
                reply = {}
            elif op == "close_stdin":
                if turn is not None:
                    _close_stdin(turn)
                reply = {}
            elif op == "kill":
                if turn is not None:
                    _kill(turn.proc)
                reply = {}
            elif op == "ack":
                if turn is not None:
                    turn.acked = max(turn.acked, int(frame.get("seq") or 0))
                reply = None
            elif op == "release":
                if turn is not None and not turn.alive:
                    self._turns.pop(turn.turn_id, None)
                reply = {}
            elif op == "list":
                reply = {"turns": [t.summary() for t in self._turns.values()]}
            elif op == "ping":
                reply = {}
            else:
                reply = {"ok": False, "error": f"unknown op {op!r}"}
        except Exception as exc:  # noqa: BLE001 - one bad request must not end the host
            logger.warning("Turn host: {} failed: {}", op, exc)
            reply = {"ok": False, "error": str(exc)}
        if rid is not None and reply is not None:
            reply.setdefault("ok", True)
            reply["rid"] = rid
            await client.send(reply)

    # ------------------------------------------------------------ requests
    async def _spawn(self, frame: dict[str, Any]) -> dict[str, Any]:
        from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

        argv = [str(part) for part in frame.get("argv") or ()]
        if not argv:
            return {"ok": False, "error": "empty argv"}
        env = frame.get("env")
        stdin_data = frame.get("stdin")
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=frame.get("cwd") or None,
            env=dict(env) if isinstance(env, dict) else None,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            # Its own process group on POSIX: the container reaps by group,
            # and the host's own group must never be the one it signals.
            start_new_session=os.name != "nt",
            limit=LINE_LIMIT,
        )
        try:
            self._tree.assign(proc.pid)
        except Exception as exc:  # noqa: BLE001 - containment is best effort, the turn runs
            logger.debug("Turn host: could not contain pid {}: {}", proc.pid, exc)
        turn = HostedTurn(
            turn_id=uuid.uuid4().hex,
            proc=proc,
            meta=dict(frame.get("meta") or {}),
            started_at=time.time(),
            # The spawning client reads from the first line on.
            streaming=True,
            close_stdin_on=str(frame.get("close_stdin_on") or ""),
        )
        self._turns[turn.turn_id] = turn
        if stdin_data is not None:
            await _write_stdin(turn, str(stdin_data))
            if not frame.get("keep_stdin"):
                _close_stdin(turn)
        turn.pumps = [
            asyncio.create_task(self._pump_stdout(turn), name=f"turn-out-{turn.turn_id[:8]}"),
            asyncio.create_task(self._pump_stderr(turn), name=f"turn-err-{turn.turn_id[:8]}"),
        ]
        asyncio.create_task(self._reap(turn), name=f"turn-reap-{turn.turn_id[:8]}")
        logger.info("Turn host: started turn {} (pid {})", turn.turn_id, proc.pid)
        return {"id": turn.turn_id, "pid": proc.pid}

    async def _attach(self, client: _Client, rid: Any, turn: HostedTurn | None) -> None:
        if turn is None:
            await client.send({"rid": rid, "ok": True, "known": False})
            return
        turn.streaming = False
        await client.send(
            {
                "rid": rid,
                "ok": True,
                "known": True,
                "pid": turn.proc.pid,
                "meta": turn.meta,
                "acked": turn.acked,
                "stderr": list(turn.stderr_tail),
                "truncated": turn.truncated,
            }
        )
        # Replay every kept line in order, then switch to live streaming with
        # no await in between: a line the pump appends while a send is in
        # flight is picked up by the next round of this loop, never lost.
        sent = 0
        while sent < len(turn.lines):
            seq = sent + 1
            await client.send(
                {"ev": "line", "id": turn.turn_id, "seq": seq, "d": turn.lines[sent]}
            )
            sent = seq
            if client.closed:
                return
        turn.streaming = True
        if not turn.alive:
            await client.send({"ev": "exit", "id": turn.turn_id, "code": turn.exit_code})

    # --------------------------------------------------------------- pumps
    async def _pump_stdout(self, turn: HostedTurn) -> None:
        stream = turn.proc.stdout
        assert stream is not None
        while True:
            try:
                raw = await stream.readline()
            except (ValueError, asyncio.LimitOverrunError):
                # A line past LINE_LIMIT: drop it, keep reading.
                logger.warning("Turn host: dropped an over-long line of turn {}", turn.turn_id)
                continue
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if turn.kept_bytes + len(raw) > MAX_KEPT_BYTES:
                turn.truncated = True
                continue
            turn.lines.append(text)
            turn.kept_bytes += len(raw)
            if turn.close_stdin_on and _line_type(text) == turn.close_stdin_on:
                _close_stdin(turn)
            client = self._client
            if turn.streaming and client is not None and not client.closed:
                await client.send(
                    {"ev": "line", "id": turn.turn_id, "seq": len(turn.lines), "d": text}
                )

    async def _pump_stderr(self, turn: HostedTurn) -> None:
        stream = turn.proc.stderr
        assert stream is not None
        while True:
            try:
                raw = await stream.readline()
            except (ValueError, asyncio.LimitOverrunError):
                # an over-long stderr line is dropped; readline already cleared it
                continue
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            turn.stderr_tail.append(text)
            client = self._client
            if turn.streaming and client is not None and not client.closed:
                await client.send({"ev": "err", "id": turn.turn_id, "d": text})

    async def _reap(self, turn: HostedTurn) -> None:
        code = await turn.proc.wait()
        # Every line first: the exit must never overtake the answer.
        await asyncio.gather(*turn.pumps, return_exceptions=True)
        turn.exit_code = int(code)
        logger.info("Turn host: turn {} ended (exit {})", turn.turn_id, code)
        client = self._client
        if turn.streaming and client is not None and not client.closed:
            await client.send({"ev": "exit", "id": turn.turn_id, "code": turn.exit_code})


# ---------------------------------------------------------------- helpers
async def _write_stdin(turn: HostedTurn, text: str) -> None:
    stdin = turn.proc.stdin
    if stdin is None or stdin.is_closing():
        return
    try:
        stdin.write(text.encode("utf-8"))
        await stdin.drain()
    except (BrokenPipeError, ConnectionResetError, OSError) as exc:
        logger.debug("Turn host: stdin write for {} failed: {}", turn.turn_id, exc)


def _line_type(text: str) -> str:
    """The ``type`` of a JSON stdout line, or "" for anything else."""
    if '"type"' not in text:
        return ""
    try:
        obj = json.loads(text)
    except ValueError:  # a non-JSON line simply has no type
        return ""
    return str(obj.get("type") or "") if isinstance(obj, dict) else ""


def _close_stdin(turn: HostedTurn) -> None:
    stdin = turn.proc.stdin
    if stdin is None or stdin.is_closing():
        return
    try:
        stdin.close()
    except OSError as exc:
        logger.debug("Turn host: stdin close for {} failed: {}", turn.turn_id, exc)


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except (ProcessLookupError, OSError) as exc:
        logger.debug("Turn host: killing pid {} failed: {}", proc.pid, exc)


async def _read_frame(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    line = await reader.readline()
    if not line:
        return None
    frame = json.loads(line.decode("utf-8"))
    if not isinstance(frame, dict):
        raise ValueError("frame is not an object")
    return frame


def _write_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError as exc:
        # Windows keeps per-user ACLs on the data dir; chmod is a POSIX nicety.
        logger.debug("Turn host: could not restrict the state file: {}", exc)
    os.replace(tmp, path)


def _remove_state(path: Path, pid: int) -> None:
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # gone or rewritten by a newer host: not ours
        return
    if isinstance(current, dict) and current.get("pid") == pid:
        try:
            path.unlink()
        except OSError as exc:
            logger.debug("Turn host: state file not removed: {}", exc)


def _boot_time() -> float:
    try:
        import psutil

        return float(psutil.boot_time())
    except Exception as exc:  # noqa: BLE001 - optional evidence, never fatal
        logger.debug("Turn host: boot time unavailable: {}", exc)
        return 0.0


def _configure_logging(log_path: Path | None) -> None:
    logger.remove()
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(str(log_path), level="INFO", rotation="2 MB", retention=2, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis.agent_chat.turn_host")
    parser.add_argument("--state", required=True, help="Where to publish the port and token.")
    parser.add_argument("--spool", required=True, help="Where ended, uncollected turns go.")
    parser.add_argument("--log", default=None, help="Log file (none when omitted).")
    parser.add_argument("--idle-exit", type=float, default=IDLE_EXIT_S)
    parser.add_argument("--token-file", default=None, help="Read the token from this file.")
    args = parser.parse_args(argv)

    token = os.environ.pop(TOKEN_ENV, "")
    if not token and args.token_file:
        token_path = Path(args.token_file)
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"Turn host: token file unreadable: {exc}", file=sys.stderr)
        finally:
            try:
                token_path.unlink()
            except OSError as exc:  # already gone is fine; anything else is reported
                print(f"Turn host: token file not removed: {exc}", file=sys.stderr)
    if not token:
        print("Turn host: no token — refusing to start.", file=sys.stderr)
        return 2
    _configure_logging(Path(args.log) if args.log else None)
    host = TurnHost(token, Path(args.spool), idle_exit_s=args.idle_exit)
    try:
        asyncio.run(host.serve(Path(args.state)))
    except KeyboardInterrupt:
        # Ctrl+C is the normal way to stop the host by hand.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
