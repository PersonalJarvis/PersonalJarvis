"""An abandoned native worker must not outlive its last usable owner."""

from __future__ import annotations

import gc
import io
import subprocess
import sys
import threading
import weakref

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.dictation.local_final import LocalFinalSTT
from jarvis.dictation.local_preview import LocalPreviewTranscriber, _WorkerModel


class Process:
    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.kills = 0
        self.reaped = threading.Event()

    def kill(self) -> None:
        self.kills += 1

    def wait(self, timeout: float) -> int:
        self.reaped.set()
        return 0


@pytest.mark.parametrize("kind", ["preview", "final"])
def test_replacing_an_owner_reaps_its_abandoned_worker(kind: str) -> None:
    proc = Process()
    worker = _WorkerModel(proc, "cpu", "int8")
    if kind == "preview":
        owner = LocalPreviewTranscriber(prefer_worker=True)
        owner._model = worker
    else:
        owner = LocalFinalSTT()
        owner._worker = worker
    reference = weakref.ref(worker)
    del worker, owner
    gc.collect()
    assert reference() is None
    assert proc.reaped.wait(2), "the owner disappeared but its worker was never reaped"
    assert proc.kills == 1


def test_a_retained_worker_is_not_reaped_until_the_last_caller_releases_it() -> None:
    proc = Process()
    owner = LocalFinalSTT()
    owner._worker = _WorkerModel(proc, "cpu", "int8")
    active_call = owner._worker
    del owner
    gc.collect()
    assert proc.kills == 0
    del active_call
    gc.collect()
    assert proc.reaped.wait(2)


def test_explicit_close_and_later_collection_reap_only_once() -> None:
    proc = Process()
    worker = _WorkerModel(proc, "cpu", "int8")
    worker.close()
    worker.close()
    del worker
    gc.collect()
    assert proc.kills == 1
    assert proc.stdin.closed and proc.stdout.closed


def test_collection_never_waits_for_a_slow_worker_on_the_collecting_thread() -> None:
    release = threading.Event()
    started = threading.Event()

    class SlowProcess(Process):
        def wait(self, timeout: float) -> int:
            started.set()
            assert release.wait(timeout)
            return super().wait(timeout)

    proc = SlowProcess()
    worker = _WorkerModel(proc, "cpu", "int8")
    try:
        del worker
        gc.collect()
        assert started.wait(1)
        assert not proc.reaped.is_set()
    finally:
        release.set()
    assert proc.reaped.wait(2)


def test_twenty_real_worker_replacements_leave_no_child_processes() -> None:
    """Exercise real process ownership without importing any inference engine."""
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for _ in range(20):
            proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-c", "import sys; sys.stdin.buffer.read()"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            processes.append(proc)
            worker = _WorkerModel(proc, "cpu", "int8")
            del worker
            gc.collect()
            proc.wait(timeout=5)
        assert all(proc.poll() is not None for proc in processes)
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    stream.close()
