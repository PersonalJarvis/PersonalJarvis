"""Tests for the PTY backend seam (Wave 1.1, AD-6 + AD-9).

Strategy (this suite runs on the Windows dev box where ``ptyprocess`` is NOT
installed):

* Factory-selection + str<->bytes normalization tests are pure logic — they
  monkeypatch ``detect_platform`` / ``detect_capabilities`` and use the
  ``fake_pty_backend`` fakes, so they pass on every OS leg.
* The ONE real-PTY roundtrip test (spawns a real ``bash``) is guarded with
  ``skipif(win32)`` + ``importorskip("ptyprocess")`` so it cleanly SKIPS on
  Windows and runs on the CI Linux/macOS legs.
"""

from __future__ import annotations

import sys

import pytest

import jarvis.terminal.backend as backend_mod
from jarvis.platform.capabilities import reset_capabilities_cache
from jarvis.terminal.backend import (
    NullPtyBackend,
    PtyBackend,
    PtyHandle,
    UnixPtyBackend,
    WinptyBackend,
    make_pty_backend,
)
from tests.fakes.fake_pty_backend import (
    FakeBytesPtyProcess,
    FakePtyBackend,
    FakePtyHandle,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_capabilities_cache()
    yield
    reset_capabilities_cache()


def _force(monkeypatch, platform: str, *, has_pty: bool = True) -> None:
    """Make the backend factory see a given platform + PTY capability."""
    monkeypatch.setattr(backend_mod, "detect_platform", lambda: platform)

    class _Caps:
        pass

    caps = _Caps()
    caps.has_pty = has_pty  # type: ignore[attr-defined]
    monkeypatch.setattr(backend_mod, "detect_capabilities", lambda: caps)


# ----------------------------------------------------------------------
# Factory selection (logic — runs on all OS legs)
# ----------------------------------------------------------------------


def test_factory_selects_winpty_on_win32(monkeypatch):
    _force(monkeypatch, "win32")
    assert isinstance(make_pty_backend(), WinptyBackend)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_factory_selects_unix_on_posix(monkeypatch, platform):
    _force(monkeypatch, platform)
    assert isinstance(make_pty_backend(), UnixPtyBackend)


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_factory_returns_null_when_no_pty_capability(monkeypatch, platform):
    _force(monkeypatch, platform, has_pty=False)
    assert isinstance(make_pty_backend(), NullPtyBackend)


def test_factory_never_raises_on_unknown_platform(monkeypatch):
    # AD-6: any non-win32 maps to the Unix backend, never a crash.
    _force(monkeypatch, "sunos5")
    assert isinstance(make_pty_backend(), UnixPtyBackend)


def test_backends_satisfy_protocol():
    # Structural compatibility with the PtyBackend Protocol.
    assert isinstance(WinptyBackend(), PtyBackend)
    assert isinstance(UnixPtyBackend(), PtyBackend)
    assert isinstance(NullPtyBackend(), PtyBackend)


def test_fake_handle_satisfies_pty_handle_protocol():
    handle = FakePtyHandle()
    assert isinstance(handle, PtyHandle)


# ----------------------------------------------------------------------
# Null backend degrade (AD-6 — typed RuntimeError, not a crash)
# ----------------------------------------------------------------------


def test_null_backend_spawn_raises_clear_english_runtime_error():
    backend = NullPtyBackend()
    with pytest.raises(RuntimeError) as exc:
        backend.spawn(argv=("bash",), cwd=None, cols=80, rows=24)
    msg = str(exc.value)
    assert "pseudo-terminal" in msg.lower()
    assert "terminal is unavailable" in msg.lower()


# ----------------------------------------------------------------------
# str <-> bytes normalization at the Unix seam
# ----------------------------------------------------------------------


def test_unix_backend_spawn_threads_args_through_to_ptyprocess(monkeypatch):
    """UnixPtyBackend.spawn must call ptyprocess.PtyProcess.spawn 1:1.

    ptyprocess takes ``dimensions=(rows, cols)`` and ``cwd=...`` — assert the
    factory does not transpose them.
    """
    fake_module = type(sys)("ptyprocess")
    fake_module.PtyProcess = FakeBytesPtyProcess  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ptyprocess", fake_module)

    backend = UnixPtyBackend()
    handle = backend.spawn(argv=("bash",), cwd="/home/x", cols=80, rows=24)
    assert isinstance(handle, PtyHandle)
    assert FakeBytesPtyProcess.last_spawn == {
        "argv": ["bash"],
        "cwd": "/home/x",
        "dimensions": (24, 80),  # ptyprocess takes (rows, cols)
        "env": None,  # no env supplied -> inherit
    }


def test_unix_backend_threads_env_through(monkeypatch):
    """A caller-supplied env must reach ptyprocess.PtyProcess.spawn(env=...).

    The Antigravity brain/worker hardens the child PATH + drops API keys, so the
    seam must forward env, not silently inherit os.environ.
    """
    fake_module = type(sys)("ptyprocess")
    fake_module.PtyProcess = FakeBytesPtyProcess  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ptyprocess", fake_module)

    backend = UnixPtyBackend()
    backend.spawn(
        argv=("agy",), cwd=None, cols=80, rows=24, env={"PATH": "/safe"}
    )
    assert FakeBytesPtyProcess.last_spawn["env"] == {"PATH": "/safe"}


def test_unix_backend_decodes_bytes_to_str_on_read():
    """ptyprocess.read returns bytes -> handle.read must return str."""
    from jarvis.terminal.backend import _UnixPtyHandle

    fake_proc = FakeBytesPtyProcess()
    fake_proc.queue_output("héllo".encode())

    h = _UnixPtyHandle(fake_proc)
    out = h.read(4096)
    assert isinstance(out, str)
    assert out == "héllo"


def test_unix_backend_encodes_str_to_bytes_on_write():
    """handle.write takes str -> ptyprocess.write must receive bytes."""
    from jarvis.terminal.backend import _UnixPtyHandle

    fake_proc = FakeBytesPtyProcess()
    h = _UnixPtyHandle(fake_proc)
    h.write("ls -l\n")
    assert fake_proc.written == [b"ls -l\n"]
    assert all(isinstance(b, bytes) for b in fake_proc.written)


def test_unix_handle_setwinsize_terminate_exitstatus_pid():
    from jarvis.terminal.backend import _UnixPtyHandle

    fake_proc = FakeBytesPtyProcess(pid=999, exitstatus=0)
    h = _UnixPtyHandle(fake_proc)
    assert h.pid == 999
    h.setwinsize(40, 120)
    assert fake_proc.winsize == (40, 120)
    assert h.isalive() is True
    h.terminate(force=True)
    assert fake_proc.terminated_force is True
    assert h.isalive() is False
    assert h.exitstatus == 0


def test_winpty_handle_passes_str_through_unchanged():
    """winpty already deals in str — the handle must not double-decode."""
    from jarvis.terminal.backend import _WinptyHandle

    class _FakeWinpty:
        pid = 5
        exitstatus = None

        def __init__(self):
            self.written = []
            self.winsize = None

        def write(self, data):
            self.written.append(data)

        def setwinsize(self, rows, cols):
            self.winsize = (rows, cols)

        def read(self, size):
            return "plain str output"

        def isalive(self):
            return True

        def terminate(self, force):
            self.forced = force

    fake = _FakeWinpty()
    h = _WinptyHandle(fake)
    assert h.read(4096) == "plain str output"
    h.write("echo hi")
    assert fake.written == ["echo hi"]  # str passed through, not encoded
    h.setwinsize(24, 80)
    assert fake.winsize == (24, 80)


def test_winpty_handle_tolerates_bytes_defensively():
    from jarvis.terminal.backend import _WinptyHandle

    class _FakeWinptyBytes:
        pid = 1
        exitstatus = 0

        def read(self, size):
            return b"byte output"

        def write(self, data):
            pass

        def setwinsize(self, rows, cols):
            pass

        def isalive(self):
            return False

        def terminate(self, force):
            pass

    h = _WinptyHandle(_FakeWinptyBytes())
    assert h.read(4096) == "byte output"  # decoded defensively


# ----------------------------------------------------------------------
# Fake backend wiring (proves PtyManager-facing surface)
# ----------------------------------------------------------------------


def test_fake_backend_records_spawn_args_and_echoes():
    backend = FakePtyBackend(pid=321)
    handle = backend.spawn(argv=("zsh", "-i"), cwd="/home", cols=100, rows=30)
    assert backend.spawn_calls == [
        {"argv": ("zsh", "-i"), "cwd": "/home", "cols": 100, "rows": 30, "env": None}
    ]
    assert handle.pid == 321
    handle.write("echo round-trip")
    assert handle.read(4096) == "echo round-trip"  # str echo


# ----------------------------------------------------------------------
# The ONE real-PTY roundtrip — SKIPS on Windows, runs on CI Linux/macOS
# ----------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="real Unix PTY")
def test_real_unix_pty_roundtrip():
    pytest.importorskip("ptyprocess")
    reset_capabilities_cache()
    backend = UnixPtyBackend()
    handle = backend.spawn(argv=("bash", "-c", "echo hi"), cwd=None, cols=80, rows=24)

    collected = ""
    # Drain until EOF / process dies.
    for _ in range(2000):
        try:
            chunk = handle.read(4096)
        except EOFError:
            break
        if chunk:
            collected += chunk
            assert isinstance(chunk, str)
        elif not handle.isalive():
            break

    assert "hi" in collected
    # Give the child a moment to reap its exit status.
    for _ in range(2000):
        if not handle.isalive():
            break
    assert handle.exitstatus == 0
