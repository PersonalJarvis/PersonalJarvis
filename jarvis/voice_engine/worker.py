"""The engine worker process: ``python -m jarvis.voice_engine.worker``.

Reads protocol frames from stdin and writes frames to stdout; logs go to
stderr. The process ends when stdin closes, so it never outlives the app that
started it — no pidfile, no port, no orphan (``docs/local-live-voice-rebuild.md``
section 4.2).
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import sys
import threading
from typing import Any, BinaryIO

from jarvis.voice_engine import ENGINE_VERSION
from jarvis.voice_engine import protocol as p
from jarvis.voice_engine.engine import ConversationSession, EngineModels

log = logging.getLogger("jarvis.voice_engine.worker")


class StdioTransport:
    """Frames over a pair of byte streams, with I/O on dedicated threads.

    A slow reader on the app side must never block the asyncio loop, so writes
    go through a queue to a writer thread; reads come from a reader thread
    that hands whole frames to the loop.
    """

    def __init__(self, source: BinaryIO, sink: BinaryIO) -> None:
        self._source = source
        self._sink = sink
        self._outbox: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()

    def start(self, loop: asyncio.AbstractEventLoop, inbox: asyncio.Queue[Any]) -> None:
        threading.Thread(target=self._read, args=(loop, inbox), name="engine-stdin",
                         daemon=True).start()
        threading.Thread(target=self._write, name="engine-stdout", daemon=True).start()

    def _read(self, loop: asyncio.AbstractEventLoop, inbox: asyncio.Queue[Any]) -> None:
        reader = p.FrameReader()
        try:
            while True:
                data = self._source.read1(65536) if hasattr(self._source, "read1") else (
                    self._source.read(65536)
                )
                if not data:
                    break
                for frame in reader.feed(data):
                    loop.call_soon_threadsafe(inbox.put_nowait, frame)
        except (OSError, ValueError, p.ProtocolError) as exc:
            log.error("input stream failed: %s", exc)
        finally:
            loop.call_soon_threadsafe(inbox.put_nowait, None)

    def _write(self) -> None:
        while True:
            data = self._outbox.get()
            if data is None:
                return
            try:
                self._sink.write(data)
                self._sink.flush()
            except (OSError, ValueError) as exc:
                log.error("output stream failed: %s", exc)
                return

    def send(self, data: bytes) -> None:
        self._outbox.put(data)

    def close(self) -> None:
        self._outbox.put(None)


class _Emitter:
    def __init__(self, transport: StdioTransport) -> None:
        self._transport = transport

    def json(self, message: dict[str, Any]) -> None:
        self._transport.send(p.encode_json(message))

    def audio(self, slot: int, seq: int, pcm16: bytes) -> None:
        self._transport.send(p.encode_audio(slot, seq, pcm16))


class Worker:
    def __init__(self, transport: StdioTransport) -> None:
        self._transport = transport
        self._emit = _Emitter(transport)
        self._models: EngineModels | None = None
        self._runtime: Any = None
        self._phase = "starting"
        self._sessions: dict[str, ConversationSession] = {}
        self._slots: dict[int, ConversationSession] = {}
        self._loading: asyncio.Task[None] | None = None

    async def run(self) -> int:
        loop = asyncio.get_running_loop()
        inbox: asyncio.Queue[Any] = asyncio.Queue()
        self._transport.start(loop, inbox)
        self._emit.json({"type": p.HELLO, "protocol": p.PROTOCOL_VERSION,
                         "engine_version": ENGINE_VERSION, "pid": os.getpid(),
                         "utf8_mode": bool(sys.flags.utf8_mode)})
        self._state("idle")
        while True:
            frame = await inbox.get()
            if frame is None:
                break
            try:
                if isinstance(frame, p.AudioFrame):
                    session = self._slots.get(frame.slot)
                    if session is not None:
                        session.on_audio(frame.pcm)
                    continue
                if not await self._dispatch(frame):
                    break
            except Exception:
                kind = frame.get("type") if isinstance(frame, dict) else "audio"
                log.exception("failed to handle %s", kind)
                self._emit.json({"type": p.ERROR, "code": "worker_error",
                                 "message": "The voice engine could not handle a request.",
                                 "recoverable": True})
        for session in list(self._sessions.values()):
            session.close()
        self._transport.close()
        return 0

    async def _dispatch(self, message: dict[str, Any]) -> bool:
        kind = message["type"]
        if kind == p.SHUTDOWN:
            return False
        if kind == p.HELLO:
            return True
        if kind == p.CONFIGURE:
            if self._loading is None or self._loading.done():
                self._loading = asyncio.get_running_loop().create_task(self._configure(message))
            return True
        if kind == p.SELFTEST:
            await self._selftest()
            return True
        if kind == p.SESSION_OPEN:
            self._open(message)
            return True
        session = self._sessions.get(str(message.get("session", "")))
        if session is None:
            self._emit.json({"type": p.ERROR, "code": "unknown_session",
                             "message": f"No open session for {kind}.", "recoverable": True})
            return True
        if kind == p.SESSION_CLOSE:
            session.close()
            self._sessions.pop(session.session_id, None)
            self._slots.pop(session.slot, None)
        elif kind == p.RESPONSE_REQUEST:
            session.on_response_request(message.get("language"))
        elif kind == p.TOOL_RESULT:
            session.on_tool_result(str(message.get("call_id", "")), message.get("result") or {})
        elif kind == p.TEXT:
            session.on_text(str(message.get("content", "")))
        elif kind == p.INTERRUPT:
            session.on_interrupt()
        elif kind == p.TRUNCATE:
            session.on_truncate(float(message.get("audio_end_ms", 0)))
        elif kind == p.SESSION_UPDATE:
            session.on_update(instructions=message.get("instructions"),
                              tools=message.get("tools"), language=message.get("language"))
        else:
            self._emit.json({"type": p.ERROR, "code": "unknown_message",
                             "message": f"Unknown message type {kind!r}.", "recoverable": True})
        return True

    async def _configure(self, message: dict[str, Any]) -> None:
        from jarvis.voice_engine.runtime import RuntimeConfig, build_models  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        config = RuntimeConfig.from_message(message)

        def progress(stage: str, fraction: float) -> None:
            loop.call_soon_threadsafe(self._state, "loading", stage, fraction)

        self._state("loading", "start", 0.0)
        try:
            models, described = await asyncio.to_thread(build_models, config, progress)
        except Exception as exc:
            log.exception("loading the voice engine failed")
            self._state("failed", reason=f"The local voice could not load: {exc}")
            return
        self._models = models
        self._runtime = config
        self._state("ready", detail=described)

    async def _selftest(self) -> None:
        from jarvis.voice_engine.runtime import selftest  # noqa: PLC0415

        if self._models is None or self._runtime is None:
            self._emit.json({"type": p.SELFTEST_RESULT, "ok": False,
                             "reason": "The engine is not configured yet."})
            return
        result = await asyncio.to_thread(selftest, self._models, self._runtime.languages)
        self._emit.json({"type": p.SELFTEST_RESULT, **result})

    def _open(self, message: dict[str, Any]) -> None:
        if self._models is None or self._runtime is None:
            self._emit.json({"type": p.ERROR, "code": "not_ready", "recoverable": True,
                             "message": "The local voice is still loading."})
            return
        session_id = str(message.get("session") or "")
        slot = int(message.get("slot", 0))
        session = ConversationSession(
            slot=slot, session_id=session_id, models=self._models, emit=self._emit,
            loop=asyncio.get_running_loop(), config=self._runtime.engine,
            instructions=str(message.get("instructions") or ""),
            language=str(message.get("language") or self._runtime.languages[0]),
            tools=list(message.get("tools") or []), history=list(message.get("history") or []),
            turn_pause_ms=message.get("turn_pause_ms"),
        )
        self._sessions[session_id] = session
        self._slots[slot] = session
        self._emit.json({"type": p.SESSION_READY, "session": session_id, "slot": slot,
                         "input_rate": p.INPUT_RATE, "output_rate": p.OUTPUT_RATE})

    def _state(self, phase: str, stage: str = "", progress: float | None = None, *,
               reason: str = "", detail: dict[str, Any] | None = None) -> None:
        self._phase = phase
        message: dict[str, Any] = {"type": p.STATE, "phase": phase}
        if stage:
            message["stage"] = stage
        if progress is not None:
            message["progress"] = round(progress, 3)
        if reason:
            message["reason"] = reason
        if detail:
            message["detail"] = detail
        self._emit.json(message)


def main() -> int:
    logging.basicConfig(level=os.environ.get("JARVIS_VOICE_ENGINE_LOG", "INFO"),
                        stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not sys.flags.utf8_mode:
        log.warning("Python UTF-8 mode is off; start the worker with PYTHONUTF8=1 "
                    "(some voice packages read their configs in the locale encoding)")
    transport = StdioTransport(sys.stdin.buffer, sys.stdout.buffer)
    return asyncio.run(Worker(transport).run())


if __name__ == "__main__":
    raise SystemExit(main())
