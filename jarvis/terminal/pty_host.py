"""The PTY host — a small process that owns the Agentic IDE's terminals.

Run as ``python -m jarvis.terminal.pty_host --state <file>``. Started on demand
by :mod:`jarvis.terminal.pty_host_client`, never by hand.

## Why the terminals live in their own process

A coding agent in a pane used to be a child of the desktop app. Everything
that ended the app ended the agents with it: closing the last window, "Quit"
in the tray, an update, a restart to pick up a fix. Each of those threw away
work in progress — the pane came back through ``--resume`` at best, as a
fresh CLI that re-reads its conversation and has forgotten what it was in the
middle of.

The shape that avoids this is the one tmux has: a long-lived server owns the
terminals, and the UI is only a client that attaches to them. Closing the
client detaches; opening it again re-attaches to the SAME processes, with the
screen they drew meanwhile. Only a reboot (or closing the pane deliberately)
ends an agent.

This module is that server, and deliberately nothing more:

* it spawns PTYs through the ordinary :class:`~jarvis.terminal.pty_manager.PtyManager`
  (so the output pump, the kill-on-close process tree and the exit-code
  normalisation are the exact code the app already runs),
* it keeps each terminal's raw output tail in a
  :class:`~jarvis.agentic_ide.transcript.ReplayBuffer`, so a client that
  attaches later can rebuild the screen,
* it answers the terminal's emulator queries itself
  (:class:`~jarvis.agentic_ide.terminal_input.TerminalQueryResponder`) —
  those replies are worthless once late, and a round trip through the app
  would make them late (see ``pty_manager``'s module docstring).

Everything that is policy — which agent runs where, resume handles, layout —
stays in the app. The host does not know what a workspace is; it knows
terminal ids and an opaque ``meta`` dict the client attached at spawn.

## Lifetime

The kill-on-close containers (a Job Object on Windows, the process group on
POSIX) belong to THIS process, so the agents live exactly as long as the host
does. The host itself therefore has to outlive the app — it is spawned
detached (``jarvis.ui.relauncher.spawn_detached``) — and it has to go away
on its own when nobody needs it, or every app start would leave one behind:
with no terminal alive and no client connected for :data:`IDLE_EXIT_S`, it
exits.

## Wire protocol

Newline-delimited JSON over one loopback TCP connection, authenticated by a
random token the client generated and handed over in the environment. One
client at a time — a new, authenticated client replaces the old one, which is
exactly what an app restart looks like from here.

Requests carry ``rid`` and are answered with ``{"rid", "ok", ...}``. Events
the host sends unprompted carry ``ev``: ``o`` (output) and ``exit``. Output is
only streamed for terminals this client spawned or adopted; everything else
keeps filling its replay buffer until somebody adopts it.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

#: Bumped only for an incompatible change. A client refuses a host speaking
#: another version rather than guessing — and must then leave it alone, since
#: that host may be holding agents from before an update.
PROTOCOL_VERSION = 1

#: Environment variable carrying the auth token from the client that started
#: this host. Never on the command line: a process list is readable by every
#: process of the user.
TOKEN_ENV = "JARVIS_PTY_HOST_TOKEN"  # noqa: S105 - a variable name, not a secret

#: With no terminal alive and no client attached for this long, the host exits.
#: Short enough that a closed app does not leave a process behind for long,
#: long enough to survive the gap between an app quitting and starting again.
IDLE_EXIT_S = 60.0

#: How often the idle check runs.
_IDLE_POLL_S = 5.0

#: Largest single frame a client may send. A pasted prompt is the biggest
#: thing that legitimately crosses; this is far above it and far below
#: anything that could hurt.
MAX_FRAME_BYTES = 16 * 1024 * 1024


@dataclass(slots=True)
class HostedTerminal:
    """One terminal the host owns."""

    terminal_id: str
    pid: int
    meta: dict[str, Any]
    cols: int
    rows: int
    started_at: float
    replay: Any  # jarvis.agentic_ide.transcript.ReplayBuffer (imported lazily)
    queries: Any  # jarvis.agentic_ide.terminal_input.TerminalQueryResponder
    #: True once the connected client asked for this terminal's live output.
    streaming: bool = False
    exit_code: int | None = None


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
                # The client went away mid-send. Its terminals keep running and
                # keep filling their replay buffers; the next client adopts them.
                logger.info("PTY host: client connection lost while sending: {}", exc)
                self.closed = True

    def close(self) -> None:
        self.closed = True
        try:
            self.writer.close()
        except Exception as exc:  # noqa: BLE001 - already closed is the goal
            logger.debug("PTY host: closing a client socket failed: {}", exc)


class PtyHost:
    """The server: a :class:`PtyManager` plus replay buffers plus one client."""

    def __init__(self, token: str, *, idle_exit_s: float = IDLE_EXIT_S) -> None:
        from jarvis.terminal.pty_manager import PtyManager

        self._token = token
        self._manager = PtyManager()
        self._terminals: dict[str, HostedTerminal] = {}
        #: Exit codes of terminals that ended while nobody was attached, so an
        #: adopting client learns how they ended instead of "unknown id".
        self._ended: dict[str, int] = {}
        self._client: _Client | None = None
        self._idle_exit_s = idle_exit_s
        self._idle_since: float | None = time.monotonic()
        self._stop = asyncio.Event()

    # ------------------------------------------------------------ lifecycle
    async def serve(self, state_path: Path) -> None:
        """Listen on a loopback port, publish it, and run until idle."""
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
                # When the MACHINE booted. A client compares it with the current
                # boot to know for certain that a missing host died with a
                # reboot, rather than guessing from a failed connection.
                "boot_time": _boot_time(),
            },
        )
        logger.info("PTY host listening on 127.0.0.1:{} (pid {})", port, os.getpid())
        idle = asyncio.create_task(self._idle_watch(), name="pty-host-idle")
        try:
            async with server:
                await self._stop.wait()
        finally:
            idle.cancel()
            _remove_state(state_path, os.getpid())
            # Reached only when idle — no terminal left to protect. Closing
            # anyway keeps a forced stop from leaking a tree.
            self._manager.close_all()
            logger.info("PTY host stopped")

    async def _idle_watch(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(_IDLE_POLL_S)
            busy = bool(self._terminals) or (self._client is not None and not self._client.closed)
            now = time.monotonic()
            if busy:
                self._idle_since = None
                continue
            if self._idle_since is None:
                self._idle_since = now
            elif now - self._idle_since >= self._idle_exit_s:
                logger.info("PTY host idle for {:.0f} s — exiting", now - self._idle_since)
                self._stop.set()

    # ----------------------------------------------------------- connection
    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = _Client(reader, writer)
        try:
            hello = await asyncio.wait_for(_read_frame(reader), timeout=10.0)
        except (TimeoutError, ValueError, ConnectionError, OSError) as exc:
            logger.info("PTY host: connection dropped before hello: {}", exc)
            client.close()
            return
        if (
            not isinstance(hello, dict)
            or hello.get("op") != "hello"
            or not hmac.compare_digest(str(hello.get("token", "")), self._token)
        ):
            logger.warning("PTY host: refused a connection with a bad hello")
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
            logger.info("PTY host: a new client replaces the attached one")
            previous.close()
        # A new client starts with nothing streaming: it adopts what it wants.
        for term in self._terminals.values():
            term.streaming = False
        self._client = client
        await client.send(
            {
                "rid": hello.get("rid"),
                "ok": True,
                "pid": os.getpid(),
                "proto": PROTOCOL_VERSION,
                "terminals": self._listing(),
            }
        )
        try:
            while not client.closed:
                try:
                    frame = await _read_frame(reader)
                except (ValueError, ConnectionError, OSError) as exc:
                    logger.info("PTY host: client disconnected: {}", exc)
                    break
                if frame is None:
                    break
                await self._handle(client, frame)
        finally:
            client.close()
            if self._client is client:
                self._client = None
                for term in self._terminals.values():
                    term.streaming = False
            logger.info(
                "PTY host: client detached — {} terminal(s) keep running",
                len(self._terminals),
            )

    async def _handle(self, client: _Client, frame: dict[str, Any]) -> None:
        op = frame.get("op")
        rid = frame.get("rid")
        try:
            if op == "spawn":
                reply = await self._spawn(frame)
            elif op == "adopt":
                reply = self._adopt(str(frame.get("id", "")))
            elif op == "write":
                self._manager.write(str(frame.get("id", "")), str(frame.get("d", "")))
                return
            elif op == "resize":
                self._resize(frame)
                return
            elif op == "appearance":
                term = self._terminals.get(str(frame.get("id", "")))
                if term is not None:
                    term.queries.appearance = str(frame.get("appearance", ""))
                return
            elif op == "close":
                self._close(str(frame.get("id", "")))
                return
            elif op == "list":
                reply = {"terminals": self._listing()}
            elif op == "ping":
                reply = {}
            else:
                reply = {"ok": False, "error": f"unknown op {op!r}"}
        except Exception as exc:  # noqa: BLE001 - one bad request must not end the host
            logger.warning("PTY host: {} failed: {}", op, exc)
            reply = {"ok": False, "error": str(exc)}
        if rid is not None:
            reply.setdefault("ok", True)
            reply["rid"] = rid
            await client.send(reply)

    # ------------------------------------------------------------ requests
    async def _spawn(self, frame: dict[str, Any]) -> dict[str, Any]:
        from jarvis.agentic_ide.terminal_input import TerminalQueryResponder
        from jarvis.agentic_ide.transcript import ReplayBuffer

        argv = tuple(str(part) for part in frame.get("argv") or ())
        if not argv:
            return {"ok": False, "error": "empty argv"}
        cols = int(frame.get("cols") or 120)
        rows = int(frame.get("rows") or 30)
        env = frame.get("env")
        queries = TerminalQueryResponder()
        appearance = frame.get("appearance")
        if isinstance(appearance, str) and appearance:
            queries.appearance = appearance
        replay = ReplayBuffer()
        # The id is not known until spawn returns, and output can arrive before
        # that — so the callbacks look the terminal up by the id they are given.
        pending: list[tuple[str, str]] = []

        async def _output(tid: str, text: str) -> None:
            term = self._terminals.get(tid)
            if term is None:
                pending.append((tid, text))
                return
            await self._deliver(term, text)

        async def _closed(tid: str, code: int) -> None:
            await self._on_exit(tid, code)

        session = await self._manager.spawn(
            shell_argv=argv,
            shell_id=str(frame.get("shell_id") or "pty-host"),
            cwd=frame.get("cwd") or None,
            cols=cols,
            rows=rows,
            on_output=_output,
            on_closed=_closed,
            env=dict(env) if isinstance(env, dict) else None,
            on_probe=queries.feed,
        )
        term = HostedTerminal(
            terminal_id=session.terminal_id,
            pid=session.pid,
            meta=dict(frame.get("meta") or {}),
            cols=cols,
            rows=rows,
            started_at=time.time(),
            replay=replay,
            queries=queries,
            # The spawning client is the viewer from the first byte.
            streaming=True,
        )
        self._terminals[term.terminal_id] = term
        for _tid, text in pending:
            await self._deliver(term, text)
        pending.clear()
        return {"id": term.terminal_id, "pid": term.pid}

    def _adopt(self, terminal_id: str) -> dict[str, Any]:
        term = self._terminals.get(terminal_id)
        if term is None:
            code = self._ended.pop(terminal_id, None)
            return {"alive": False, "code": code}
        # Replay and "stream from now on" in ONE step on the loop: no output can
        # land between the two, so the client sees every byte exactly once.
        term.streaming = True
        return {
            "alive": True,
            "replay": term.replay.text(),
            "truncated": bool(term.replay.truncated),
            "cols": term.cols,
            "rows": term.rows,
        }

    def _resize(self, frame: dict[str, Any]) -> None:
        terminal_id = str(frame.get("id", ""))
        cols = int(frame.get("cols") or 0)
        rows = int(frame.get("rows") or 0)
        if cols <= 0 or rows <= 0:
            return
        if self._manager.resize(terminal_id, cols, rows):
            term = self._terminals.get(terminal_id)
            if term is not None:
                term.cols, term.rows = cols, rows

    def _close(self, terminal_id: str) -> None:
        # The pump reports the exit afterwards through ``_on_exit``.
        self._manager.close(terminal_id)

    def _listing(self) -> list[dict[str, Any]]:
        return [
            {
                "id": term.terminal_id,
                "pid": term.pid,
                "meta": term.meta,
                "cols": term.cols,
                "rows": term.rows,
                "started_at": term.started_at,
            }
            for term in self._terminals.values()
        ]

    # --------------------------------------------------------------- events
    async def _deliver(self, term: HostedTerminal, text: str) -> None:
        term.replay.feed(text)
        client = self._client
        if client is not None and term.streaming:
            await client.send({"ev": "o", "id": term.terminal_id, "d": text})

    async def _on_exit(self, terminal_id: str, code: int) -> None:
        term = self._terminals.pop(terminal_id, None)
        streaming = term is not None and term.streaming
        client = self._client
        if client is not None and not client.closed and streaming:
            await client.send({"ev": "exit", "id": terminal_id, "code": code})
        else:
            # Nobody saw it end; keep the answer for whoever adopts it next.
            self._ended[terminal_id] = code
            while len(self._ended) > 256:
                self._ended.pop(next(iter(self._ended)))
        logger.info("PTY host: terminal {} ended (exit {})", terminal_id, code)


# ---------------------------------------------------------------- framing
async def _read_frame(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """One JSON line, or None at a clean end of stream."""
    line = await reader.readline()
    if not line:
        return None
    frame = json.loads(line.decode("utf-8"))
    if not isinstance(frame, dict):
        raise ValueError("frame is not an object")
    return frame


def _write_state(path: Path, data: dict[str, Any]) -> None:
    """Publish where the host listens — atomically, readable only by the user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError as exc:
        # Windows keeps per-user ACLs on the data dir; chmod is a POSIX nicety.
        logger.debug("PTY host: could not restrict the state file: {}", exc)
    os.replace(tmp, path)


def _boot_time() -> float:
    """When this machine last booted (0.0 when it cannot be told)."""
    try:
        import psutil

        return float(psutil.boot_time())
    except Exception as exc:  # noqa: BLE001 - optional evidence, never fatal
        logger.debug("PTY host: boot time unavailable: {}", exc)
        return 0.0


def _remove_state(path: Path, pid: int) -> None:
    """Remove the state file — only if it still describes THIS host."""
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # gone or rewritten by a newer host: not ours to remove
        return
    if isinstance(current, dict) and current.get("pid") == pid:
        try:
            path.unlink()
        except OSError as exc:
            logger.debug("PTY host: state file not removed: {}", exc)


def _configure_logging(log_path: Path | None) -> None:
    logger.remove()
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_path),
        level="INFO",
        rotation="2 MB",
        retention=2,
        encoding="utf-8",
        enqueue=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis.terminal.pty_host")
    parser.add_argument("--state", required=True, help="Where to publish the port and token.")
    parser.add_argument("--log", default=None, help="Log file (none when omitted).")
    parser.add_argument("--idle-exit", type=float, default=IDLE_EXIT_S)
    parser.add_argument(
        "--token-file",
        default=None,
        help="Read the token from this file and delete it (a start that cannot pass env).",
    )
    args = parser.parse_args(argv)

    token = os.environ.pop(TOKEN_ENV, "")
    if not token and args.token_file:
        token_path = Path(args.token_file)
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"PTY host: token file unreadable: {exc}", file=sys.stderr)
        finally:
            try:
                token_path.unlink()
            except OSError as exc:  # already gone is fine; anything else is logged
                print(f"PTY host: token file not removed: {exc}", file=sys.stderr)
    if not token:
        print("PTY host: no token in the environment — refusing to start.", file=sys.stderr)
        return 2
    _configure_logging(Path(args.log) if args.log else None)
    host = PtyHost(token, idle_exit_s=args.idle_exit)
    try:
        asyncio.run(host.serve(Path(args.state)))
    except KeyboardInterrupt:
        # Ctrl+C is the normal way to stop the host by hand.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
