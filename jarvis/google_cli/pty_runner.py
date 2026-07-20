"""Drive a TUI agent CLI (Antigravity ``agy``) over a real pseudo-terminal.

Why this exists: ``agy`` is part of the Antigravity IDE suite and behaves like a
terminal UI. Spawned with ordinary pipes (``asyncio.create_subprocess_exec`` /
``subprocess.PIPE``) it detects ``!isatty(stdout)`` and emits **zero bytes** —
the brain then sees no answer. Driven over a ConPTY/PTY it renders the answer
wrapped in ANSI control sequences + window-title OSC frames; we read that,
strip the terminal noise, and recover the plain answer.

Cross-platform via the existing AD-6 PTY seam (:func:`jarvis.terminal.backend.
make_pty_backend` — ConPTY/pywinpty on Windows, ptyprocess on POSIX). On a host
with no PTY backend (headless VPS) the seam returns a ``NullPtyBackend`` whose
``spawn`` raises; we surface that as a clean ``error`` result so the brain falls
back to the next provider instead of crashing (CLOUD.md graceful-degradation).

Google ToS (hard): we only ever drive the official binary; the stored OAuth
token is never read to make our own HTTP request.
"""
from __future__ import annotations

import ntpath
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass

# A wide terminal minimizes hard-wrap artifacts in multi-line answers (agy
# renders to the PTY width). The brain's answers are short; the worker captures
# its result from the git diff, not this text, so wrapping there is harmless.
_COLS = 200
_ROWS = 50
_READ_SIZE = 8192
_POLL_S = 0.05

# OSC = Operating System Command (window titles): ESC ] ... terminated by BEL
# (\x07) or ST (ESC \). agy emits these for every npm/cmd subprocess it spawns.
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# CSI = Control Sequence Introducer: ESC [ params intermediates final.
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Other 2-byte ESC sequences (charset select, keypad mode, lone ESC at EOF).
_ESC_RE = re.compile(r"\x1b[()][AB0-2]|\x1b[=>]|\x1b")
# Control chars to drop — everything except TAB (\x09), LF (\x0a), CR (\x0d).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Braille glyphs (U+2800-U+28FF) = agy's progress spinner. It rewrites the same
# line via a leading CR every frame ("\r⠋ Thinking...\r⠙ Thinking..."); a stray
# glyph may also survive on the final visible line. Never part of a real answer.
_SPINNER_RE = re.compile(r"[⠀-⣿]")


def strip_terminal_noise(raw: str) -> str:
    """Remove ANSI/OSC/CSI control sequences and stray control bytes.

    Preserves printable text plus TAB/LF/CR; the answer text survives intact.
    """
    out = _OSC_RE.sub("", raw)
    out = _CSI_RE.sub("", out)
    out = _ESC_RE.sub("", out)
    out = _CTRL_RE.sub("", out)
    return out


def extract_cli_answer(raw: str) -> str:
    """Recover the plain answer text from a PTY-rendered CLI transcript.

    Strips terminal noise, honors bare carriage returns as line-overwrites
    (terminal semantics), drops the resulting blank/spinner lines, and trims —
    leaving the model's answer (single- or multi-line).

    The CR handling is what removes agy's progress spinner: agy renders it by
    repeatedly rewriting the same physical line with a leading CR
    ("\\r⠋ Fetching...\\r⠙ Fetching...\\r<answer>"), so only the text after the
    last CR on a line survives — the transient "Fetching…"/"Thinking…" frames
    are overwritten by the real answer and never leak into the spoken text.
    """
    cleaned = strip_terminal_noise(raw)
    cleaned = cleaned.replace("\r\n", "\n")  # CRLF first, so it stays a newline
    lines: list[str] = []
    for physical in cleaned.split("\n"):
        # Honor bare-CR overwrites: the last non-empty CR segment is what stays
        # on screen (a trailing lone CR overwrites nothing).
        segments = physical.split("\r")
        visible = next((s for s in reversed(segments) if s.strip()), "")
        visible = _SPINNER_RE.sub("", visible)  # drop any leftover spinner glyph
        if visible.strip():
            lines.append(visible.rstrip())
    return "\n".join(lines).strip()


def repair_agy_path(
    path: str | None,
    *,
    is_windows: bool = sys.platform == "win32",
    system_root: str | None = None,
    node_dir: str | None = None,
) -> str:
    """Ensure the standard Windows system dirs (and optionally Node) are on PATH.

    agy spawns ``cmd.exe`` + ``npm`` internally (it boots MCP servers even in
    ``--print`` mode). Launched from a degraded environment whose PATH lacks
    ``System32`` (forensic 2026-06-20: jarvis started by an agent runtime →
    ``chcp not recognized``), those inner spawns fail. We prepend the missing
    standard dirs idempotently. No-op on POSIX (chcp/cmd have no analogue and
    Node resolves via the usual prefixes).
    """
    if not is_windows:
        return path or ""
    root = system_root or os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    needed = [
        ntpath.join(root, "System32"),
        ntpath.join(root, "System32", "Wbem"),
        root,
    ]
    if node_dir:
        needed.append(node_dir)
    parts = [p for p in (path or "").split(";") if p]
    have = {p.rstrip("\\/").lower() for p in parts}
    prefix = [d for d in needed if d.rstrip("\\/").lower() not in have]
    if not prefix:
        return path or ""
    return ";".join([*prefix, *parts])


@dataclass(frozen=True)
class PtyRunResult:
    """Outcome of one PTY-driven CLI run."""

    text: str  # extracted, cleaned answer ("" when none / on error)
    raw: str  # full raw terminal transcript (diagnostics)
    exit_status: int | None
    timed_out: bool
    error: str | None  # set when no PTY backend / spawn failed (else None)


def run_cli_over_pty(
    argv: tuple[str, ...],
    *,
    timeout_s: float,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    backend: object | None = None,
    on_spawn: Callable[[int], None] | None = None,
    _now: Callable[[], float] = time.monotonic,
    _sleep: Callable[[float], None] = time.sleep,
) -> PtyRunResult:
    """Spawn ``argv`` over a PTY, read until exit/timeout, return the answer.

    ``on_spawn`` (when given) is invoked once with the child PID right after the
    spawn — the Phase-6 worker uses it to assign the PID to its kill-on-crash
    Job Object. ``backend`` / ``_now`` / ``_sleep`` are injectable seams for
    tests. A missing PTY backend (or a spawn failure) returns a result with
    ``error`` set and ``text=""`` — never raises — so the caller can fall back
    cleanly.
    """
    if backend is None:
        from jarvis.terminal.backend import make_pty_backend

        backend = make_pty_backend()

    try:
        handle = backend.spawn(  # type: ignore[attr-defined]
            argv=tuple(argv), cwd=cwd, cols=_COLS, rows=_ROWS, env=env,
        )
    except RuntimeError as exc:
        return PtyRunResult(text="", raw="", exit_status=None, timed_out=False, error=str(exc))

    if on_spawn is not None:
        with suppress(Exception):
            on_spawn(int(getattr(handle, "pid", 0) or 0))

    chunks: list[str] = []
    deadline = _now() + timeout_s
    timed_out = False
    try:
        while True:
            if _now() >= deadline:
                timed_out = True
                break
            try:
                data = handle.read(_READ_SIZE)
            except EOFError:
                break
            if data:
                chunks.append(data)
            elif not handle.isalive():
                break
            else:
                _sleep(_POLL_S)
    finally:
        if handle.isalive():
            with suppress(Exception):
                handle.terminate(force=True)

    raw = "".join(chunks)
    exit_status = handle.exitstatus
    return PtyRunResult(
        text=extract_cli_answer(raw),
        raw=raw,
        exit_status=exit_status,
        timed_out=timed_out,
        error=None,
    )


__all__ = [
    "PtyRunResult",
    "extract_cli_answer",
    "repair_agy_path",
    "run_cli_over_pty",
    "strip_terminal_noise",
]
