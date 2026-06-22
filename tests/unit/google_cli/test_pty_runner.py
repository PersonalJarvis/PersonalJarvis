"""Tests for the ConPTY/PTY runner that drives the Antigravity ``agy`` CLI.

``agy`` is a TUI tool: over a plain pipe it emits 0 bytes; over a real
pseudo-terminal it renders the answer wrapped in ANSI control sequences and
window-title OSC frames. The runner spawns it over a PTY (cross-platform seam),
reads until exit/timeout, strips the terminal noise, and extracts the answer.

No real CLI, PTY, or network is touched — the backend is faked.
"""
from __future__ import annotations

import pytest

import os

from jarvis.google_cli.pty_runner import (
    PtyRunResult,
    extract_cli_answer,
    repair_agy_path,
    run_cli_over_pty,
    strip_terminal_noise,
)

# A faithful slice of a real ``agy --print`` capture over ConPTY (PONG probe,
# 2026-06-21): screen-setup CSI codes + three npm/cmd window-title OSC frames +
# the answer "PONG" + trailing cmd.exe title.
_REAL_AGY_CAPTURE = (
    "\r\x1b[?9001h\x1b[?1004h\x1b[?25l\x1b[2J\x1b[m\x1b[H"
    "\x1b]0;npm\x07\x1b[?25h\x1b]0;npm exec @iflow-mcp/ollama-mcp\x07"
    "\x1b]0;npm exec firecrawl-mcp\x07PONG\r\r\n"
    "\x1b]0;C:\\windows\\system32\\cmd.exe \x07\r\n"
)


# ---- strip_terminal_noise -------------------------------------------------


def test_strip_removes_csi_sequences():
    assert strip_terminal_noise("\x1b[2J\x1b[H\x1b[mPONG") == "PONG"


def test_strip_removes_osc_window_titles():
    assert strip_terminal_noise("\x1b]0;npm exec firecrawl-mcp\x07PONG") == "PONG"


def test_strip_removes_osc_terminated_by_st():
    # OSC may end with ST (ESC \) instead of BEL.
    assert strip_terminal_noise("\x1b]0;title\x1b\\PONG") == "PONG"


def test_strip_real_capture_yields_clean_answer():
    cleaned = strip_terminal_noise(_REAL_AGY_CAPTURE)
    assert "\x1b" not in cleaned
    assert "\x07" not in cleaned
    assert "npm exec" not in cleaned  # window titles gone
    assert "PONG" in cleaned


def test_strip_preserves_plain_text_and_newlines():
    assert strip_terminal_noise("line one\nline two\n") == "line one\nline two\n"


# ---- extract_cli_answer ---------------------------------------------------


def test_extract_answer_from_real_capture():
    assert extract_cli_answer(_REAL_AGY_CAPTURE) == "PONG"


def test_extract_multiline_answer_preserved():
    raw = "\x1b[2J\x1b[Hfirst line\r\nsecond line\r\n"
    assert extract_cli_answer(raw) == "first line\nsecond line"


def test_extract_empty_when_only_noise():
    assert extract_cli_answer("\x1b[2J\x1b[H\x1b]0;npm\x07\r\n") == ""


# agy renders a Braille progress spinner (U+2800-U+28FF) by repeatedly
# rewriting the SAME line with a leading CR ("\r⠋ Fetching...\r⠙ Fetching...").
# Over a PTY capture those frames arrive as bare-CR overwrites; the old
# CR->LF rewrite turned each frame into its own line, so 18 "⠋ Fetching
# available models..." lines leaked into the cleaned answer (real 2026-06-21
# `agy models` capture). The answer is only the text after the last CR.
_REAL_AGY_SPINNER_CAPTURE = (
    "\x1b[?25l"
    "\r⠋ Fetching available models..."
    "\r⠙ Fetching available models..."
    "\x1b]0;npm\x07"
    "\r⠹ Fetching available models..."
    "\r⠸ Fetching available models..."
    "\rGemini 3.5 Flash (Medium)\r\n"
    "Gemini 3.1 Pro (Low)\r\n"
    "GPT-OSS 120B (Medium)\r\n"
)


def test_extract_drops_spinner_overwrite_frames():
    out = extract_cli_answer(_REAL_AGY_SPINNER_CAPTURE)
    assert "Fetching available models" not in out  # transient status gone
    assert "⠋" not in out and "⠹" not in out  # no Braille glyphs
    assert out == "Gemini 3.5 Flash (Medium)\nGemini 3.1 Pro (Low)\nGPT-OSS 120B (Medium)"


def test_extract_spinner_then_answer():
    # A short turn: a couple of "Thinking" spinner frames, then the answer.
    raw = "\r⠋ Thinking...\r⠙ Thinking...\rpong\r\n"
    assert extract_cli_answer(raw) == "pong"


def test_extract_strips_trailing_lone_spinner_glyph():
    # Belt-and-suspenders: a stray spinner glyph on the final visible line.
    assert extract_cli_answer("⠋ answer text\r\n") == "answer text"


# ---- repair_agy_path ------------------------------------------------------
# agy spawns cmd.exe + npm internally (MCP servers); in a degraded environment
# (PATH stripped of System32 / Node) that fails with "chcp nicht erkannt". The
# child PATH must carry the standard Windows system dirs.


def test_repair_path_adds_system32_when_missing():
    out = repair_agy_path(
        r"C:\Some\Other",
        is_windows=True,
        system_root=r"C:\Windows",
    )
    parts = [p.lower() for p in out.split(os.pathsep)]
    assert r"c:\windows\system32" in parts
    assert r"c:\windows\system32\wbem" in parts
    assert r"c:\some\other" in parts.__str__() or r"c:\some\other" in parts


def test_repair_path_idempotent_when_present():
    existing = os.pathsep.join([r"C:\Windows\System32", r"C:\Windows\System32\Wbem", r"C:\Windows"])
    out = repair_agy_path(existing, is_windows=True, system_root=r"C:\Windows")
    # No duplicate System32 entries.
    assert out.lower().count(r"c:\windows\system32".lower()) == existing.lower().count(
        r"c:\windows\system32".lower()
    )


def test_repair_path_includes_node_dir_when_given():
    out = repair_agy_path(
        "", is_windows=True, system_root=r"C:\Windows", node_dir=r"C:\Program Files\nodejs"
    )
    assert r"c:\program files\nodejs" in [p.lower() for p in out.split(os.pathsep)]


def test_repair_path_noop_on_posix():
    assert repair_agy_path("/usr/bin:/bin", is_windows=False) == "/usr/bin:/bin"


# ---- run_cli_over_pty -----------------------------------------------------


class _FakeHandle:
    """A fake PtyHandle: yields queued chunks, then EOF; tracks terminate."""

    def __init__(self, chunks: list[str], *, exit_status: int = 0) -> None:
        self._chunks = list(chunks)
        self._exit = exit_status
        self.terminated = False
        self._alive = True

    @property
    def pid(self) -> int:
        return 4242

    @property
    def exitstatus(self) -> int | None:
        return None if self._alive else self._exit

    def read(self, size: int) -> str:
        if self._chunks:
            return self._chunks.pop(0)
        self._alive = False
        raise EOFError

    def isalive(self) -> bool:
        return self._alive

    def terminate(self, force: bool) -> None:
        self.terminated = True
        self._alive = False

    def write(self, data: str) -> None:  # parity, unused
        pass

    def setwinsize(self, rows: int, cols: int) -> None:
        pass


class _FakeBackend:
    """Captures the spawn kwargs and returns a pre-seeded fake handle."""

    def __init__(self, handle: _FakeHandle) -> None:
        self._handle = handle
        self.spawn_kwargs: dict | None = None

    def spawn(self, argv, cwd, cols, rows, env=None):  # noqa: ANN001
        self.spawn_kwargs = {
            "argv": argv,
            "cwd": cwd,
            "cols": cols,
            "rows": rows,
            "env": env,
        }
        return self._handle


def test_run_reads_until_eof_and_extracts_answer():
    handle = _FakeHandle(["\x1b[2J\x1b[H", "PONG\r\n"])
    backend = _FakeBackend(handle)
    result = run_cli_over_pty(
        ("agy", "--print", "x"), timeout_s=5.0, backend=backend,
    )
    assert isinstance(result, PtyRunResult)
    assert result.text == "PONG"
    assert result.timed_out is False
    assert result.error is None
    assert handle.terminated is False  # exited cleanly, no kill needed


def test_run_passes_env_and_cwd_to_backend():
    backend = _FakeBackend(_FakeHandle(["ok\r\n"]))
    run_cli_over_pty(
        ("agy", "--print", "x"),
        timeout_s=5.0,
        cwd="/tmp/work",
        env={"PATH": "/safe"},
        backend=backend,
    )
    assert backend.spawn_kwargs["cwd"] == "/tmp/work"
    assert backend.spawn_kwargs["env"] == {"PATH": "/safe"}


def test_run_times_out_and_terminates():
    # Never EOFs, never goes dead on its own -> the deadline must fire.
    class _NeverEnds(_FakeHandle):
        def read(self, size: int) -> str:
            return ""  # no data, still alive

        def isalive(self) -> bool:
            return True

    handle = _NeverEnds([])
    backend = _FakeBackend(handle)
    # A fake clock so the test doesn't actually wait.
    ticks = iter([0.0, 0.1, 0.2, 99.0, 99.0, 99.0])
    result = run_cli_over_pty(
        ("agy", "--print", "x"),
        timeout_s=1.0,
        backend=backend,
        _now=lambda: next(ticks),
        _sleep=lambda _s: None,
    )
    assert result.timed_out is True
    assert handle.terminated is True


def test_run_invokes_on_spawn_with_pid():
    """The worker uses on_spawn to assign the child PID to its Job Object."""
    handle = _FakeHandle(["ok\r\n"])
    backend = _FakeBackend(handle)
    seen: list[int] = []
    run_cli_over_pty(
        ("agy", "--print", "x"),
        timeout_s=5.0,
        backend=backend,
        on_spawn=seen.append,
    )
    assert seen == [4242]  # _FakeHandle.pid


def test_run_no_pty_backend_returns_error_result():
    class _NullBackend:
        def spawn(self, *a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("No pseudo-terminal backend available on this host")

    result = run_cli_over_pty(
        ("agy", "--print", "x"), timeout_s=5.0, backend=_NullBackend(),
    )
    assert result.text == ""
    assert result.error is not None
    assert "pseudo-terminal" in result.error
