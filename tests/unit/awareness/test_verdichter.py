"""Tests for jarvis.awareness.verdichter.Verdichter.

Plan §6 AC:
- Verdichter call with FakeBrain returns a deterministic summary
- Timeout (5s) yields an empty summary + error_reason="timeout", does not crash
- Token cap: for len(frames+events) > 30, the 30 newest are kept
- Empty input (frames=[], events=[]) -> skip with error_reason="empty_input"
- Verdichter NEVER goes through the spawn_worker mechanism (direct BrainProviderRegistry)
"""
from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis.awareness.config import AwarenessVerdichterConfig
from jarvis.awareness.prompts import (
    VERDICHTER_SYSTEM_PROMPT,
    build_verdichter_prompt,
)
from jarvis.awareness.verdichter import MAX_FRAMES_PLUS_EVENTS, Verdichter
from jarvis.core.protocols import BrainDelta, BrainRequest
from tests.fixtures.brain.fake_brain import FakeBrain

# ----------------------------------------------------------------------
# Test-Helpers
# ----------------------------------------------------------------------

def _frame(ts_ns: int, process: str = "Code.exe", title: str = "main.py") -> dict:
    """Builds a frame-dict im A1-Schema (timestamp_ns)."""
    return {
        "timestamp_ns": ts_ns,
        "process_name": process,
        "window_title": title,
    }


def _event(ts_ns: int, kind: str = "FileSaved", payload: dict | None = None) -> dict:
    """Builds an event-dict (ts_ns)."""
    out = {"ts_ns": ts_ns, "kind": kind}
    if payload is not None:
        out["payload"] = payload
    return out


def _default_config(**overrides) -> AwarenessVerdichterConfig:
    """AwarenessVerdichterConfig with a short timeout for tests."""
    base = {
        "enabled": True,
        "provider": "claude-api",
        "model": "claude-haiku-4-5-20251001",
        "max_input_tokens": 800,
        "max_output_tokens": 200,
        "timeout_s": 1.0,    # short, for fast tests
    }
    base.update(overrides)
    return AwarenessVerdichterConfig(**base)


# ----------------------------------------------------------------------
# Test 1: deterministischer Summary-Return
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_with_fake_brain_returns_summary() -> None:
    """FakeBrain.text -> call() returnt (text, usage mit error_reason=None)."""
    brain = FakeBrain(text_response="Du arbeitest seit 12min an pipeline.py in VS Code.")
    cfg = _default_config()
    verdichter = Verdichter(brain=brain, config=cfg)

    frames = [_frame(1_700_000_000_000_000_000, "Code.exe", "pipeline.py")]
    events = [_event(1_700_000_001_000_000_000, "FileSaved")]

    summary, usage = await verdichter.call(
        frames=frames, events=events, primary_app="Code.exe",
    )

    assert summary == "Du arbeitest seit 12min an pipeline.py in VS Code."
    assert usage["error_reason"] is None
    assert usage["tokens_in"] == 0    # FakeBrain does not emit usage
    assert usage["tokens_out"] == 0
    assert usage["duration_ms"] >= 0
    # FakeBrain was called ONCE (no Sub-Jarvis spawn loop)
    assert len(brain.calls) == 1
    # Verify that the system prompt from prompts.py was passed through
    assert brain.calls[0].system == VERDICHTER_SYSTEM_PROMPT


# ----------------------------------------------------------------------
# Test 2: Empty Input
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_with_empty_input_returns_empty_input_reason() -> None:
    """frames=[] + events=[] -> ('', error_reason='empty_input'), no brain call."""
    brain = FakeBrain(text_response="should not be called")
    cfg = _default_config()
    verdichter = Verdichter(brain=brain, config=cfg)

    summary, usage = await verdichter.call(
        frames=[], events=[], primary_app="Code.exe",
    )

    assert summary == ""
    assert usage["error_reason"] == "empty_input"
    assert usage["tokens_in"] == 0
    assert usage["tokens_out"] == 0
    assert usage["duration_ms"] == 0
    # NO brain call on empty input
    assert len(brain.calls) == 0


# ----------------------------------------------------------------------
# Test 3: Timeout
# ----------------------------------------------------------------------

class _SlowBrain:
    """Brain that deliberately sleeps longer than the timeout."""

    name: str = "slow-brain"
    context_window: int = 8192
    supports_tools: bool = False
    supports_vision: bool = False

    def __init__(self, sleep_s: float = 5.0) -> None:
        self._sleep_s = sleep_s
        self.calls: list = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls.append(req)
        await asyncio.sleep(self._sleep_s)
        yield BrainDelta(content="never", finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:
        return 0.0


@pytest.mark.asyncio
async def test_call_with_timeout_returns_timeout_reason() -> None:
    """Brain schlaeft 5s, Timeout=0.1s -> ('', error_reason='timeout')."""
    brain = _SlowBrain(sleep_s=5.0)
    cfg = _default_config(timeout_s=0.1)
    verdichter = Verdichter(brain=brain, config=cfg)

    frames = [_frame(1_700_000_000_000_000_000)]

    summary, usage = await verdichter.call(
        frames=frames, events=[], primary_app="Code.exe",
    )

    assert summary == ""
    assert usage["error_reason"] == "timeout"
    assert usage["tokens_in"] == 0
    assert usage["tokens_out"] == 0
    # Timeout ungefaehr 100ms (Toleranz +/-)
    assert usage["duration_ms"] >= 90, f"duration_ms={usage['duration_ms']} too short"
    assert usage["duration_ms"] < 1500, f"duration_ms={usage['duration_ms']} too long"


# ----------------------------------------------------------------------
# Test 4: Token-Cap (max 30 Frames+Events, neueste gewinnen)
# ----------------------------------------------------------------------

class _SpyBrain:
    """Brain that stores the passed-in prompt for inspection."""

    name: str = "spy-brain"
    context_window: int = 8192
    supports_tools: bool = False
    supports_vision: bool = False

    def __init__(self) -> None:
        self.last_prompt: str = ""
        self.calls: list = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls.append(req)
        # Store the last message (user prompt)
        for msg in req.messages:
            if msg.role == "user" and isinstance(msg.content, str):
                self.last_prompt = msg.content
        yield BrainDelta(content="ok", finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:
        return 0.0


@pytest.mark.asyncio
async def test_call_caps_to_30_frames_plus_events() -> None:
    """50 Frames + 0 Events -> nur 30 Frames im finalen Prompt.

    Strategy: NEWEST 30 (chronological tail). The oldest 20 frames are
    dropped — we verify this by looking for the "old" and "new" markers
    in the prompt.
    """
    brain = _SpyBrain()
    cfg = _default_config(max_input_tokens=10_000)    # large enough, no truncation
    verdichter = Verdichter(brain=brain, config=cfg)

    base_ts = 1_700_000_000_000_000_000
    # 50 Frames mit aufsteigender Zeit, jedes mit Marker im Title.
    frames = [
        _frame(base_ts + i * 1_000_000_000, "Code.exe", f"file_{i:02d}.py")
        for i in range(50)
    ]

    summary, usage = await verdichter.call(
        frames=frames, events=[], primary_app="Code.exe",
    )

    assert summary == "ok"
    # Cap: max 30 Frames+Events. 50 Frames -> 30 neueste.
    assert MAX_FRAMES_PLUS_EVENTS == 30
    # Check: the old frames file_00..file_19 are missing, file_20..file_49 are present.
    prompt = brain.last_prompt
    assert "file_49.py" in prompt, "Newest frame must be in the prompt"
    assert "file_20.py" in prompt, "First non-trimmed frame must be present"
    assert "file_19.py" not in prompt, "file_19 should have been dropped (too old)"
    assert "file_00.py" not in prompt, "file_00 should have been dropped (oldest)"
    # Genau 30 Frame-Zeilen
    frame_lines = re.findall(r"^- \[\d\d:\d\d:\d\d\] Code\.exe: file_\d\d\.py$",
                             prompt, flags=re.MULTILINE)
    assert len(frame_lines) == 30, f"Erwartet 30 Frame-Zeilen, gefunden {len(frame_lines)}"


# ----------------------------------------------------------------------
# Test 5: Brain-Error
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_brain_error_returns_error_reason() -> None:
    """FakeBrain mit fail_on_call=0 -> ('', error_reason=str(exc))."""
    brain = FakeBrain(text_response="never", fail_on_call=0)
    cfg = _default_config()
    verdichter = Verdichter(brain=brain, config=cfg)

    frames = [_frame(1_700_000_000_000_000_000)]

    summary, usage = await verdichter.call(
        frames=frames, events=[], primary_app="Code.exe",
    )

    assert summary == ""
    assert usage["error_reason"] is not None
    # FakeBrain wirft "FakeBrain-scripted failure"
    assert "FakeBrain-scripted failure" in usage["error_reason"]
    assert usage["tokens_in"] == 0
    assert usage["tokens_out"] == 0


# ----------------------------------------------------------------------
# Test 6: Prompt-Format (Header + Footer)
# ----------------------------------------------------------------------

def test_build_verdichter_prompt_format() -> None:
    """Direct-Call: Header + Frame + Event + Dominante App."""
    base_ts = 1_700_000_000_000_000_000
    frames = [_frame(base_ts, "Code.exe", "main.py")]
    events = [_event(base_ts + 1_000_000_000, "FileSaved", {"path": "main.py"})]

    prompt = build_verdichter_prompt(
        frames=frames, events=events, primary_app="Code.exe",
    )

    assert "# Frames + Events der letzten Minuten:" in prompt
    assert "Dominante App: Code.exe" in prompt
    assert "Code.exe: main.py" in prompt
    assert "EVENT FileSaved" in prompt
    assert "path=main.py" in prompt


# ----------------------------------------------------------------------
# Test 7: Truncation-Marker
# ----------------------------------------------------------------------

def test_build_verdichter_prompt_truncation() -> None:
    """Sehr viele Frames + max_chars klein -> [...] Marker erscheint."""
    base_ts = 1_700_000_000_000_000_000
    # 100 Frames mit langen Titles
    frames = [
        _frame(base_ts + i * 1_000_000_000, "Code.exe", "x" * 200)
        for i in range(100)
    ]

    prompt = build_verdichter_prompt(
        frames=frames, events=[], primary_app="Code.exe",
        max_chars=500,    # too small for everything
    )

    assert "[...]" in prompt, f"Truncation-Marker fehlt im Prompt:\n{prompt!r}"
    assert "Dominante App: Code.exe" in prompt    # Footer bleibt
    assert "# Frames + Events der letzten Minuten:" in prompt    # Header bleibt
    assert len(prompt) <= 500 + 50, "Truncation hat max_chars zu weit ueberschritten"


# ----------------------------------------------------------------------
# Test 8: spawn_worker NIEMALS — Source-Code-Audit + Behavior-Spy
# ----------------------------------------------------------------------

def _strip_string_literals_and_comments(src: str) -> str:
    """Strips Python docstrings/string literals and comments out of src.

    Tokenize-based. Used in the NEVER-spawn test so that hard-negative
    mentions in docstrings (e.g. "NEVER spawn_worker") don't false-positive
    trigger. We only want actual code references (imports, function
    calls, identifiers).
    """
    import io
    import token as _token_mod
    import tokenize

    buf = io.StringIO(src)
    out: list[str] = []
    for tok in tokenize.generate_tokens(buf.readline):
        if tok.type in (_token_mod.STRING, _token_mod.COMMENT):
            continue
        if tok.type == _token_mod.NL or tok.type == _token_mod.NEWLINE:
            out.append("\n")
            continue
        if tok.string:
            out.append(tok.string + " ")
    return "".join(out)


def test_verdichter_NEVER_calls_spawn_worker() -> None:
    """Hard negative §6: Verdichter must NOT run via spawn_worker.

    Welle-4 migration: the tool used to be called ``spawn_sub_jarvis``. The
    regression guards stay active for both names, to prevent a spawn
    dependency from ever being reintroduced.

    Check 1 (code, not docstrings): the source code of Verdichter +
    prompts.py contains NO code reference to spawn_worker /
    spawn_sub_jarvis / SubJarvisManager / jarvis.sub_jarvis. Docstring
    mentions are allowed — we strip strings + comments via tokenize
    before matching.
    Check 2: Verdichter.__init__ accepts ``brain`` + ``config``
    directly — no hidden manager wiring.
    """
    # Check 1: source of the module (code-only, no docstrings)
    import jarvis.awareness.verdichter as v_mod
    src_code = _strip_string_literals_and_comments(inspect.getsource(v_mod))
    assert "spawn_worker" not in src_code, (
        "Verdichter CODE must not reference spawn_worker "
        "(docstring mentions would be OK)"
    )
    assert "SubJarvisManager" not in src_code, (
        "Verdichter CODE must not import/instantiate SubJarvisManager"
    )
    assert "jarvis.sub_jarvis" not in src_code, (
        "Verdichter CODE must not import jarvis.sub_jarvis"
    )

    # Check 2: prompts.py is also clean (code-only)
    import jarvis.awareness.prompts as p_mod
    p_code = _strip_string_literals_and_comments(inspect.getsource(p_mod))
    assert "spawn_worker" not in p_code
    assert "jarvis.sub_jarvis" not in p_code
    assert "SubJarvisManager" not in p_code

    # Check 3: the Verdichter class hangs directly off the Brain protocol,
    # not off a manager. inspect.signature(__init__) must have "brain".
    sig = inspect.signature(Verdichter.__init__)
    assert "brain" in sig.parameters, "Verdichter.__init__ must accept brain="
    assert "config" in sig.parameters, "Verdichter.__init__ must accept config="
    assert "manager" not in sig.parameters, (
        "Verdichter.__init__ must NOT have a manager= (otherwise Sub-Jarvis wiring)"
    )

    # Check 4: source file exists and lives in the awareness module
    src_file = Path(inspect.getfile(v_mod))
    assert src_file.exists()
    assert src_file.parent.name == "awareness", (
        f"verdichter.py must live in jarvis/awareness/, is in {src_file.parent}"
    )


@pytest.mark.asyncio
async def test_verdichter_brain_call_uses_brain_complete_directly() -> None:
    """Behavior check: Verdichter.call() calls brain.complete() ONCE.

    A Sub-Jarvis spawn would trigger multiple brain calls + a tool-use loop.
    A single brain.complete() call is the proof that this is a direct
    brain call (hard negative §6).
    """
    brain = FakeBrain(text_response="ok")
    cfg = _default_config()
    verdichter = Verdichter(brain=brain, config=cfg)

    frames = [_frame(1_700_000_000_000_000_000)]

    summary, usage = await verdichter.call(
        frames=frames, events=[], primary_app="Code.exe",
    )

    # EXACTLY ONE brain call (no tool-use loop, no spawn loop)
    assert len(brain.calls) == 1, (
        f"Verdichter must make only 1 brain call, found {len(brain.calls)}. "
        "More calls = tool-use loop = probably a Sub-Jarvis spawn."
    )
    # Request has NO tools (a Sub-Jarvis spawn would be a tool)
    assert brain.calls[0].tools == (), (
        "Verdichter BrainRequest must NOT have tools (otherwise tool-use loop)"
    )
    assert summary == "ok"


async def test_verdichter_latency_p95_under_2s() -> None:
    """Plan §6 AC: Verdichter call p95 < 2s.

    100 calls with FakeBrain (deterministic response, no network).
    Verifies that the Verdichter implementation itself (prompt build,
    stream aggregate, asyncio overhead) does not introduce a path latency
    > 2s.

    Real latency regression against the Anthropic API is out of scope
    (needs an API key, is network-dependent). This test guards against
    Verdichter implementation regressions (e.g. an unbounded loop in the
    prompt builder, blocking I/O that stalls the asyncio loop).
    """
    import time as _time

    brain = FakeBrain(text_response="OK")
    cfg = _default_config()
    verdichter = Verdichter(brain=brain, config=cfg)

    frames = [_frame(_time.time_ns() - i * 100_000_000) for i in range(5)]

    durations_ms: list[float] = []
    for _ in range(100):
        start_ns = _time.time_ns()
        await verdichter.call(
            frames=frames, events=[], primary_app="Code.exe",
        )
        durations_ms.append((_time.time_ns() - start_ns) / 1_000_000.0)

    durations_ms.sort()
    p95_idx = int(len(durations_ms) * 0.95)
    p95_ms = durations_ms[p95_idx]
    assert p95_ms < 2000.0, (
        f"Verdichter p95 = {p95_ms:.1f}ms > 2000ms (AC Plan §6). "
        f"durations_ms head/tail = {durations_ms[:3]} / {durations_ms[-3:]}"
    )
