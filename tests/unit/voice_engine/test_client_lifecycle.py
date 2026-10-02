"""Failed starts release workers; live audio keeps its order on the wire."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.voice_engine import protocol as p
from jarvis.voice_engine.client import EngineClient


class Pipe:
    def __init__(self) -> None:
        self.closed = False

    def write(self, data: bytes) -> None:
        pass  # The fake worker never responds to shutdown: exercise forced cleanup.

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class Process:
    def __init__(self, wire: bytes = b"") -> None:
        self.stdin = Pipe()
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(wire)
        self.returncode = None
        self.pid = 123
        self.killed = False
        self.exited = asyncio.Event()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -1
        self.stdout.feed_eof()
        self.exited.set()

    async def wait(self) -> int:
        await self.exited.wait()
        return self.returncode


def install_process(monkeypatch: pytest.MonkeyPatch, process: Process) -> None:
    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)


@pytest.mark.asyncio
async def test_handshake_timeout_and_cancellation_release_process_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for cancel in (False, True):
        process = Process()
        install_process(monkeypatch, process)
        client = EngineClient("fake", stderr_path=tmp_path / "worker.log")
        original_close = client.close

        async def close(original_close=original_close):
            return await original_close(timeout_s=0.01)

        monkeypatch.setattr(client, "close", close)
        task = asyncio.create_task(client.start(timeout_s=0.02))
        await asyncio.sleep(0)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
        assert process.killed and process.stdin.closed
        assert client._process is None and client._stderr_handle is None
        assert client._reader_task is None


@pytest.mark.asyncio
async def test_spawn_error_closes_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def spawn(*args, **kwargs):
        raise FileNotFoundError("missing interpreter")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = EngineClient("missing", stderr_path=tmp_path / "worker.log")
    with pytest.raises(FileNotFoundError):
        await client.start()
    assert client._stderr_handle is None


@pytest.mark.asyncio
async def test_live_frames_preserve_audio_completion_and_interrupt_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = {"type": "response.done", "session": "s"}
    interrupted = {"type": "interrupted", "session": "s"}
    process = Process(
        p.encode_json({"type": "hello", "protocol": p.PROTOCOL_VERSION})
        + p.encode_audio(1, 1, b"\x01\x00") + p.encode_json(done)
        + p.encode_audio(1, 2, b"\x02\x00") + p.encode_json(interrupted))
    install_process(monkeypatch, process)
    client = EngineClient("fake", ordered=True)
    try:
        await client.start()
        received = [await asyncio.wait_for(client.messages.get(), 1) for _ in range(4)]
        assert received == [p.AudioFrame(1, 1, b"\x01\x00"), done,
                            p.AudioFrame(1, 2, b"\x02\x00"), interrupted]
        assert client.audio_frames.empty()
    finally:
        await client.close(timeout_s=0.01)


@pytest.mark.asyncio
async def test_incompatible_worker_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    process = Process(p.encode_json({"type": "hello", "protocol": 999}))
    install_process(monkeypatch, process)
    client = EngineClient("fake")
    original_close = client.close

    async def close():
        return await original_close(timeout_s=0.01)

    monkeypatch.setattr(client, "close", close)
    with pytest.raises(p.ProtocolError, match="incompatible"):
        await client.start()
    assert process.killed and client._process is None


@pytest.mark.asyncio
async def test_broken_output_pipe_ends_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    process = Process()
    process.stdout.set_exception(OSError("broken pipe"))
    install_process(monkeypatch, process)
    client = EngineClient("fake")
    with pytest.raises(RuntimeError, match="exited"):
        await client.start()
    assert process.killed and client._process is None


@pytest.mark.asyncio
async def test_cancelling_one_closer_does_not_cancel_worker_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Process(p.encode_json({"type": "hello", "protocol": p.PROTOCOL_VERSION}))
    install_process(monkeypatch, process)
    client = EngineClient("fake")
    await client.start()
    closer = asyncio.create_task(client.close(timeout_s=0.02))
    await asyncio.sleep(0)
    closer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closer
    await asyncio.wait_for(client.close(), 1)
    assert process.killed and client._process is None and client._reader_task is None
