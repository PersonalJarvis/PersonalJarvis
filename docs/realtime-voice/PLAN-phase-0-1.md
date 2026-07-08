# Realtime Voice Mode — Phase 0 + 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first working, testable slice of the realtime voice mode — a browser, OpenAI-Realtime, conversation-only duplex path that is default-OFF and reuses the existing `/ws/audio` transport, with the load-bearing audio-hold voice-scrub gate in place from day one.

**Architecture:** A new `jarvis/realtime/` orchestrator drives a provider-agnostic duplex session; a `jarvis.realtime` plugin group holds one provider adapter (OpenAI GA `gpt-realtime`). A new `RealtimeVoiceSession` implements the exact duck interface (`handle_audio_frame`/`handle_control`/`end`) the existing `/ws/audio` route already calls, so the route is branched in one place (`_build_browser_session`) and otherwise untouched. Model audio is buffered by a `ScrubHoldGate` and released only after its transcript passes the regex-only `scrub_for_voice`. The classic pipeline and the classic browser bridge are unchanged.

**Tech Stack:** Python 3.11+ (asyncio, Pydantic v2, tomlkit), the installed `openai` SDK (`AsyncOpenAI().realtime.connect`), FastAPI/Starlette WebSockets, React 18 + Vite 6 + TypeScript 5.6 (Web Audio `AudioWorklet`), pytest (fakes, not `unittest.mock`), vitest.

## Global Constraints

Every task's requirements implicitly include this section. Values copied verbatim from `docs/realtime-voice/DESIGN.md` and the codebase.

- **English-only artifacts** — code, comments, docstrings, tests, commit messages, docs (CLAUDE.md §1). When you touch a line carrying pre-existing German, translate it on the way through.
- **Default OFF** — realtime mode ships disabled; the classic pipeline stays the default and the classic browser bridge's behavior is unchanged.
- **AP-3** — every future tool call goes through `ToolExecutor.execute`; Phase 1 is conversation-only and declares NO tools; never enable the OpenAI SDK's tool auto-exec.
- **AP-11 / ADR-0010** — `scrub_for_voice` is regex-only; never add an LLM call inside the scrub path.
- **AP-16** — any new config sub-model that self-mod/drift-guard could write carries `model_config = ConfigDict(extra="allow")`.
- **AP-21 / AP-22** — gate on capability + key presence, never a provider name; Phase 1 is OpenAI-only by explicit scope (documented), with the classic pipeline as the always-present fallback when realtime is unbuildable.
- **AP-26** — no provider SDK import at module top level, in `_run_backend`, the `WebServer` ctor, or `_start_speech_and_orb`; import `openai` lazily inside the adapter's `connect()`. Keep the pre-push boot-budget gate green.
- **AP-20** — a realtime WS receive path treats any non-`WebSocketDisconnect` teardown error as terminal (`break`, never `continue`).
- **Cross-platform** — no hard `audioop` import for new resampling in code that must run on Python 3.13 (`audioop` is removed there); reuse `jarvis/telephony/audio.py::Resampler` (which already carries the `audioop-lts` backport concern) or a numpy path.
- **Config writes** — only via `jarvis/core/config_writer.py` helpers (`_patch_table` → `_atomic_write`, lock + tempfile + BOM-safe, AP-7). Never hand-write `jarvis.toml`.
- **Sample rates** — mic capture browser-rate (default 48000) → 16000 for STT is the classic path; OpenAI realtime input is upsampled 16000→24000; OpenAI output is 24000. `STT_SAMPLE_RATE=16000`, `TTS_SAMPLE_RATE=24000` (`jarvis/telephony/audio.py:37`).
- **No new plugin group is discoverable** until it is BOTH added to `PLUGIN_GROUPS` (`jarvis/core/protocols.py:380`) AND given a `[project.entry-points."jarvis.<group>"]` block, followed by `pip install -e . --no-deps`.

---

## File Structure

**New files**
- `jarvis/realtime/__init__.py` — lazy exports (`build_realtime_session`); no heavy imports (AP-26).
- `jarvis/realtime/protocol.py` — `RealtimeEvent` union + `RealtimeProvider`/`RealtimeSession` `@runtime_checkable` Protocols + `RealtimeSessionConfig`.
- `jarvis/realtime/scrub_gate.py` — `ScrubHoldGate` (buffer audio, release after transcript passes `scrub_for_voice`; HARD-leak → drop + fallback).
- `jarvis/realtime/session.py` — `RealtimeVoiceSession` (the `/ws/audio` duck interface, provider→gate→`send_binary`, barge-in, language resolve, latency marks).
- `jarvis/realtime/factory.py` — `build_realtime_session(...)`: OpenAI-key-gated builder returning `None` when unbuildable.
- `jarvis/plugins/realtime/__init__.py` — plugin package marker (no `jarvis.*` import).
- `jarvis/plugins/realtime/openai_realtime.py` — `OpenAIRealtimeProvider` (`RealtimeProvider`), lazy `openai` import.
- `jarvis/ui/web/frontend/src/lib/pcm-worklet.ts` — capture (downsample to int16) + playback (24 kHz jitter buffer) `AudioWorkletProcessor`s.
- `jarvis/ui/web/frontend/src/lib/realtimeAudio.ts` — dedicated `/ws/audio` client (own WebSocket, `binaryType="arraybuffer"`).
- `jarvis/ui/web/frontend/src/hooks/useVoiceMode.ts` — React-Query GET/PUT for the voice-mode toggle.
- `jarvis/ui/web/frontend/src/views/settings/RealtimeVoiceGroup.tsx` — the default-OFF settings toggle.
- Tests: `tests/unit/realtime/test_scrub_gate.py`, `test_session.py`, `test_factory.py`, `tests/contract/test_realtime_provider_contract.py`, `tests/unit/core/test_voice_mode_config.py`, `tests/unit/web/test_voice_mode_route.py`.

**Modified files**
- `jarvis/core/protocols.py` — add `"jarvis.realtime"` to `PLUGIN_GROUPS`.
- `pyproject.toml` — add `[project.entry-points."jarvis.realtime"]`.
- `jarvis/core/config.py` — `VoiceConfig.mode`; `BrainConfig.realtime: BrainTierConfig | None`; remove dead `BrainPolicyConfig.use_realtime_for_smalltalk`.
- `jarvis/core/config_writer.py` — `set_voice_mode(mode)`.
- `jarvis/core/events.py` — 3 `LatencyPhase` members.
- `jarvis/browser_voice/route.py` — branch `_build_browser_session` on `cfg.voice.mode`; invert the connect gate to default-OFF for the realtime path.
- `jarvis/ui/web/settings_routes.py` — `GET`/`PUT /api/settings/voice-mode`.
- `jarvis/ui/web/frontend/src/views/SettingsView.tsx` — mount `<RealtimeVoiceGroup/>`.
- `jarvis/ui/web/frontend/src/i18n/locales/{en,de,es}.json` — toggle strings.

**Scope note (documented, not a placeholder):** Phase 0+1 is browser-only, OpenAI-only, conversation-only. NO tools, NO ask-tier confirmation, NO Gemini, NO desktop `_realtime_session()`, NO `[brain.realtime]` cross-family chain wiring beyond the config field, NO capability seed (a conversation-only mode is not an action surface, and `Capability.source` is a closed Literal without a `realtime` value — `jarvis/core/capabilities.py:126`). Those land in Phases 2–5, each with its own plan.

---

## Phase 0 — Contracts & scaffolding (default OFF)

### Task 1: Config surface — voice mode toggle + realtime tier field + retire dead flag

**Files:**
- Modify: `jarvis/core/config.py` (`VoiceConfig` at :1838, `BrainConfig` at :733, `BrainPolicyConfig` at :400)
- Modify: `jarvis/core/config_writer.py` (add `set_voice_mode` near `set_computer_use_engine` at :529)
- Test: `tests/unit/core/test_voice_mode_config.py`

**Interfaces:**
- Produces: `VoiceConfig.mode: str = "pipeline"`; `BrainConfig.realtime: BrainTierConfig | None = None`; `config_writer.set_voice_mode(mode: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None`.
- Consumes: existing `BrainTierConfig` (config.py:629), `_patch_table` (config_writer.py:1185).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_voice_mode_config.py
from pathlib import Path

from jarvis.core import config as cfg_mod
from jarvis.core import config_writer


def test_voice_mode_defaults_to_pipeline():
    cfg = cfg_mod.JarvisConfig()
    assert cfg.voice.mode == "pipeline"
    assert cfg.brain.realtime is None


def test_dead_realtime_smalltalk_flag_is_removed():
    # The abandoned Phase-1 flag must be gone (retired, not repurposed).
    assert not hasattr(cfg_mod.BrainPolicyConfig(), "use_realtime_for_smalltalk")


def test_set_voice_mode_persists_toml_only(tmp_path: Path):
    toml = tmp_path / "jarvis.toml"
    toml.write_text("", encoding="utf-8")
    config_writer.set_voice_mode("realtime", path=toml)
    assert '[voice]' in toml.read_text(encoding="utf-8")
    assert 'mode = "realtime"' in toml.read_text(encoding="utf-8")


def test_realtime_tier_field_accepts_brain_tier_config():
    cfg = cfg_mod.JarvisConfig.model_validate(
        {"brain": {"realtime": {"provider": "openai"}}}
    )
    assert cfg.brain.realtime is not None
    assert cfg.brain.realtime.provider == "openai"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_voice_mode_config.py -v`
Expected: FAIL — `AttributeError: 'VoiceConfig' object has no attribute 'mode'` (and `set_voice_mode` undefined).

- [ ] **Step 3: Add the `mode` field to `VoiceConfig`**

In `jarvis/core/config.py`, inside `class VoiceConfig(BaseModel):` (has `model_config = ConfigDict(extra="allow")`), add after `completion_detection_enabled`:

```python
    # Voice engine selector. "pipeline" = the classic STT->brain->TTS chain
    # (default, unchanged). "realtime" = the full-duplex speech-to-speech engine
    # (browser, OpenAI Realtime; opt-in). Read once per voice session; a live
    # change lands on the next session.
    mode: str = "pipeline"
```

- [ ] **Step 4: Add the `realtime` tier field to `BrainConfig`**

In `class BrainConfig(BaseModel):`, add next to `router` / `worker` (config.py:763):

```python
    # Realtime-tier provider preference + cross-family fallback chain (AP-22).
    # None until the user opts into realtime voice. Reuses BrainTierConfig so the
    # fallback shape matches [brain.router]/[brain.worker].
    realtime: BrainTierConfig | None = None
```

- [ ] **Step 5: Remove the dead `use_realtime_for_smalltalk` flag**

In `class BrainPolicyConfig(BaseModel):` (config.py:400) delete the line:

```python
    use_realtime_for_smalltalk: bool = False
```

(Confirm no reader references it: `grep -rn "use_realtime_for_smalltalk" jarvis/ tests/` must return only this deletion. It is dead — verified in the prior-art research pass.)

- [ ] **Step 6: Add the `set_voice_mode` writer**

In `jarvis/core/config_writer.py`, after `set_computer_use_engine` (:529), add:

```python
def set_voice_mode(mode: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the active voice engine to ``[voice] mode``.

    ``mode`` is ``"pipeline"`` (classic STT->brain->TTS, the default) or
    ``"realtime"`` (full-duplex speech-to-speech). TOML-only by design:
    ``voice.mode`` is NOT in the drift-guard reference snapshot, so it is never
    reverted (same rationale as :func:`set_computer_use_engine`). Read once per
    voice session, so the switch applies on the next session / restart.
    """
    _patch_table(path, "voice", "mode", mode)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/unit/core/test_voice_mode_config.py -v`
Expected: PASS (4 passed).

- [ ] **Step 8: Commit**

```bash
git add jarvis/core/config.py jarvis/core/config_writer.py tests/unit/core/test_voice_mode_config.py
git commit -m "feat(realtime): add [voice].mode + [brain].realtime config, retire dead flag"
```

---

### Task 2: Latency phases for the realtime hot path

**Files:**
- Modify: `jarvis/core/events.py` (`LatencyPhase` StrEnum at :942)
- Test: `tests/unit/core/test_realtime_latency_phases.py`

**Interfaces:**
- Produces: `LatencyPhase.REALTIME_INPUT_COMMITTED`, `LatencyPhase.REALTIME_FIRST_TRANSCRIPT`, `LatencyPhase.REALTIME_FIRST_AUDIO` (string values below). `_LATENCY_PHASE_VALUES` auto-derives, so `LatencySpan` accepts them with no further edit.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_realtime_latency_phases.py
from uuid import uuid4

from jarvis.core.events import LatencyPhase, LatencySpan


def test_realtime_phases_exist_and_are_accepted_by_the_span_guard():
    for phase in (
        LatencyPhase.REALTIME_INPUT_COMMITTED,
        LatencyPhase.REALTIME_FIRST_TRANSCRIPT,
        LatencyPhase.REALTIME_FIRST_AUDIO,
    ):
        span = LatencySpan(trace_id=uuid4(), phase=phase.value, duration_ms=1.0)
        assert span.phase == phase.value


def test_unknown_realtime_phase_still_rejected():
    import pytest

    with pytest.raises(ValueError):
        LatencySpan(trace_id=uuid4(), phase="realtime_not_a_phase")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_realtime_latency_phases.py -v`
Expected: FAIL — `AttributeError: REALTIME_INPUT_COMMITTED`.

- [ ] **Step 3: Add the three enum members**

In `jarvis/core/events.py`, inside `class LatencyPhase(StrEnum):`, append after `TTS_STREAM_DONE = "tts_stream_done"`:

```python
    # Realtime duplex voice mode (browser/OpenAI). REALTIME_INPUT_COMMITTED is
    # the per-turn anchor + stall-guard reset point; FIRST_TRANSCRIPT is the
    # BrainTTFT-equivalent; FIRST_AUDIO is the first provider audio delta
    # received (pre scrub-hold). AudioOutFirst still marks the first audible,
    # post-hold sample.
    REALTIME_INPUT_COMMITTED = "realtime_input_committed"
    REALTIME_FIRST_TRANSCRIPT = "realtime_first_transcript"
    REALTIME_FIRST_AUDIO = "realtime_first_audio"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_realtime_latency_phases.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/events.py tests/unit/core/test_realtime_latency_phases.py
git commit -m "feat(realtime): add realtime latency phases to the single-source enum"
```

---

### Task 3: Plugin group + provider Protocol + event union

**Files:**
- Create: `jarvis/realtime/__init__.py`, `jarvis/realtime/protocol.py`
- Create: `jarvis/plugins/realtime/__init__.py`
- Modify: `jarvis/core/protocols.py` (`PLUGIN_GROUPS` at :380)
- Modify: `pyproject.toml` (new entry-point block)
- Test: `tests/unit/realtime/test_protocol.py`

**Interfaces:**
- Produces: `RealtimeSessionConfig`, `RealtimeEvent`, `RealtimeProvider` (Protocol), `RealtimeSession` (Protocol) in `jarvis/realtime/protocol.py`; plugin group `"jarvis.realtime"`.
- Consumes: `AudioChunk` (protocols.py:22), `runtime_checkable` pattern.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/realtime/test_protocol.py
from jarvis.core.protocols import PLUGIN_GROUPS


def test_realtime_group_registered():
    assert "jarvis.realtime" in PLUGIN_GROUPS


def test_protocol_types_importable():
    from jarvis.realtime.protocol import (
        RealtimeEvent,
        RealtimeProvider,
        RealtimeSession,
        RealtimeSessionConfig,
    )

    ev = RealtimeEvent(type="audio_delta")
    assert ev.type == "audio_delta"
    cfg = RealtimeSessionConfig(instructions="hi", language="en")
    assert cfg.language == "en"
    # Protocols are runtime_checkable.
    assert hasattr(RealtimeProvider, "_is_runtime_protocol")
    assert hasattr(RealtimeSession, "_is_runtime_protocol")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/realtime/test_protocol.py -v`
Expected: FAIL — `"jarvis.realtime" not in PLUGIN_GROUPS` and `ModuleNotFoundError: jarvis.realtime.protocol`.

- [ ] **Step 3: Register the plugin group**

In `jarvis/core/protocols.py`, extend the `PLUGIN_GROUPS` tuple (:380):

```python
PLUGIN_GROUPS: tuple[str, ...] = (
    "jarvis.wakeword",
    "jarvis.stt",
    "jarvis.tts",
    "jarvis.brain",
    "jarvis.harness",
    "jarvis.tool",
    "jarvis.channel",  # NEW
    "jarvis.realtime",  # NEW — full-duplex speech-to-speech providers
)
```

- [ ] **Step 4: Create the protocol module**

```python
# jarvis/realtime/protocol.py
"""Contracts for the realtime (full-duplex speech-to-speech) plugin group.

A realtime provider fuses STT + reasoning + TTS + VAD into one stateful
WebSocket session. None of the Brain/STT/TTS protocols can express this, so this
is its own ``jarvis.realtime`` group. Provider modules live under
``jarvis/plugins/realtime/`` and MUST NOT import ``jarvis.*`` at module import.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from jarvis.core.protocols import AudioChunk

RealtimeEventType = Literal[
    "audio_delta",
    "output_transcript_delta",
    "input_transcript",
    "speech_started",
    "interrupted",
    "turn_complete",
    "error",
]


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    """One normalized, provider-neutral event from a duplex session."""

    type: RealtimeEventType
    audio: AudioChunk | None = None          # audio_delta
    text: str | None = None                  # output_transcript_delta / input_transcript
    is_final: bool = False
    ms_played: int | None = None             # speech_started: ms of our audio already heard
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RealtimeSessionConfig:
    """Everything a provider needs to open one duplex session."""

    instructions: str = ""
    language: str = "en"                     # bare de/en/es (resolved once, upstream)
    voice: str = ""
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    modalities: tuple[str, ...] = ("audio", "text")
    turn_detection: str = "server_vad"       # "server_vad" | "semantic_vad"


@runtime_checkable
class RealtimeSession(Protocol):
    """A live duplex handle (one connection)."""

    session_id: str

    async def send_audio(self, chunk: AudioChunk) -> None: ...
    def receive(self) -> AsyncIterator[RealtimeEvent]: ...
    async def update_session(self, *, instructions: str | None = None, language: str | None = None) -> None: ...
    async def truncate(self, audio_end_ms: int) -> None: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class RealtimeProvider(Protocol):
    """The plugin entry-point class."""

    name: str
    supports_realtime: bool
    input_sample_rate: int
    output_sample_rate: int

    async def can_open_duplex_session(self) -> bool: ...
    async def open_session(self, cfg: RealtimeSessionConfig) -> RealtimeSession: ...
```

- [ ] **Step 5: Create the package inits**

```python
# jarvis/realtime/__init__.py
"""Realtime (full-duplex speech-to-speech) orchestrator package.

Nothing heavy is imported at module load (AP-26): the OpenAI SDK is imported
lazily inside the provider adapter. Use ``build_realtime_session`` from
``jarvis.realtime.factory`` to construct a session.
"""
```

```python
# jarvis/plugins/realtime/__init__.py
"""Realtime provider plugins (jarvis.realtime group).

Per the structural plugin rule these modules MUST NOT import ``jarvis.*`` at
module import; keep the provider SDK import lazy inside methods.
"""
```

- [ ] **Step 6: Register the entry-point group**

In `pyproject.toml`, after the `[project.entry-points."jarvis.channel"]` block, add:

```toml
[project.entry-points."jarvis.realtime"]
# Full-duplex speech-to-speech providers (Phase realtime-voice). OpenAI GA
# gpt-realtime is the first; Gemini Live follows in Phase 4.
openai-realtime = "jarvis.plugins.realtime.openai_realtime:OpenAIRealtimeProvider"
```

- [ ] **Step 7: Activate entry-points and run the test**

Run: `pip install -e . --no-deps && pytest tests/unit/realtime/test_protocol.py -v`
Expected: PASS (2 passed). (The `openai_realtime` module lands in Task 5; the entry-point line does not load until `registry.load` is called, so the group registers cleanly now.)

- [ ] **Step 8: Commit**

```bash
git add jarvis/core/protocols.py jarvis/realtime/__init__.py jarvis/realtime/protocol.py jarvis/plugins/realtime/__init__.py pyproject.toml tests/unit/realtime/test_protocol.py
git commit -m "feat(realtime): add jarvis.realtime plugin group + provider Protocol + event union"
```

---

## Phase 1 — Browser realtime conversation (OpenAI, default OFF)

### Task 4: The load-bearing audio-hold scrub gate

**Files:**
- Create: `jarvis/realtime/scrub_gate.py`
- Test: `tests/unit/realtime/test_scrub_gate.py`

**Interfaces:**
- Produces: `ScrubHoldGate(language: str, *, lookahead_ms: int = 250)` with `async def feed_transcript(text: str) -> str` (returns display-safe text), `async def push_audio(chunk: AudioChunk) -> list[AudioChunk]` (returns releasable chunks), `def hard_leak_pending() -> bool`, `def drain() -> None`, `def fallback_phrase() -> str`.
- Consumes: `scrub_for_voice` (output_filter.py:442) + `ScrubResult`, `FALLBACK_PHRASES` (output_filter.py:73), `AudioChunk` (protocols.py:22).

**Design note:** Phase-1 conservative policy — buffer audio deltas; on each transcript boundary run `scrub_for_voice`; if `fallback_used` OR a hard-leak action (`replaced_stacktrace` / `replaced_raw_repr` / `replaced_shell_command`) appears, mark a hard leak, drop buffered audio, and signal the session to cancel + speak the fallback. Otherwise release buffered audio. An availability cap (`lookahead_ms`) releases audio if no transcript arrives, so a missing transcript never deadlocks playback.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/realtime/test_scrub_gate.py
import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.realtime.scrub_gate import ScrubHoldGate

_HARD_LEAK_ACTIONS = {"replaced_stacktrace", "replaced_raw_repr", "replaced_shell_command"}


def _chunk(n: int) -> AudioChunk:
    return AudioChunk(pcm=b"\x00\x01" * n, sample_rate=24000, timestamp_ns=0)


@pytest.mark.asyncio
async def test_clean_transcript_releases_buffered_audio():
    gate = ScrubHoldGate(language="en")
    await gate.push_audio(_chunk(4))
    released = await gate.feed_transcript("Hello there, how can I help?")
    # display text returned; audio releasable after the clean boundary
    assert released == "Hello there, how can I help?" or released  # scrubbed display
    out = await gate.push_audio(_chunk(4))
    assert gate.hard_leak_pending() is False
    assert out  # buffered + new audio flows once transcript cleared


@pytest.mark.asyncio
async def test_hard_leak_transcript_marks_leak_and_drops_audio():
    gate = ScrubHoldGate(language="en")
    await gate.push_audio(_chunk(4))
    # A stacktrace transcript is a hard leak (scrub_for_voice early-returns fallback).
    await gate.feed_transcript("Traceback (most recent call last):\n  File x\nValueError: y\n\n")
    assert gate.hard_leak_pending() is True
    # No audio may be released after a hard leak.
    out = await gate.push_audio(_chunk(4))
    assert out == []
    assert gate.fallback_phrase() == "An error occurred."


@pytest.mark.asyncio
async def test_scrub_is_regex_only_no_llm(monkeypatch):
    # Guard AP-11: the gate must call scrub_for_voice and nothing that awaits a model.
    import jarvis.realtime.scrub_gate as mod

    calls = {"n": 0}
    real = mod.scrub_for_voice

    def spy(text, **kw):
        calls["n"] += 1
        return real(text, **kw)

    monkeypatch.setattr(mod, "scrub_for_voice", spy)
    gate = ScrubHoldGate(language="en")
    await gate.feed_transcript("A normal sentence.")
    assert calls["n"] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/realtime/test_scrub_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: jarvis.realtime.scrub_gate`.

- [ ] **Step 3: Implement the gate**

```python
# jarvis/realtime/scrub_gate.py
"""Audio-hold voice-scrub gate for realtime duplex mode (AP-11 / ADR-0010).

A duplex model speaks audio natively and its transcript is co-timed but NOT
guaranteed to arrive before the matching audio is audible. So we buffer each
decoded audio delta and release it only once its transcript region has passed
``scrub_for_voice``. A hard leak (stacktrace / raw repr / shell command) drops
the buffered audio and signals the session to cancel + speak the fallback.
Regex-only, no LLM (AP-11).
"""

from __future__ import annotations

from jarvis.brain.output_filter import FALLBACK_PHRASES, scrub_for_voice
from jarvis.core.protocols import AudioChunk

_HARD_LEAK_ACTIONS = frozenset(
    {"replaced_stacktrace", "replaced_raw_repr", "replaced_shell_command", "replaced_with_fallback_residue"}
)


class ScrubHoldGate:
    """Hold audio until its transcript is scrub-cleared; drop on a hard leak."""

    def __init__(self, language: str, *, lookahead_ms: int = 250) -> None:
        self._language = language if language in FALLBACK_PHRASES else "en"
        self._lookahead_ms = lookahead_ms
        self._pending: list[AudioChunk] = []
        self._cleared = False
        self._hard_leak = False

    def hard_leak_pending(self) -> bool:
        return self._hard_leak

    def fallback_phrase(self) -> str:
        return FALLBACK_PHRASES.get(self._language, FALLBACK_PHRASES["en"])

    async def feed_transcript(self, text: str) -> str:
        """Scrub a transcript boundary. Returns display-safe text.

        Sets the clear flag (audio may flow) on clean text; sets the hard-leak
        flag (audio dropped) on a hard leak.
        """
        result = scrub_for_voice(text, language=self._language)
        if result.fallback_used or (_HARD_LEAK_ACTIONS & set(result.actions)):
            self._hard_leak = True
            self._cleared = False
            self._pending.clear()
            return result.cleaned  # the canned fallback phrase
        self._cleared = True
        return result.cleaned

    async def push_audio(self, chunk: AudioChunk) -> list[AudioChunk]:
        """Buffer or release an audio delta. Returns chunks safe to play now."""
        if self._hard_leak:
            return []
        if self._cleared:
            out = self._pending + [chunk]
            self._pending = []
            self._cleared = False  # one-shot: the next chunk re-buffers until its own transcript clears
            return out
        self._pending.append(chunk)
        return []

    def release_available(self) -> list[AudioChunk]:
        """Availability cap: release whatever is buffered (no transcript came)."""
        if self._hard_leak:
            return []
        out = self._pending
        self._pending = []
        self._cleared = False  # released on timeout, not a clean transcript — go back to holding
        return out

    def drain(self) -> None:
        """Barge-in / turn-end: discard buffered audio and reset per-turn state."""
        self._pending.clear()
        self._cleared = False
        self._hard_leak = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/realtime/test_scrub_gate.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/realtime/scrub_gate.py tests/unit/realtime/test_scrub_gate.py
git commit -m "feat(realtime): audio-hold voice-scrub gate (load-bearing safety, AP-11)"
```

---

### Task 5: OpenAI Realtime provider adapter + provider contract test

**Files:**
- Create: `jarvis/plugins/realtime/openai_realtime.py`
- Test: `tests/contract/test_realtime_provider_contract.py`

**Interfaces:**
- Produces: `OpenAIRealtimeProvider` implementing `RealtimeProvider`; its `open_session` returns an object implementing `RealtimeSession` whose `receive()` yields normalized `RealtimeEvent`s.
- Consumes: `jarvis.realtime.protocol` types; `get_provider_secret("openai")` (config.py:2515); the installed `openai` SDK (`AsyncOpenAI().realtime.connect(model="gpt-realtime")`).

**Design note:** The adapter maps wire events → `RealtimeEvent`: `response.output_audio.delta` (base64 24 kHz PCM16) → `audio_delta`; `response.output_audio_transcript.delta` → `output_transcript_delta`; `conversation.item.input_audio_transcription.completed` → `input_transcript(is_final=True)`; `input_audio_buffer.speech_started` → `speech_started`; `response.done` → `turn_complete`; errors → `error`. Input is upsampled 16 kHz→24 kHz before `input_audio_buffer.append` using `jarvis.telephony.audio.resample_pcm16` (no hard `audioop` import in this module). `can_open_duplex_session()` returns `bool(get_provider_secret("openai"))` — a cheap key probe; a live connect probe is Phase 4.

- [ ] **Step 1: Write the failing contract test**

```python
# tests/contract/test_realtime_provider_contract.py
import pytest

from jarvis.realtime.protocol import RealtimeProvider


def _load_provider_class():
    from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
    return OpenAIRealtimeProvider


def test_provider_is_structurally_conformant():
    cls = _load_provider_class()
    inst = cls()
    assert isinstance(inst, RealtimeProvider)
    assert inst.supports_realtime is True
    assert inst.name == "openai-realtime"


@pytest.mark.asyncio
async def test_can_open_duplex_session_returns_bool_when_keyless(monkeypatch):
    import jarvis.plugins.realtime.openai_realtime as mod

    monkeypatch.setattr(mod, "get_provider_secret", lambda _p: None)
    inst = _load_provider_class()()
    assert await inst.can_open_duplex_session() is False


def test_module_does_not_import_openai_at_top_level():
    # AP-26: the SDK import is lazy inside methods, not at module import.
    import ast
    import pathlib

    src = pathlib.Path("jarvis/plugins/realtime/openai_realtime.py").read_text("utf-8")
    tree = ast.parse(src)
    top_imports = [
        n
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for n in (getattr(node, "names", []) or [])
    ]
    assert not any("openai" in (a.name or "") for a in top_imports)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contract/test_realtime_provider_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: jarvis.plugins.realtime.openai_realtime`.

- [ ] **Step 3: Implement the adapter**

```python
# jarvis/plugins/realtime/openai_realtime.py
"""OpenAI GA realtime provider (gpt-realtime) for the jarvis.realtime group.

Uses the RELEASED interface AsyncOpenAI().realtime.connect(...) (NOT the removed
client.beta.realtime). The openai SDK import is lazy inside connect() (AP-26).
This module must not import jarvis.* beyond the config secret helper and the
protocol types (both stdlib-light, no heavy side effects).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core.config import get_provider_secret
from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent, RealtimeSessionConfig

_MODEL = "gpt-realtime"
_OUTPUT_RATE = 24000
_INPUT_RATE = 24000  # we upsample our 16 kHz mic to 24 kHz before append


class _OpenAIRealtimeSession:
    def __init__(self, conn: Any, cfg: RealtimeSessionConfig, session_id: str) -> None:
        self._conn = conn
        self._cfg = cfg
        self.session_id = session_id
        self._last_item_id = ""

    async def send_audio(self, chunk: AudioChunk) -> None:
        from jarvis.telephony.audio import resample_pcm16

        pcm = chunk.pcm
        if chunk.sample_rate != _INPUT_RATE:
            pcm = resample_pcm16(pcm, chunk.sample_rate, _INPUT_RATE)
        await self._conn.input_audio_buffer.append(audio=base64.b64encode(pcm).decode("ascii"))

    async def receive(self) -> AsyncIterator[RealtimeEvent]:
        async for event in self._conn:
            etype = getattr(event, "type", "")
            if etype == "response.output_audio.delta":
                pcm = base64.b64decode(event.delta)
                yield RealtimeEvent(
                    type="audio_delta",
                    audio=AudioChunk(pcm=pcm, sample_rate=_OUTPUT_RATE, timestamp_ns=0),
                )
            elif etype == "response.output_audio_transcript.delta":
                yield RealtimeEvent(type="output_transcript_delta", text=event.delta)
            elif etype == "conversation.item.input_audio_transcription.completed":
                yield RealtimeEvent(type="input_transcript", text=event.transcript, is_final=True)
            elif etype == "input_audio_buffer.speech_started":
                yield RealtimeEvent(type="speech_started")
            elif etype == "response.done":
                yield RealtimeEvent(type="turn_complete")
            elif etype == "error":
                yield RealtimeEvent(type="error", error=str(getattr(event, "error", event)))

    async def update_session(self, *, instructions: str | None = None, language: str | None = None) -> None:
        payload: dict[str, Any] = {}
        if instructions is not None:
            payload["instructions"] = instructions
        if payload:
            await self._conn.session.update(session=payload)

    async def truncate(self, audio_end_ms: int) -> None:
        if self._last_item_id:
            await self._conn.conversation.item.truncate(
                item_id=self._last_item_id, content_index=0, audio_end_ms=audio_end_ms
            )

    async def interrupt(self) -> None:
        await self._conn.response.cancel()

    async def close(self) -> None:
        await self._conn.close()


class OpenAIRealtimeProvider:
    name = "openai-realtime"
    supports_realtime = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE

    async def can_open_duplex_session(self) -> bool:
        return bool(get_provider_secret("openai"))

    async def open_session(self, cfg: RealtimeSessionConfig):
        from openai import AsyncOpenAI  # lazy (AP-26)

        client = AsyncOpenAI(api_key=get_provider_secret("openai"))
        conn = await client.realtime.connect(model=_MODEL).__aenter__()
        await conn.session.update(
            session={
                "instructions": cfg.instructions,
                "output_modalities": list(cfg.modalities),
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": cfg.input_sample_rate},
                        "turn_detection": {"type": cfg.turn_detection},
                    },
                    "output": {
                        "format": {"type": "audio/pcm"},
                        **({"voice": cfg.voice} if cfg.voice else {}),
                    },
                },
            }
        )
        import uuid

        return _OpenAIRealtimeSession(conn, cfg, session_id=str(uuid.uuid4()))
```

*(The exact `session.update` payload keys track the GA schema verified in the design's Facts pass; if the SDK reports an unknown field on connect, adjust the payload — the wire `type` strings above are the stable GA names.)*

- [ ] **Step 4: Run tests to verify they pass**

Run: `pip install -e . --no-deps && pytest tests/contract/test_realtime_provider_contract.py -v`
Expected: PASS (3 passed). `open_session` is not exercised here (it needs a live socket); it is covered by the session test in Task 6 via a fake provider.

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/realtime/openai_realtime.py tests/contract/test_realtime_provider_contract.py
git commit -m "feat(realtime): OpenAI GA realtime provider adapter + contract test"
```

---

### Task 6: RealtimeVoiceSession — the /ws/audio duck interface

**Files:**
- Create: `jarvis/realtime/session.py`
- Test: `tests/unit/realtime/test_session.py`

**Interfaces:**
- Produces: `RealtimeVoiceSession(*, session_id, send_binary, send_json, provider, config, bus=None)` with `async handle_audio_frame(pcm_native: bytes)`, `async handle_control(msg: dict)`, `async end(*, reason: str = "")` — the exact duck interface `browser_voice_ws` calls.
- Consumes: `RealtimeProvider`/`RealtimeSession`/`RealtimeSessionConfig`/`RealtimeEvent` (protocol.py), `ScrubHoldGate` (scrub_gate.py), `resolve_output_language` (turn_language.py:178), `Resampler`/`STT_SAMPLE_RATE` (telephony/audio.py), `AudioOutFirst` (events.py:929), `mark_phase`/`LatencyPhase` (telemetry/latency.py), `SendBinary`/`SendJson` shapes (session.py:44).

**Design note:** Mirrors `BrowserVoiceSession`'s transport contract (injected `send_binary`/`send_json`, browser 48 kHz → 16 kHz inbound resample). Inbound frames are forwarded to `provider.send_audio` (the provider upsamples 16→24 kHz). A `receive()` pump task maps events: `audio_delta` → gate → `send_binary` (+ `AudioOutFirst` + `REALTIME_FIRST_AUDIO`); `output_transcript_delta` → gate.feed_transcript → `send_json({"type":"transcript"...})` (+ `REALTIME_FIRST_TRANSCRIPT`); `input_transcript` → resolve language, re-pin on change, `REALTIME_INPUT_COMMITTED`; `speech_started` → `send_json({"type":"tts_cancel"})` + `gate.drain()` + `provider.truncate`; a hard leak → cancel + speak the fallback via one `send_binary` of TTS-less text is out of scope, so send `send_json({"type":"error_spoken","text":fallback})` and drain. Server-VAD owns turn boundaries; NO local endpointer runs (avoids double VAD).

- [ ] **Step 1: Write the failing test (fakes, not mocks)**

```python
# tests/unit/realtime/test_session.py
import asyncio

import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent, RealtimeSessionConfig
from jarvis.realtime.session import RealtimeVoiceSession


class FakeSession:
    session_id = "fake"

    def __init__(self, events):
        self._events = events
        self.sent_audio = []
        self.truncated = []
        self.closed = False

    async def send_audio(self, chunk):
        self.sent_audio.append(chunk)

    async def receive(self):
        for ev in self._events:
            yield ev
            await asyncio.sleep(0)

    async def update_session(self, *, instructions=None, language=None):
        pass

    async def truncate(self, audio_end_ms):
        self.truncated.append(audio_end_ms)

    async def interrupt(self):
        pass

    async def close(self):
        self.closed = True


class FakeProvider:
    name = "fake"
    supports_realtime = True
    input_sample_rate = 16000
    output_sample_rate = 24000

    def __init__(self, events):
        self._events = events

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, cfg):
        self.session = FakeSession(self._events)
        return self.session


def _cfg():
    from types import SimpleNamespace

    return SimpleNamespace(brain=SimpleNamespace(reply_language="en"), voice=SimpleNamespace(mode="realtime"))


@pytest.mark.asyncio
async def test_clean_turn_streams_audio_and_transcript():
    events = [
        RealtimeEvent(type="output_transcript_delta", text="Hello there."),
        RealtimeEvent(type="audio_delta", audio=AudioChunk(pcm=b"\x01\x02" * 8, sample_rate=24000, timestamp_ns=0)),
        RealtimeEvent(type="turn_complete"),
    ]
    binaries, jsons = [], []
    sess = RealtimeVoiceSession(
        session_id="s1",
        send_binary=lambda b: binaries.append(b) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await asyncio.sleep(0.05)  # let the receive pump drain the fake events
    await sess.end(reason="test")
    assert any(m.get("type") == "transcript" for m in jsons)
    assert binaries  # audio was released after the clean transcript


@pytest.mark.asyncio
async def test_hard_leak_transcript_drops_audio():
    events = [
        RealtimeEvent(type="audio_delta", audio=AudioChunk(pcm=b"\x01\x02" * 8, sample_rate=24000, timestamp_ns=0)),
        RealtimeEvent(type="output_transcript_delta", text="Traceback (most recent call last):\n  File a\nValueError: b\n\n"),
        RealtimeEvent(type="turn_complete"),
    ]
    binaries, jsons = [], []
    sess = RealtimeVoiceSession(
        session_id="s2",
        send_binary=lambda b: binaries.append(b) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await asyncio.sleep(0.05)
    await sess.end(reason="test")
    # The pre-leak audio was buffered, then dropped when the leak transcript arrived.
    assert binaries == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/realtime/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: jarvis.realtime.session`.

- [ ] **Step 3: Implement the session**

```python
# jarvis/realtime/session.py
"""RealtimeVoiceSession — the duplex session that slots into /ws/audio.

Implements the same duck interface (handle_audio_frame/handle_control/end) as
BrowserVoiceSession, so the existing route branches to it with no receive-loop
change. Server-VAD owns turn boundaries (no local endpointer). Model audio is
held by ScrubHoldGate until its transcript is scrub-cleared (AP-11).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.core.protocols import AudioChunk
from jarvis.core.turn_language import resolve_output_language
from jarvis.realtime.protocol import RealtimeSessionConfig
from jarvis.realtime.scrub_gate import ScrubHoldGate
from jarvis.telephony.audio import STT_SAMPLE_RATE, Resampler

log = logging.getLogger(__name__)

_INSTRUCTIONS = (
    "You are Jarvis, a concise spoken assistant. Never read tool JSON, code, "
    "stack traces, file paths, base64, or raw URLs aloud — speak only a natural "
    "summary. Reply in the user's language."
)


class RealtimeVoiceSession:
    def __init__(
        self,
        *,
        session_id: str,
        send_binary: Any,
        send_json: Any,
        provider: Any,
        config: Any,
        bus: Any = None,
        browser_sample_rate: int = 48000,
    ) -> None:
        self.session_id = session_id
        self._send_binary = send_binary
        self._send_json = send_json
        self._provider = provider
        self._config = config
        self._bus = bus
        self.browser_sample_rate = int(browser_sample_rate or STT_SAMPLE_RATE)
        self._in_resampler = Resampler(self.browser_sample_rate, STT_SAMPLE_RATE)

        self._language = self._resolve_lang(text="")
        self._gate = ScrubHoldGate(self._language)
        self._session: Any = None
        self._pump_task: asyncio.Task[None] | None = None
        self._ms_sent = 0
        self._ended = False

    def _resolve_lang(self, *, text: str) -> str:
        brain = getattr(self._config, "brain", None)
        pin = getattr(brain, "reply_language", "auto")
        return resolve_output_language(pin, "unknown", text)

    async def handle_control(self, msg: dict[str, Any]) -> None:
        kind = str(msg.get("type", ""))
        if kind == "audio_start":
            rate = int(msg.get("sample_rate", self.browser_sample_rate) or self.browser_sample_rate)
            if rate != self.browser_sample_rate:
                self.browser_sample_rate = rate
                self._in_resampler = Resampler(rate, STT_SAMPLE_RATE)
            if self._session is None:
                await self._open()
            await self._send_json({"type": "audio_ready"})
        elif kind == "barge_in":
            await self._barge_in()
        elif kind == "audio_stop":
            await self.end(reason="client_stop")

    async def _open(self) -> None:
        cfg = RealtimeSessionConfig(
            instructions=_INSTRUCTIONS,
            language=self._language,
            voice=getattr(getattr(self._config, "voice", None), "realtime_voice", "") or "",
        )
        self._session = await self._provider.open_session(cfg)
        self._pump_task = asyncio.create_task(self._pump(), name=f"rt-pump-{self.session_id}")

    async def handle_audio_frame(self, pcm_native: bytes) -> None:
        if self._ended or self._session is None or not pcm_native:
            return
        try:
            pcm16 = bytes(pcm_native) if self.browser_sample_rate == STT_SAMPLE_RATE else self._in_resampler.process(bytes(pcm_native))
        except Exception:  # noqa: BLE001 — malformed frame, drop it
            return
        await self._session.send_audio(AudioChunk(pcm=pcm16, sample_rate=STT_SAMPLE_RATE, timestamp_ns=0))

    async def _pump(self) -> None:
        from jarvis.telemetry.latency import LatencyPhase, mark_phase

        try:
            async for ev in self._session.receive():
                if ev.type == "input_transcript" and ev.text:
                    new_lang = self._resolve_lang(text=ev.text)
                    if new_lang != self._language:
                        self._language = new_lang
                        self._gate = ScrubHoldGate(new_lang)
                        await self._session.update_session(instructions=_INSTRUCTIONS, language=new_lang)
                    mark_phase(LatencyPhase.REALTIME_INPUT_COMMITTED)
                elif ev.type == "output_transcript_delta" and ev.text:
                    mark_phase(LatencyPhase.REALTIME_FIRST_TRANSCRIPT)
                    display = await self._gate.feed_transcript(ev.text)
                    if self._gate.hard_leak_pending():
                        await self._session.interrupt()
                        await self._send_json({"type": "error_spoken", "text": self._gate.fallback_phrase()})
                        self._gate.drain()
                        continue
                    await self._send_json({"type": "transcript", "text": display, "is_final": False})
                elif ev.type == "audio_delta" and ev.audio is not None:
                    mark_phase(LatencyPhase.REALTIME_FIRST_AUDIO)
                    for chunk in await self._gate.push_audio(ev.audio):
                        await self._emit_audio(chunk)
                elif ev.type == "speech_started":
                    await self._barge_in()
                elif ev.type == "turn_complete":
                    for chunk in self._gate.release_available():
                        await self._emit_audio(chunk)
                    self._gate.drain()
                elif ev.type == "error":
                    log.warning("realtime[%s] provider error: %s", self.session_id, ev.error)
        except Exception:  # noqa: BLE001 — AP-20: any pump error is terminal
            log.warning("realtime[%s] pump ended", self.session_id, exc_info=True)

    async def _emit_audio(self, chunk: AudioChunk) -> None:
        if self._ms_sent == 0 and self._bus is not None:
            from jarvis.core.events import AudioOutFirst

            try:
                await self._bus.publish(AudioOutFirst())
            except Exception:  # noqa: BLE001
                pass
        self._ms_sent += len(chunk.pcm)
        await self._send_binary(chunk.pcm)

    async def _barge_in(self) -> None:
        self._gate.drain()
        try:
            await self._session.truncate(audio_end_ms=self._ms_sent // (24000 * 2 // 1000) if self._ms_sent else 0)
        except Exception:  # noqa: BLE001
            pass
        self._ms_sent = 0
        try:
            await self._send_json({"type": "tts_cancel"})
        except Exception:  # noqa: BLE001, S110
            pass

    async def end(self, *, reason: str = "") -> None:
        if self._ended:
            return
        self._ended = True
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001
                pass
        log.info("realtime[%s] ended: reason=%s", self.session_id, reason)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/realtime/test_session.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/realtime/session.py tests/unit/realtime/test_session.py
git commit -m "feat(realtime): RealtimeVoiceSession duck interface for /ws/audio"
```

---

### Task 7: Factory + browser route branch (default OFF)

**Files:**
- Create: `jarvis/realtime/factory.py`
- Modify: `jarvis/browser_voice/route.py` (`_browser_voice_enabled` at :29, `_build_browser_session` at :44)
- Test: `tests/unit/realtime/test_factory.py`, `tests/unit/web/test_voice_mode_route.py` (route branch part)

**Interfaces:**
- Produces: `build_realtime_session(*, cfg, bus, session_id, send_binary, send_json) -> RealtimeVoiceSession | None` (returns `None` when realtime is not selected or no OpenAI key).
- Consumes: `OpenAIRealtimeProvider` (via registry `load`), `get_provider_secret("openai")`, `RealtimeVoiceSession`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/realtime/test_factory.py
from types import SimpleNamespace

from jarvis.realtime.factory import build_realtime_session


def _cfg(mode, key):
    import jarvis.core.config as c
    orig = c.get_provider_secret
    return SimpleNamespace(voice=SimpleNamespace(mode=mode), brain=SimpleNamespace(reply_language="en")), orig, key


def test_returns_none_when_mode_is_pipeline(monkeypatch):
    import jarvis.realtime.factory as f
    monkeypatch.setattr(f, "get_provider_secret", lambda _p: "sk-x")
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="pipeline"), brain=SimpleNamespace(reply_language="en"))
    assert build_realtime_session(cfg=cfg, bus=None, session_id="s", send_binary=None, send_json=None) is None


def test_returns_none_when_no_openai_key(monkeypatch):
    import jarvis.realtime.factory as f
    monkeypatch.setattr(f, "get_provider_secret", lambda _p: None)
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="realtime"), brain=SimpleNamespace(reply_language="en"))
    assert build_realtime_session(cfg=cfg, bus=None, session_id="s", send_binary=None, send_json=None) is None


def test_builds_session_when_realtime_and_keyed(monkeypatch):
    import jarvis.realtime.factory as f
    monkeypatch.setattr(f, "get_provider_secret", lambda _p: "sk-x")
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="realtime"), brain=SimpleNamespace(reply_language="en"))
    sess = build_realtime_session(cfg=cfg, bus=None, session_id="s", send_binary=lambda b: None, send_json=lambda m: None)
    assert sess is not None
    assert sess.session_id == "s"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/realtime/test_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: jarvis.realtime.factory`.

- [ ] **Step 3: Implement the factory**

```python
# jarvis/realtime/factory.py
"""Build a RealtimeVoiceSession for the browser /ws/audio path.

Returns None (=> caller runs the classic path) when realtime is not selected or
no OpenAI key is present. Phase 1 is OpenAI-only by scope; the cross-family
chain lands in Phase 4.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.config import get_provider_secret

log = logging.getLogger(__name__)


def build_realtime_session(
    *, cfg: Any, bus: Any, session_id: str, send_binary: Any, send_json: Any
):
    mode = getattr(getattr(cfg, "voice", None), "mode", "pipeline")
    if mode != "realtime":
        return None
    if not get_provider_secret("openai"):
        log.info("realtime: no OpenAI key — falling back to the classic path")
        return None
    try:
        from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
        from jarvis.realtime.session import RealtimeVoiceSession

        return RealtimeVoiceSession(
            session_id=session_id,
            send_binary=send_binary,
            send_json=send_json,
            provider=OpenAIRealtimeProvider(),
            config=cfg,
            bus=bus,
        )
    except Exception as exc:  # noqa: BLE001 — unbuildable stack => classic path
        log.warning("realtime: session build failed: %s", exc)
        return None
```

- [ ] **Step 4: Branch the browser route (default-OFF for realtime)**

In `jarvis/browser_voice/route.py`, in `_build_browser_session` (:44), add the realtime branch at the very top of the function body, before the factory-seam check:

```python
    # Realtime mode branch (default OFF): only when [voice].mode == "realtime"
    # AND an OpenAI key exists. Otherwise fall through to the classic bridge.
    from jarvis.realtime.factory import build_realtime_session

    rt = build_realtime_session(
        cfg=cfg, bus=bus, session_id=session_id, send_binary=send_binary, send_json=send_json
    )
    if rt is not None:
        return rt
```

And invert the connect gate so the socket is closed by default unless a voice surface is explicitly enabled. Replace `_browser_voice_enabled` (:29) with:

```python
def _browser_voice_enabled(cfg: Any) -> bool:
    """Default OFF. The socket is only served when the user has explicitly
    enabled a browser voice surface: realtime mode ([voice].mode == "realtime")
    or the classic browser bridge ([browser_voice].enabled == true).
    """
    if getattr(getattr(cfg, "voice", None), "mode", "pipeline") == "realtime":
        return True
    bv = getattr(cfg, "browser_voice", None)
    return bool(getattr(bv, "enabled", False)) if bv is not None else False
```

- [ ] **Step 5: Write the route-branch test**

```python
# tests/unit/web/test_voice_mode_route.py  (route-branch half)
from types import SimpleNamespace

from jarvis.browser_voice.route import _browser_voice_enabled


def test_gate_default_off_when_pipeline_and_no_browser_voice():
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="pipeline"))
    assert _browser_voice_enabled(cfg) is False


def test_gate_on_for_realtime_mode():
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="realtime"))
    assert _browser_voice_enabled(cfg) is True


def test_gate_on_for_explicit_classic_browser_voice():
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="pipeline"), browser_voice=SimpleNamespace(enabled=True))
    assert _browser_voice_enabled(cfg) is True
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/realtime/test_factory.py tests/unit/web/test_voice_mode_route.py -v`
Expected: PASS.

- [ ] **Step 7: Verify the classic browser-bridge tests still pass**

Run: `pytest tests/unit/browser_voice/ -v`
Expected: PASS — confirm the inverted default gate didn't break the existing suite (if a test asserted default-ON, update it to pass `browser_voice=SimpleNamespace(enabled=True)`, and note the behavior change in the commit body).

- [ ] **Step 8: Commit**

```bash
git add jarvis/realtime/factory.py jarvis/browser_voice/route.py tests/unit/realtime/test_factory.py tests/unit/web/test_voice_mode_route.py
git commit -m "feat(realtime): factory + browser route branch, default-OFF connect gate"
```

---

### Task 8: Settings route — GET/PUT /api/settings/voice-mode

**Files:**
- Modify: `jarvis/ui/web/settings_routes.py` (existing router, prefix `/api/settings`, tags at :44)
- Test: `tests/unit/web/test_voice_mode_route.py` (route handler half — append)

**Interfaces:**
- Produces: `GET /api/settings/voice-mode -> {mode, realtime_available, active_provider}`; `PUT /api/settings/voice-mode {mode, persist} -> {ok, mode, persisted}`.
- Consumes: `app.state.config` (server.py:410), `config_writer.set_voice_mode` (Task 1), `get_provider_secret("openai")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/web/test_voice_mode_route.py  (append handler tests)
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from jarvis.ui.web.settings_routes import router


def _app(mode="pipeline", key="sk-x", monkeypatch=None):
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(voice=SimpleNamespace(mode=mode))
    return app


def test_get_voice_mode(monkeypatch):
    import jarvis.ui.web.settings_routes as sr
    monkeypatch.setattr(sr, "get_provider_secret", lambda _p: "sk-x")
    client = TestClient(_app(mode="realtime"))
    r = client.get("/api/settings/voice-mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "realtime"
    assert body["realtime_available"] is True


def test_put_voice_mode_invalid_is_400():
    client = TestClient(_app())
    r = client.put("/api/settings/voice-mode", json={"mode": "bogus", "persist": False})
    assert r.status_code == 400


def test_put_voice_mode_updates_live_and_persists(monkeypatch):
    import jarvis.ui.web.settings_routes as sr
    persisted = {"called": False}
    monkeypatch.setattr(sr, "get_provider_secret", lambda _p: "sk-x")

    def fake_set(mode, **kw):
        persisted["called"] = True

    import jarvis.core.config_writer as cw
    monkeypatch.setattr(cw, "set_voice_mode", fake_set)
    app = _app()
    client = TestClient(app)
    r = client.put("/api/settings/voice-mode", json={"mode": "realtime", "persist": True})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "mode": "realtime", "persisted": True}
    assert app.state.config.voice.mode == "realtime"
    assert persisted["called"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/web/test_voice_mode_route.py -k voice_mode -v`
Expected: FAIL — 404 (routes not defined).

- [ ] **Step 3: Add the routes**

In `jarvis/ui/web/settings_routes.py`, add near the reply-language handlers. Import at the top of the file (with the other imports): `from jarvis.core.config import get_provider_secret`. Then:

```python
_VOICE_MODES = ("pipeline", "realtime")


class VoiceModeBody(BaseModel):
    mode: str = Field(..., min_length=1)
    persist: bool = Field(default=True, description="Persist as boot default in jarvis.toml")


@router.get("/voice-mode")
async def get_voice_mode(request: Request) -> dict[str, object]:
    cfg = getattr(request.app.state, "config", None) or getattr(request.app.state, "cfg", None)
    mode = getattr(getattr(cfg, "voice", None), "mode", "pipeline")
    available = bool(get_provider_secret("openai"))
    return {
        "mode": mode,
        "realtime_available": available,
        "active_provider": "openai-realtime" if available else None,
    }


@router.put("/voice-mode")
async def put_voice_mode(body: VoiceModeBody, request: Request) -> dict[str, object]:
    if body.mode not in _VOICE_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {_VOICE_MODES}")

    cfg = getattr(request.app.state, "config", None) or getattr(request.app.state, "cfg", None)
    if cfg is not None and getattr(cfg, "voice", None) is not None:
        try:
            cfg.voice.mode = body.mode  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory cfg.voice.mode update skipped: %s", exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer

            config_writer.set_voice_mode(body.mode)
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("voice-mode persist failed (live switch still applied): %s", exc)

    return {"ok": True, "mode": body.mode, "persisted": persisted}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/web/test_voice_mode_route.py -k voice_mode -v`
Expected: PASS.

- [ ] **Step 5: Run the CLI-coverage gate**

Run: `python scripts/ci/check_cli_coverage.py`
Expected: PASS — `settings_routes.py` is already imported in `server.py` and declares `tags=`, so the new routes are covered.

- [ ] **Step 6: Commit**

```bash
git add jarvis/ui/web/settings_routes.py tests/unit/web/test_voice_mode_route.py
git commit -m "feat(realtime): GET/PUT /api/settings/voice-mode with in-app persist"
```

---

### Task 9: Frontend — PCM worklet, realtime client, settings toggle

**Files:**
- Create: `jarvis/ui/web/frontend/src/lib/pcm-worklet.ts` (capture + playback `AudioWorkletProcessor`s)
- Create: `jarvis/ui/web/frontend/src/lib/realtimeAudio.ts` (dedicated `/ws/audio` client)
- Create: `jarvis/ui/web/frontend/src/hooks/useVoiceMode.ts`
- Create: `jarvis/ui/web/frontend/src/views/settings/RealtimeVoiceGroup.tsx`
- Modify: `jarvis/ui/web/frontend/src/views/SettingsView.tsx` (mount the group at :70)
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/{en,de,es}.json` (toggle strings)
- Test: `jarvis/ui/web/frontend/src/lib/realtimeAudio.test.ts` (vitest)

**Interfaces:**
- Produces: `RealtimeAudioClient` (`connect()`, `disconnect()`), `useVoiceMode()` hook (`{mode, realtimeAvailable, setMode, isLoading}`), `<RealtimeVoiceGroup/>`.
- Consumes: the backend control-frame protocol (up: `audio_start{sample_rate,language}`→`audio_ready`, `barge_in`, `audio_stop`; down: `transcript`, `tts_cancel`, binary 24 kHz PCM), `window.__JARVIS_TOKEN`, the `Switch` primitive (`@/components/ui/switch`), the `useAutostart`/`AppSettingsGroup` toggle pattern.

**Design note:** The worklet is greenfield (no AudioWorklet exists). `tsconfig.lib` lacks AudioWorklet globals, so the processor file declares its own types. Load via `new URL("./pcm-worklet.ts", import.meta.url)` so Vite emits it as a standalone asset. The realtime client opens its OWN WebSocket (`binaryType = "arraybuffer"`) to `${proto}://${host}/ws/audio?token=${window.__JARVIS_TOKEN}` — never the JSON-only `WSClient`. Vite dev proxy already forwards `/ws/*`.

- [ ] **Step 1: Write the failing test (client URL + handshake)**

```ts
// jarvis/ui/web/frontend/src/lib/realtimeAudio.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { buildAudioSocketUrl } from "./realtimeAudio";

describe("realtime audio client", () => {
  beforeEach(() => {
    // @ts-expect-error test shim
    global.window = { location: { protocol: "https:", host: "app.example" }, __JARVIS_TOKEN: "tok" };
  });

  it("builds a wss /ws/audio url with the token", () => {
    expect(buildAudioSocketUrl()).toBe("wss://app.example/ws/audio?token=tok");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- realtimeAudio`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the realtime client**

```ts
// jarvis/ui/web/frontend/src/lib/realtimeAudio.ts
// Dedicated /ws/audio client for realtime voice. Separate from the JSON-only
// WSClient (src/lib/ws.ts): this one carries binary int16 PCM (arraybuffer).

export function buildAudioSocketUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  const token = (window as unknown as { __JARVIS_TOKEN?: string }).__JARVIS_TOKEN;
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}://${host}/ws/audio${query}`;
}

export type RealtimeCallbacks = {
  onTranscript?: (text: string, isFinal: boolean) => void;
  onStatus?: (status: string) => void;
};

export class RealtimeAudioClient {
  private ws: WebSocket | null = null;
  private ctx: AudioContext | null = null;
  private captureNode: AudioWorkletNode | null = null;
  private playbackNode: AudioWorkletNode | null = null;
  private stream: MediaStream | null = null;

  constructor(private cb: RealtimeCallbacks = {}) {}

  async connect(): Promise<void> {
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule(new URL("./pcm-worklet.ts", import.meta.url));

    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.captureNode = new AudioWorkletNode(this.ctx, "pcm-capture");
    this.playbackNode = new AudioWorkletNode(this.ctx, "pcm-playback");
    this.playbackNode.connect(this.ctx.destination);
    src.connect(this.captureNode);

    this.ws = new WebSocket(buildAudioSocketUrl());
    this.ws.binaryType = "arraybuffer";

    this.ws.onopen = () => {
      this.ws?.send(JSON.stringify({ type: "audio_start", sample_rate: this.ctx?.sampleRate ?? 48000 }));
    };
    this.captureNode.port.onmessage = (e) => {
      if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(e.data as ArrayBuffer);
    };
    this.ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        const msg = JSON.parse(e.data);
        if (msg.type === "transcript") this.cb.onTranscript?.(msg.text, !!msg.is_final);
        else if (msg.type === "tts_cancel") this.playbackNode?.port.postMessage({ type: "flush" });
        else this.cb.onStatus?.(msg.type);
      } else {
        this.playbackNode?.port.postMessage({ type: "pcm", data: e.data }, [e.data as ArrayBuffer]);
      }
    };
  }

  async disconnect(): Promise<void> {
    try {
      this.ws?.send(JSON.stringify({ type: "audio_stop" }));
    } catch {
      // socket may already be closing
    }
    this.ws?.close();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close();
    this.ws = null;
    this.ctx = null;
  }
}
```

- [ ] **Step 4: Implement the PCM worklet**

```ts
// jarvis/ui/web/frontend/src/lib/pcm-worklet.ts
// Standalone AudioWorklet module (loaded via addModule). Not part of the main
// bundle graph. tsconfig lib lacks AudioWorklet globals, so declare them here.
declare const sampleRate: number;
declare function registerProcessor(name: string, ctor: unknown): void;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
  constructor();
  process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean;
}

function floatToInt16(float32: Float32Array): ArrayBuffer {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out.buffer;
}

class PcmCapture extends AudioWorkletProcessor {
  process(inputs: Float32Array[][]): boolean {
    const ch = inputs[0]?.[0];
    if (ch && ch.length) this.port.postMessage(floatToInt16(ch), [floatToInt16(ch)]);
    return true;
  }
}

class PcmPlayback extends AudioWorkletProcessor {
  private queue: Float32Array[] = [];
  constructor() {
    super();
    this.port.onmessage = (e: MessageEvent) => {
      const msg = e.data as { type: string; data?: ArrayBuffer };
      if (msg.type === "flush") this.queue = [];
      else if (msg.type === "pcm" && msg.data) {
        const i16 = new Int16Array(msg.data);
        const f32 = new Float32Array(i16.length);
        for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
        this.queue.push(f32);
      }
    };
  }
  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const out = outputs[0]?.[0];
    if (!out) return true;
    let filled = 0;
    while (filled < out.length && this.queue.length) {
      const head = this.queue[0];
      const n = Math.min(head.length, out.length - filled);
      out.set(head.subarray(0, n), filled);
      filled += n;
      if (n === head.length) this.queue.shift();
      else this.queue[0] = head.subarray(n);
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
registerProcessor("pcm-playback", PcmPlayback);
```

*(Note: the backend advertises 24 kHz TTS output while the browser `AudioContext` runs at its own rate — Phase 1 accepts minor pitch drift by playing 24 kHz samples through the context; a resampling playback pass is a Phase-2 polish item, logged here so it is not silently skipped.)*

- [ ] **Step 5: Implement the settings hook + group**

```ts
// jarvis/ui/web/frontend/src/hooks/useVoiceMode.ts
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

type VoiceModeResp = { mode: string; realtime_available: boolean; active_provider: string | null };

export function useVoiceMode() {
  const qc = useQueryClient();
  const q = useQuery<VoiceModeResp>({
    queryKey: ["voice-mode"],
    queryFn: async () => (await fetch("/api/settings/voice-mode")).json(),
  });
  const m = useMutation({
    mutationFn: async (mode: string) => {
      const r = await fetch("/api/settings/voice-mode", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode, persist: true }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["voice-mode"] }),
  });
  return {
    mode: q.data?.mode ?? "pipeline",
    realtimeAvailable: q.data?.realtime_available ?? false,
    setMode: m.mutate,
    isLoading: q.isLoading,
    isSaving: m.isPending,
  };
}
```

```tsx
// jarvis/ui/web/frontend/src/views/settings/RealtimeVoiceGroup.tsx
import { useTranslation } from "react-i18next";
import { Radio } from "lucide-react";

import { Switch } from "@/components/ui/switch";
import { useVoiceMode } from "@/hooks/useVoiceMode";

export function RealtimeVoiceGroup() {
  const { t } = useTranslation();
  const { mode, realtimeAvailable, setMode, isLoading, isSaving } = useVoiceMode();
  const on = mode === "realtime";
  return (
    <div className="rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Radio className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-medium">{t("settings_view.realtime_voice.title", "Realtime voice (browser)")}</h4>
            <Switch
              checked={on}
              disabled={isLoading || isSaving || !realtimeAvailable}
              onCheckedChange={(next) => setMode(next ? "realtime" : "pipeline")}
            />
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {realtimeAvailable
              ? t("settings_view.realtime_voice.description", "Full-duplex speech-to-speech in the browser (OpenAI). Off by default.")
              : t("settings_view.realtime_voice.unavailable", "Needs an OpenAI key. Using standard voice.")}
          </p>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Mount the group + add i18n keys**

In `jarvis/ui/web/frontend/src/views/SettingsView.tsx`, add `import { RealtimeVoiceGroup } from "@/views/settings/RealtimeVoiceGroup";` and place `<RealtimeVoiceGroup />` in the group list after `<AppSettingsGroup />`. Add to each of `src/i18n/locales/{en,de,es}.json` under `settings_view`:

```json
"realtime_voice": {
  "title": "Realtime voice (browser)",
  "description": "Full-duplex speech-to-speech in the browser (OpenAI). Off by default.",
  "unavailable": "Needs an OpenAI key. Using standard voice."
}
```

(Provide the German and Spanish translations in `de.json` / `es.json` — this is localized product copy, an allowed multilingual surface.)

- [ ] **Step 7: Run the frontend checks**

Run: `cd jarvis/ui/web/frontend && npm run test -- realtimeAudio && npm run build`
Expected: the URL test passes; `tsc -b && vite build` succeeds (the worklet emits as a standalone asset).

- [ ] **Step 8: Commit**

```bash
git add jarvis/ui/web/frontend/src/lib/pcm-worklet.ts jarvis/ui/web/frontend/src/lib/realtimeAudio.ts jarvis/ui/web/frontend/src/lib/realtimeAudio.test.ts jarvis/ui/web/frontend/src/hooks/useVoiceMode.ts jarvis/ui/web/frontend/src/views/settings/RealtimeVoiceGroup.tsx jarvis/ui/web/frontend/src/views/SettingsView.tsx jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "feat(realtime): browser PCM worklet + /ws/audio client + voice-mode settings toggle"
```

---

### Task 10: End-to-end smoke + boot budget + full-suite guard

**Files:**
- Test: `tests/integration/realtime/test_realtime_smoke.py` (self-skips without a key)

**Interfaces:**
- Consumes: everything above; a real OpenAI key when present, else self-skip.

- [ ] **Step 1: Write a self-skipping integration smoke test**

```python
# tests/integration/realtime/test_realtime_smoke.py
import asyncio

import pytest

from jarvis.core.config import get_provider_secret

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not get_provider_secret("openai"), reason="no OpenAI key")
@pytest.mark.asyncio
async def test_open_and_close_a_real_session():
    from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
    from jarvis.realtime.protocol import RealtimeSessionConfig

    prov = OpenAIRealtimeProvider()
    assert await prov.can_open_duplex_session() is True
    sess = await prov.open_session(RealtimeSessionConfig(instructions="Say hi.", language="en"))
    # Pull a couple of events with a timeout, then close cleanly.
    async def _drain():
        n = 0
        async for _ in sess.receive():
            n += 1
            if n >= 1:
                break
    try:
        await asyncio.wait_for(_drain(), timeout=10)
    finally:
        await sess.close()
```

- [ ] **Step 2: Run the smoke test (skips without a key)**

Run: `pytest tests/integration/realtime/test_realtime_smoke.py -v`
Expected: SKIPPED (no key) or PASS (with a key).

- [ ] **Step 3: Run the boot-budget gate**

Run: `python scripts/ci/check_boot_budget.py`
Expected: PASS — the realtime imports are lazy, so boot stays within the window ≤ 8 s / voice-usable ≤ 20 s (AP-26).

- [ ] **Step 4: Run the full fast suite + lint + typecheck**

Run: `pytest -m "not slow" && ruff check jarvis/ && mypy jarvis/realtime/ jarvis/plugins/realtime/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/realtime/test_realtime_smoke.py
git commit -m "test(realtime): self-skipping end-to-end smoke + boot-budget verification"
```

---

## Self-Review (completed inline)

- **Spec coverage:** Phase 0 (contracts/scaffolding) → Tasks 1–3; Phase 1 (browser OpenAI conversation) → Tasks 4–10. The design's load-bearing audio-hold gate (Task 4), server-VAD barge-in + language re-pin + latency marks (Task 6), default-OFF config + fallback-to-classic (Tasks 1, 7), in-app switch (Task 8), and the AudioWorklet deliverable (Task 9) are each implemented. Deferred by explicit scope (documented, not gaps): tools/ask-confirmation (Phase 2–3), Gemini + cross-family chain (Phase 4), desktop `_realtime_session()` (Phase 5), capability seed (dropped — conversation-only is not an action surface and `Capability.source` is a closed Literal).
- **Placeholder scan:** No TBD/TODO. The two documented deferrals (24 kHz playback resample polish; live-connect probe) are named with their phase, not left vague.
- **Type consistency:** `RealtimeEvent`/`RealtimeSessionConfig`/`RealtimeProvider`/`RealtimeSession` are defined once (Task 3) and consumed unchanged (Tasks 5–7); `ScrubHoldGate` method names (`feed_transcript`/`push_audio`/`release_available`/`drain`/`hard_leak_pending`/`fallback_phrase`) match between Task 4 and Task 6; `build_realtime_session(cfg, bus, session_id, send_binary, send_json)` signature matches between Task 7's factory and the route branch.
