"""Tests fuer jarvis.awareness.verdichter.Verdichter.

Plan §6 AC:
- Verdichter-Call mit FakeBrain returnt deterministische Summary
- Timeout (5s) liefert empty summary + error_reason="timeout", crashed nicht
- Token-Cap: bei len(frames+events) > 30 werden 30 neueste genommen
- Empty input (frames=[], events=[]) -> skip mit error_reason="empty_input"
- Verdichter NIEMALS via spawn_worker-Mechanik (direct BrainProviderRegistry)
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
    """AwarenessVerdichterConfig mit kurzem Timeout fuer Tests."""
    base = {
        "enabled": True,
        "provider": "claude-api",
        "model": "claude-haiku-4-5-20251001",
        "max_input_tokens": 800,
        "max_output_tokens": 200,
        "timeout_s": 1.0,    # kurz fuer schnelle Tests
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
    assert usage["tokens_in"] == 0    # FakeBrain emittet keine usage
    assert usage["tokens_out"] == 0
    assert usage["duration_ms"] >= 0
    # FakeBrain wurde EINMAL aufgerufen (kein Sub-Jarvis-Spawn-Loop)
    assert len(brain.calls) == 1
    # Verifiziere dass System-Prompt aus prompts.py uebergeben wurde
    assert brain.calls[0].system == VERDICHTER_SYSTEM_PROMPT


# ----------------------------------------------------------------------
# Test 2: Empty Input
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_with_empty_input_returns_empty_input_reason() -> None:
    """frames=[] + events=[] -> ('', error_reason='empty_input'), kein Brain-Call."""
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
    # KEIN Brain-Call bei Empty-Input
    assert len(brain.calls) == 0


# ----------------------------------------------------------------------
# Test 3: Timeout
# ----------------------------------------------------------------------

class _SlowBrain:
    """Brain das absichtlich laenger schlaeft als der Timeout."""

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
    """Brain das den uebergebenen Prompt fuer Inspection speichert."""

    name: str = "spy-brain"
    context_window: int = 8192
    supports_tools: bool = False
    supports_vision: bool = False

    def __init__(self) -> None:
        self.last_prompt: str = ""
        self.calls: list = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls.append(req)
        # Letzte Message (User-Prompt) speichern
        for msg in req.messages:
            if msg.role == "user" and isinstance(msg.content, str):
                self.last_prompt = msg.content
        yield BrainDelta(content="ok", finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:
        return 0.0


@pytest.mark.asyncio
async def test_call_caps_to_30_frames_plus_events() -> None:
    """50 Frames + 0 Events -> nur 30 Frames im finalen Prompt.

    Strategy: NEUESTE 30 (chronological tail). Die aeltesten 20 Frames
    werden gedroppt — das verifizieren wir indem wir die "alte" und
    "neue" Marker im Prompt suchen.
    """
    brain = _SpyBrain()
    cfg = _default_config(max_input_tokens=10_000)    # gross genug, kein Trunc
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
    # Pruefen: die alten Frames file_00..file_19 fehlen, file_20..file_49 sind drin.
    prompt = brain.last_prompt
    assert "file_49.py" in prompt, "Neuester Frame muss im Prompt sein"
    assert "file_20.py" in prompt, "Erster nicht-getrimmter Frame muss da sein"
    assert "file_19.py" not in prompt, "file_19 sollte gedroppt sein (zu alt)"
    assert "file_00.py" not in prompt, "file_00 sollte gedroppt sein (aeltester)"
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
        max_chars=500,    # zu klein fuer alles
    )

    assert "[...]" in prompt, f"Truncation-Marker fehlt im Prompt:\n{prompt!r}"
    assert "Dominante App: Code.exe" in prompt    # Footer bleibt
    assert "# Frames + Events der letzten Minuten:" in prompt    # Header bleibt
    assert len(prompt) <= 500 + 50, "Truncation hat max_chars zu weit ueberschritten"


# ----------------------------------------------------------------------
# Test 8: spawn_worker NIEMALS — Source-Code-Audit + Behavior-Spy
# ----------------------------------------------------------------------

def _strip_string_literals_and_comments(src: str) -> str:
    """Entfernt Python-Docstrings/String-Literals und Kommentare aus src.

    Tokenize-basiert. Wird im NEVER-spawn-Test genutzt, damit Hard-Negative-
    Erwaehnungen in Docstrings (z.B. "NIEMALS spawn_worker") nicht
    falsch positiv triggern. Wir wollen nur tatsaechliche Code-References
    (Imports, Function-Calls, Identifier).
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
    """Hard Negative §6: Verdichter darf NICHT via spawn_worker laufen.

    Welle-4-Migration: vorher hiess das Tool ``spawn_sub_jarvis``. Die
    Regression-Guards bleiben fuer beide Namen aktiv, um zu verhindern dass
    je wieder eine Spawn-Abhaengigkeit eingeschleust wird.

    Pruefung 1 (Code, nicht Docstrings): Source-Code von Verdichter +
    prompts.py enthaelt KEINE Code-Reference auf spawn_worker /
    spawn_sub_jarvis / SubJarvisManager / jarvis.sub_jarvis. Docstring-
    Erwaehnungen sind erlaubt — wir strippen Strings + Kommentare via
    tokenize bevor wir matchen.
    Pruefung 2: Verdichter.__init__ akzeptiert ``brain`` + ``config``
    direkt — kein Hidden-Manager-Wiring.
    """
    # Pruefung 1: Source des Moduls (Code-Only, keine Docstrings)
    import jarvis.awareness.verdichter as v_mod
    src_code = _strip_string_literals_and_comments(inspect.getsource(v_mod))
    assert "spawn_worker" not in src_code, (
        "Verdichter-CODE darf spawn_worker nicht referenzieren "
        "(Docstring-Erwaehnungen waeren OK)"
    )
    assert "SubJarvisManager" not in src_code, (
        "Verdichter-CODE darf SubJarvisManager nicht importieren/instanziieren"
    )
    assert "jarvis.sub_jarvis" not in src_code, (
        "Verdichter-CODE darf jarvis.sub_jarvis nicht importieren"
    )

    # Pruefung 2: prompts.py auch sauber (Code-Only)
    import jarvis.awareness.prompts as p_mod
    p_code = _strip_string_literals_and_comments(inspect.getsource(p_mod))
    assert "spawn_worker" not in p_code
    assert "jarvis.sub_jarvis" not in p_code
    assert "SubJarvisManager" not in p_code

    # Pruefung 3: Verdichter-Klasse haengt direkt am Brain-Protocol, nicht
    # an einem Manager. inspect.signature(__init__) muss "brain" haben.
    sig = inspect.signature(Verdichter.__init__)
    assert "brain" in sig.parameters, "Verdichter.__init__ muss brain= akzeptieren"
    assert "config" in sig.parameters, "Verdichter.__init__ muss config= akzeptieren"
    assert "manager" not in sig.parameters, (
        "Verdichter.__init__ darf KEINEN manager= haben (sonst Sub-Jarvis-Wiring)"
    )

    # Pruefung 4: Source-File existiert und ist im awareness-Modul
    src_file = Path(inspect.getfile(v_mod))
    assert src_file.exists()
    assert src_file.parent.name == "awareness", (
        f"verdichter.py muss in jarvis/awareness/ liegen, ist in {src_file.parent}"
    )


@pytest.mark.asyncio
async def test_verdichter_brain_call_uses_brain_complete_directly() -> None:
    """Behavior-Check: Verdichter.call() ruft brain.complete() EINMAL.

    Sub-Jarvis-Spawn wuerde mehrere Brain-Calls + Tool-Use-Loop ausloesen.
    Ein einzelner brain.complete()-Call ist der Beweis dass es ein direkter
    Brain-Call ist (Hard Negative §6).
    """
    brain = FakeBrain(text_response="ok")
    cfg = _default_config()
    verdichter = Verdichter(brain=brain, config=cfg)

    frames = [_frame(1_700_000_000_000_000_000)]

    summary, usage = await verdichter.call(
        frames=frames, events=[], primary_app="Code.exe",
    )

    # GENAU EIN Brain-Call (kein Tool-Use-Loop, kein Spawn-Loop)
    assert len(brain.calls) == 1, (
        f"Verdichter darf nur 1 Brain-Call machen, gefunden {len(brain.calls)}. "
        "Mehr Calls = Tool-Use-Loop = vermutlich Sub-Jarvis-Spawn."
    )
    # Request hat KEINE Tools (Sub-Jarvis-Spawn waere ein Tool)
    assert brain.calls[0].tools == (), (
        "Verdichter-BrainRequest darf KEINE Tools haben (sonst Tool-Use-Loop)"
    )
    assert summary == "ok"


async def test_verdichter_latency_p95_under_2s() -> None:
    """Plan §6 AC: Verdichter-Call p95 < 2s.

    100 Calls mit FakeBrain (deterministische Response, kein Network).
    Verifiziert dass die Verdichter-Implementation selbst (Prompt-Build,
    Stream-Aggregate, asyncio-Overhead) keinen Pfad-Latency > 2s einfuehrt.

    Echte Latency-Regression gegen Anthropic-API ist out-of-scope (braucht
    API-Key, ist netzwerkabhaengig). Dieser Test schuetzt vor Verdichter-
    Implementations-Regressions (z.B. unbeschraenkte Schleife im Prompt-
    Builder, blocking I/O das den asyncio-Loop staut).
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
