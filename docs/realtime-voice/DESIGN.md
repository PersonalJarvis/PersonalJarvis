# Realtime Voice Mode — Design Proposal

- **Status:** Design / analysis only — no implementation, no invasive refactor.
- **Date:** 2026-07-07
- **Scope:** A second, user-selectable voice engine ("realtime mode") that runs a
  full-duplex speech-to-speech provider (OpenAI Realtime GA / Gemini Live)
  alongside the existing pipeline mode (STT → thinking brain → TTS). The
  pipeline mode stays **byte-identical and remains the default**; realtime is an
  additive, default-OFF branch.
- **Product decisions (locked by the maintainer):** both surfaces (desktop
  flagship voice **and** the browser `/ws/audio` bridge); full tool-calling
  parity from day one, including ask-tier spoken confirmation in the duplex
  stream; both OpenAI Realtime and Gemini Live from the start.

This document is the reconciliation of a four-part parallel design effort plus an
adversarial architecture review. Where the four drafts disagreed, this document
records the single decision and why.

---

## 1. Goal & non-goals

**Goal.** Let a user switch a running Jarvis into a realtime speech-to-speech
conversation where one provider model does listening + reasoning + speaking over
a single WebSocket with server-side VAD and native function-calling — while
preserving every safety and routing invariant the pipeline mode guarantees
(risk-tier gating, voice-scrub, deterministic heavy-task routing, capability
honesty, cross-family fallback).

**Non-goals.** No change to the pipeline path. No new heavy dependency. No new
provider hard-coding. No mid-session mode flip within a single turn. Desktop
full-duplex with acoustic echo cancellation (AEC) is explicitly a follow-up
(see §8.3).

---

## 2. Verified provider facts (the load-bearing ones)

Confirmed against current OpenAI Realtime and Gemini Live docs and the installed
SDKs (`openai`, `google-genai` — both already base dependencies; nothing new to
install; both interpreters expose the realtime/live modules).

| Concern | OpenAI Realtime (GA, `gpt-realtime`) | Gemini Live (`*-native-audio` / `live-*`) |
|---|---|---|
| Transport | `wss://api.openai.com/v1/realtime` — **server-to-server**, backend holds the key | `client.aio.live.connect` BidiGenerateContent WSS — server-to-server |
| Audio in | PCM16 mono, rate configurable; **24 kHz canonical** → we resample 16k→24k | **PCM16 mono 16 kHz** → exact match, no resample |
| Audio out | PCM16 mono 24 kHz (base64 `response.output_audio.delta`) | PCM16 mono 24 kHz |
| Output transcript side-channel | `response.output_audio_transcript.delta/.done` — co-timed, **not guaranteed before** the matching audio is audible | `server_content.output_transcription` — same caveat |
| User-input transcript | opt-in `conversation.item.input_audio_transcription.completed` (async) | opt-in `server_content.input_transcription` (async) |
| Server VAD / barge-in | `input_audio_buffer.speech_started`; `interrupt_response=true` auto-truncates; client sends `conversation.item.truncate{audio_end_ms}` | `server_content.interrupted=true` (server stops itself) |
| Tool declare | `session.tools=[{type:"function",name,description,parameters}]` | `LiveConnectConfig.tools=[{function_declarations:[…]}]` |
| Tool call | `response.function_call_arguments.done` + `function_call` output item | `message.tool_call.function_calls[]` |
| Tool result | `conversation.item.create{function_call_output}` + `response.create` | `session.send_tool_response(FunctionResponse)` |
| **Async / long-running tool** | **Native** — a pending call does not block; deliver the result late | `behavior=NON_BLOCKING` + `FunctionResponseScheduling.WHEN_IDLE` |
| Language re-pin mid-session | Live `session.update(instructions=…)` | **Locked at connect** — re-pin needs a reconnect carrying `SessionResumptionConfig(handle)` |
| Voice | Locked after the first audio response of a session | Fixed at connect (native-audio) |

Two facts drive the whole safety and language design:

1. The output transcript is **not** guaranteed to arrive before its audio is
   audible → scrub-before-heard requires an **audio hold gate** (§4.1).
2. Language re-pin is **asymmetric**: OpenAI live-updates, Gemini reconnects.

---

## 3. Architecture overview (the one reconciled design)

```
                       ┌─────────────────────────── jarvis/realtime/ (may import jarvis.*) ───────────────────────────┐
 desktop mic ─┐        │  RealtimeSessionEngine (transport-neutral turn loop)                                          │
 (MicCapture) │        │    audio_source  ─► provider.send_audio                                                        │
              ├─audio─►│    provider events ─► ScrubHoldGate ─► sink.play / send_binary   ◄─ audio out ─┐              │
 browser mic ─┘        │    tool_call ─► RealtimeToolBridge ─► ToolExecutor.execute (AP-3) │              │              │
 (/ws/audio)           │    barge-in ─► sink.flush (AudioPlayer.stop / tts_cancel)         │              │              │
                       │    language ─► resolve_output_language + repin (live | reconnect) │              │              │
                       │    RealtimeNameMap (MCP slash↔wire) · latency marks · stall guard  │              │              │
                       └───────────────────────────────────────────────────────────────────┼──────────────┘              │
                                                                                            │                             │
              jarvis/plugins/realtime/ (must NOT import jarvis.*, entry-point group "jarvis.realtime")                    │
                 openai_realtime.py (RealtimeProvider)      gemini_live.py (RealtimeProvider)                             │
```

**Home & layering.**
- `jarvis/realtime/` — the orchestrator package (engine, tool bridge, scrub gate,
  name map, factory, two surface adapters). It **may** import `jarvis.*`.
- `jarvis/plugins/realtime/` — the provider plugins, discovered via a **new
  `jarvis.realtime` entry-point group**. Per the structural plugin rule they
  **must not** import `jarvis.*`.
- This mirrors the existing plugin/orchestrator split used by channels, and
  keeps `jarvis/speech/pipeline.py` and `jarvis/browser_voice/` unchanged except
  for a single mode branch each.

**New plugin group + two-tier capability.**
- Add `"jarvis.realtime"` to `PLUGIN_GROUPS` (`jarvis/core/protocols.py:380`);
  `jarvis/core/registry.py` is fully generic and needs zero changes.
- Provider capability follows the existing two-tier pattern: a **static**
  `supports_realtime: bool` flag (like `supports_tools`/`supports_vision`) plus a
  **runtime** `async can_open_duplex_session() -> bool` probe (like
  `can_call_tools()`), disk-cached in `data/realtime_probe.json` and kept **off
  the boot path** (AP-26).
- Register one `Capability(id="realtime.duplex_voice", source="realtime",
  risk_tier="safe")` in the ADR-0017 CapabilityRegistry with **de/en/es** trigger
  vocabulary, so "start voice mode" / "modo de voz en vivo" is not falsely
  refused. (SR input vocabulary — multilingual tokens are allowed here.)

**Core contracts** (single source, in `jarvis/realtime/protocol.py`):

```python
RealtimeEventType = Literal[
    "audio_delta", "output_transcript_delta", "input_transcript",
    "tool_call", "speech_started", "interrupted", "turn_complete", "error"]

@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    type: RealtimeEventType
    audio: AudioChunk | None = None
    text: str | None = None
    is_final: bool = False
    tool_call: dict | None = None      # {"id","name","arguments"}
    call_id: str | None = None
    ms_played: int | None = None       # for truncate on barge-in
    error: str | None = None

@runtime_checkable
class RealtimeProvider(Protocol):       # the entry-point class
    name: str
    supports_realtime: bool
    input_sample_rate: int
    output_sample_rate: int
    async def can_open_duplex_session(self) -> bool: ...
    async def open_session(self, cfg: RealtimeSessionConfig) -> "RealtimeSession": ...

@runtime_checkable
class RealtimeSession(Protocol):        # the live duplex handle
    session_id: str
    async def send_audio(self, chunk: AudioChunk) -> None: ...
    def receive(self) -> AsyncIterator[RealtimeEvent]: ...
    async def send_tool_result(self, call_id: str, result: ToolResult, *, scheduling: str = "when_idle") -> None: ...
    async def update_session(self, *, instructions: str | None = None, language: str | None = None) -> None: ...
    async def truncate(self, item_id: str, audio_end_ms: int) -> None: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...
```

The engine is **transport-neutral** via an injected `RealtimeSink`
(`play` / `transcript` / `flush` / `status`) plus an `audio_source:
AsyncIterator[AudioChunk]`, exactly like `BrowserVoiceSession` is
transport-decoupled today. Desktop supplies a `MicrophoneCapture`-backed source
and an `AudioPlayer`-backed sink; the browser supplies a queue-backed source and
a `send_binary`/`send_json`-backed sink. **No `if provider == …` branching** in
the engine (AP-21).

---

## 4. The three risk areas, designed

### 4.1 Safety — the load-bearing audio-HOLD scrub gate

`scrub_for_voice` (`jarvis/brain/output_filter.py:442`) is regex-only (AP-11,
ADR-0010) and today runs on text before TTS. A duplex model speaks audio
directly, so safety is **three layers, in priority order**:

- **A — Instruction guardrail (primary, cheapest).** Bake a hard rule into
  `session.instructions` / `system_instruction`: never read tool JSON,
  function-call arguments, code, stack traces, file paths, base64, or raw URLs
  aloud; speak only a natural-language summary. This removes almost all leaks at
  the source. It is **not** sufficient alone.
- **B — Display/log gate (always on).** `ScrubHoldGate` accumulates transcript
  deltas and runs `scrub_for_voice` at sentence/segment boundaries; nothing
  reaches a chat bubble or a log line until it has passed scrub.
- **C — Audio hold gate (load-bearing).** Each decoded audio delta is
  **buffered, not played**, and released only once the co-timed transcript
  region has cleared scrub. A **HARD leak** (tool-JSON / stack-trace /
  shell-command / raw-repr classes) → `AudioPlayer.stop()/abort_active()` + drain
  the buffer + `response.cancel` + speak the localized fallback phrase (de/en/es).
  A **SOFT edit** (markdown/jargon/number) → clean only the displayed text; let
  audio flow. An **availability cap** (`transcript_lookahead_ms`, default 250 ms)
  releases held audio if a matching transcript never arrives, so a missing
  transcript can never deadlock playback.
- **Fail-closed:** if a provider/model offers **no** usable output-transcript
  side-channel, do **not** run raw duplex — degrade to the classic pipeline
  (scrub is load-bearing safety).

This resolves the review's #1 blocker: the "provider-side guardrails only" stance
is rejected; the hold gate is mandatory.

### 4.2 Tool-calling + routing — full parity through the same safety gate

One transport-agnostic core, `RealtimeToolBridge`, reuses the **live router
`BrainManager`'s** already-built tool dict, `ToolExecutor`, and deterministic
gates. Tools stay the single source of truth (`ROUTER_TOOLS`, ADR-0011); the
bridge never invents a second tool list.

1. **Deterministic pre-gate.** The pipeline's deterministic preamble
   (cancel-intent, provider/language/subagent switch, mission-command,
   `_should_force_spawn` heavy-task shortcut, evidence gate — all regex/registry,
   AP-11) **must** run in realtime too, or a spoken "switch to English" or a heavy
   build is mis-handled. It runs on the **input transcript**. To avoid a drifting
   copy, expose the preamble as one reusable path that both `generate()` and the
   bridge call — **guarded by characterization tests proving byte-identical
   classic behavior** before/after (review major #6). Default timing = **Tier B**
   (server-VAD auto-response on; cancel the half-started response only on the rare
   handled/heavy turn); **Tier A** (strict pre-LLM gate, no auto-response) is an
   opt-in for zero-latency heavy-task routing at the cost of per-turn input
   transcription latency.
2. **Declare `ROUTER_TOOLS` into each session** using the canonical schemas
   (`ToolUseLoop._tool_schemas`), mapping `input_schema → parameters` and
   wrapping per provider. MCP tools are named `server/tool` (slash illegal in
   provider function names) → a **bijective `RealtimeNameMap`** (`/`→`__`, collision
   fallback, reverse map) is **mandatory** (review blocker #3), then the existing
   `_resolve_tool` alias layer absorbs hyphen/underscore drift.
3. **Intercept every native function call → `ToolExecutor.execute()` (AP-3).**
   The provider SDK's auto-execution / auto-tool features are a **hard-OFF
   contract in every adapter** (declare functions only), asserted by a contract
   test that no execution path exists except `ToolExecutor.execute`. Risk-tier,
   plausibility, approval, and audit events fire identically to the classic path.
4. **Ask-tier spoken confirmation (the hard part).** The executor's two-turn
   `VOICE_CONFIRM_SENTINEL` stash/resume is transport-agnostic and unchanged. The
   bridge keeps a session-scoped `pending_confirm[trace_id]` map, holds
   auto-response while a confirmation is pending, and — for reliability — voices
   the confirmation question via **deterministic local TTS as the primary path**
   (socket muted for that beat), with `speak_exactly` as a secondary. The user's
   "ja/nein" is classified deterministically (`classify_response`, socket muted)
   → `execute_confirmed` / `cancel_pending`; "moved on" drops the pending action
   and runs the utterance as a normal turn. **A Gemini language reconnect is
   forbidden while a confirmation is pending** (would change call_ids); a test
   asserts pending-confirm survival across reconnect (review major #11).
5. **Fire-and-forget + async completion.** `spawn_worker`/`computer_use` return
   an immediate spoken ACK (the call is closed with the ACK); the minutes-later
   completion arrives on `JarvisAgentBackgroundCompleted` and is injected as a
   **new spoken turn** through a shared `speak_injected_turn` primitive that
   reuses the existing announcement policy (mute guard, `scrub_for_voice`,
   language resolution, floor-deferral).
6. **`session.instructions` = the conversational persona** (`persona_loader`) +
   the reply-language directive + a tiny realtime appendix — **not** the router
   pure-dispatcher prompt (which would suppress conversation). The ADR-0011
   dispatch guarantee is preserved by the deterministic pre-gate, not by the
   prompt. → **Amend ADR-0011** to record realtime as a router+responder-fused
   tier (review major #5).

**Input transcription is a hard requirement whenever realtime is active** — the
deterministic gates need the user text. If a provider/model cannot transcribe
input, fail closed to the classic pipeline (review major #7).

### 4.3 Barge-in, language, latency & stability

- **Barge-in:** provider server-VAD only. In realtime mode the local
  `_barge_monitor` (`pipeline.py:8082`) and local VAD **stay OFF** (no double
  mic / double VAD). OpenAI `speech_started` → `player.stop()/abort_active()` +
  `truncate{audio_end_ms = ms actually played}` (keeps the model's memory aligned
  with what was heard). Gemini `interrupted` → stop/abort only.
- **Language:** run `resolve_output_language` on the input-transcript event and
  re-pin **only on a substantive change** (thin interjections keep the sticky
  `conversation_language`). OpenAI = live `session.update(instructions)`; Gemini =
  reconnect with `SessionResumptionConfig`. First-turn "auto" (before any
  transcript) seeds from pin → prior `conversation_language` → `DEFAULT_LOCALE`,
  then self-corrects on the first transcript. One resolver, no per-layer
  re-derivation, no de/en-only phrase tables (CLAUDE.md §1.4).
- **Latency marks** (owned in one place — three additions to the single-source
  `LatencyPhase` enum, `events.py:942`): `REALTIME_INPUT_COMMITTED`,
  `REALTIME_FIRST_TRANSCRIPT`, `REALTIME_FIRST_AUDIO`. Reuse `AudioOutFirst`
  (published at the first **post-hold** audible sample = TTFW) and
  `BrainTTFT(source_layer="realtime.<provider>")`.
- **Stability:** per-turn stall guard (reset at `REALTIME_INPUT_COMMITTED`, never
  process-global — AP-19); socket-idle liveness → reconnect; exponential backoff
  reconnect (Gemini carries the resumption handle); bounded per-message sends
  (reuse `_WS_SEND_TIMEOUT_S`) both directions so a wedged client cannot
  back-pressure the provider stream; a bounded drop-oldest mic queue.
- **AP-26:** all provider SDK imports/clients are constructed **inside**
  `adapter.connect()`, never at module load, in `_run_backend`, the `WebServer`
  ctor, or `_start_speech_and_orb`. The capability probe is disk-cached and off
  the boot path.

---

## 5. Config surface, in-app recovery & fallback (AP-22)

- **Switch:** `[voice].mode = "pipeline" | "realtime"` (new `mode` field on
  `VoiceConfig`, which already has `extra="allow"`). It lives on the **voice**
  surface because realtime replaces the whole STT+Brain+TTS chain, not just the
  brain.
- **Provider preference + fallback:** `[brain.realtime]` = a `BrainTierConfig`
  block (reusing `provider` / `fallback_provider` / `fallback_provider_2`), so the
  cross-family chain is expressible (this is the only config shape that satisfies
  AP-22; review blocker #4).
- **Writer:** `config_writer.set_voice_mode(mode)` — TOML-only, not
  drift-guarded (modeled on `set_computer_use_engine`).
- **In-app:** `GET`/`PUT /api/settings/voice-mode` (modeled on
  `put_reply_language`); GET reports `{mode, realtime_available, active_provider}`
  from a dry probe so the UI can show "realtime unavailable — no OpenAI/Google
  key". A Voice-settings toggle drives it. The new routes must stay CLI-reachable
  (`check_cli_coverage.py`).
- **Resolution (capability-gated, AP-21):** iterate registered `jarvis.realtime`
  providers whose `supports_realtime` is true and whose key is present, ordered by
  `[brain.realtime]` preference, skipping dead/keyless — **never** a hardcoded
  name pair.
- **Retire** the dead `use_realtime_for_smalltalk` flag.

**Fallback matrix (never load-bearing):**

| Situation | Behavior |
|---|---|
| `[voice].mode = pipeline` (default) | Classic path, unchanged |
| Realtime on, preferred provider keyless/429/402/down | Cross to the other realtime family (key-aware) |
| No realtime family reachable | Return `None` → run the classic key-aware pipeline + one honest, language-resolved line |
| No output-transcript side-channel | Refuse raw duplex → classic pipeline (safety) |
| Mid-session socket death beyond max reconnects | Unwind to the classic pipeline for the next turn (in-app, no restart) |
| Headless VPS (no audio) | Desktop realtime = logged no-op; **browser** `/ws/audio` realtime still works (backend holds key, browser owns audio) |
| HARD leak in a transcript delta | Stop/abort + drain + cancel + localized fallback phrase |

**Three non-maintainer paths (CLAUDE.md §3), verified as one system:** (1) a user
with only a Gemini key → Gemini Live; only OpenAI → OpenAI Realtime; only
Anthropic/OpenRouter → `None` → classic pipeline + honest line. (2) headless
`python:3.11-slim` → base install boots (SDKs are base deps, lazy-imported);
desktop realtime no-ops, browser realtime works. (3) cross-family degrade is
honest and in-app.

---

## 6. Integration points & migration path

**New files** (all additive):
- `jarvis/realtime/__init__.py`, `protocol.py`, `engine.py`, `scrub_gate.py`,
  `tool_bridge.py`, `name_map.py`, `factory.py`, `desktop_adapter.py`,
  `browser_adapter.py`
- `jarvis/plugins/realtime/__init__.py`, `openai_realtime.py`, `gemini_live.py`
- `jarvis/ui/web/frontend/src/audio/pcm-worklet.ts` (**first-class Phase-1
  deliverable** — `/ws/audio` is built but frontend-unwired; without it neither
  the browser surface nor the headless-VPS path works)
- Tests: `tests/unit/realtime/*`, `tests/contract/test_realtime_provider_contract.py`

**Minimal edits to existing files** (mode branch + declarations only):
- `jarvis/core/protocols.py` — add the Protocols/event union + `"jarvis.realtime"`
  in `PLUGIN_GROUPS`.
- `pyproject.toml` — `[project.entry-points."jarvis.realtime"]` (then
  `pip install -e . --no-deps`).
- `jarvis/core/config.py` — `VoiceConfig.mode`; `BrainConfig.realtime:
  BrainTierConfig | None`; remove the dead flag.
- `jarvis/core/config_writer.py` — `set_voice_mode`.
- `jarvis/core/capabilities.py` + `capabilities_seed.py` — add `realtime` source +
  the seed.
- `jarvis/core/events.py` — three `LatencyPhase` members.
- `jarvis/speech/pipeline.py` — `_realtime_session()` sibling selected at the
  `_state_loop` seam (`:4736`) on `self._config.voice.mode`; local VAD/barge off in
  realtime.
- `jarvis/speech/watchdog.py:125` — pass `config=config` (the crash-restart root
  omits it today, so the fork would silently never fire there).
- `jarvis/browser_voice/route.py:44` — branch `_build_browser_session` on
  `cfg.voice.mode`; `/ws/audio` route + receive loop untouched.
- `jarvis/ui/web/settings_routes.py` — the two voice-mode routes.
- `docs/adr/0011` amendment + a new realtime ADR.

**Migration:** existing `jarvis.toml` needs no change (mode defaults to
`pipeline`); the retired flag is removed under `extra="allow"` after confirming no
reader references it.

---

## 7. Compliance with the anti-pattern register (from the adversarial review)

- **AP-3** — every tool call goes through `ToolExecutor.execute`; SDK auto-exec is
  hard-OFF; contract test enforces it.
- **AP-11 / ADR-0010** — scrub stays regex-only; the audio-hold gate makes it
  cover spoken output; refuse duplex without a transcript.
- **AP-21** — gate on `supports_realtime` capability + key presence, never a
  provider name.
- **AP-22** — `[brain.realtime]` cross-family chain; classic pipeline is the
  always-present final fallback; never a single-provider brick.
- **AP-23** — the three non-maintainer paths verified as one system; the
  AudioWorklet is a real deliverable, not an assumption.
- **AP-26** — lazy SDK imports, disk-cached probe, nothing on the boot path;
  keep the boot-budget gate green.
- **AP-18 / AP-20** — bus/socket teardown discipline reused; a dead receive stream
  is terminal (break), not re-polled.
- **ADR-0011** — router discipline preserved via the deterministic pre-gate;
  amend to record the fused router+responder tier.
- **ADR-0017** — realtime registered as a first-class capability with de/en/es
  vocabulary.
- **Cross-platform** — no hard `audioop` (deprecated, removed in 3.13); the
  16k→24k upsample uses a numpy-based resampler with a capability-guarded fallback.

---

## 8. Open decisions (with recommendations)

Exactly one recommendation each.

### 8.1 Routing timing — **Tier B (Recommended)** vs Tier A
**Recommended: Tier B** (server-VAD auto-response on; cancel only on the rare
handled/heavy turn). It keeps common turns at zero added latency; the wasted
half-started response on a heavy turn is dwarfed by the minutes-long mission.
Tier A is a config opt-in for deployments that prefer strict pre-LLM routing.

### 8.2 Gemini model family — **native-audio (Recommended)** vs half-cascade
**Recommended: native-audio** as the default for the most natural voice, with
half-cascade available as the "more robust tool use / safer transcript" option
when scrub-before-heard is safety-critical.

### 8.3 Desktop duplex — **half-duplex first (Recommended)** vs full-duplex now
**Recommended: half-duplex first** (mic gated while speaking) on desktop, because
there is no server/desktop AEC and full-duplex would echo the model's own voice
back into the mic. The **browser** surface gets full-duplex immediately via
Web-Audio `echoCancellation`. Full-duplex desktop ships behind a validated
AEC/ducking follow-up. This is the one place the "full parity, both surfaces, day
one" decision is only partially met on desktop — called out for sign-off.

---

## 9. Phased rollout with acceptance budgets

Each phase is independently shippable and default-OFF until proven.

**Phase 0 — Contracts & scaffolding.** Protocols, event union, plugin group,
config fields, capability seed, `LatencyPhase` members, empty engine + fakes.
*Exit:* `tests/contract/test_realtime_provider_contract.py` green; boot-budget
gate green (window ≤ 8 s, voice-usable ≤ 20 s); classic path byte-identical
(characterization tests).

**Phase 1 — Browser surface, one provider (OpenAI), conversation only.** Engine +
OpenAI adapter + `pcm-worklet.ts` + `ScrubHoldGate` + server-VAD barge-in +
language resolve/repin. No tools yet. *Exit budgets:* realtime TTFW p50 ≤ classic
pipeline TTFB p50; audio-hold added latency ≤ `transcript_lookahead_ms` (250 ms
default) on clean turns; zero heard HARD leaks in a leak-injection test; headless
VPS browser path driven end-to-end.

**Phase 2 — Tool parity.** `RealtimeToolBridge` + `RealtimeNameMap` + native
tool-call interception → `ToolExecutor` + fire-and-forget ACK + async completion
injection + the deterministic pre-gate (Tier B). *Exit:* every `ROUTER_TOOLS`
tool (incl. MCP slash names) resolves and executes only via `ToolExecutor`
(contract test); tool round-trip (call → execute → resumed audio) p50 budget
defined and met; force-spawn/switch parity tests green.

**Phase 3 — Ask-tier spoken confirmation.** Deterministic local-TTS question +
`classify_response` + `pending_confirm` map + Gemini-reconnect guard. *Exit:*
consequential action requires a spoken "ja" before executing; pending-confirm
survives a Gemini reconnect (test); no unaudited execution path exists.

**Phase 4 — Second provider (Gemini Live) + cross-family fallback.** Gemini
adapter + `SessionResumptionConfig` reconnect re-pin + `factory` cross-family
chain. *Exit:* AP-22 fallback tests (only-OpenAI / only-Gemini / only-Anthropic →
pipeline / dead-provider skip); Gemini reconnect duration budget met.

**Phase 5 — Desktop surface + in-app switch.** `_realtime_session()` sibling +
`watchdog.py` config fix + `set_voice_mode` + settings routes + UI toggle
(half-duplex on desktop per §8.3). *Exit:* in-app switch takes effect on the next
session without a `jarvis.toml` hand-edit; CLI coverage gate green.

**Stability SLOs to define per provider before GA:** acceptable reconnect rate on
long sessions, socket-idle/response-stall thresholds, dropped-frame ceiling before
degrading to classic.

---

## 10. Risks & mitigations (top)

| Risk | Mitigation |
|---|---|
| Transcript not before audible audio → heard leak | Load-bearing audio-HOLD gate; refuse duplex without a transcript |
| MCP slash names break tool declaration | Mandatory bijective `RealtimeNameMap` + parity test |
| Extracting the classic routing preamble regresses the hot path | Characterization tests prove byte-identical classic behavior, or call in place |
| Deterministic gates dead if input transcription is off | Input transcription is a hard requirement; else fail closed to pipeline |
| SDK auto-exec bypasses risk tiers (AP-3) | Hard-OFF contract in every adapter + no-auto-exec contract test |
| Single-provider brick (AP-22) | `[brain.realtime]` cross-family chain + pipeline final fallback |
| Boot regression (AP-26) | Lazy imports, disk-cached probe, boot-budget gate |
| Confirmation lost across Gemini reconnect | Forbid re-pin while pending; deterministic local-TTS question; survival test |
| Desktop echo (no AEC) | Half-duplex first on desktop; browser full-duplex via Web-Audio |
| `audioop` removed in Python 3.13 | numpy-based resampler, no hard `audioop` import |

---

## 11. Testing

- **Contract:** `test_realtime_provider_contract.py` (every provider is
  structurally conformant, `can_open_duplex_session` returns a bool when keyless).
- **Unit (fakes, not mocks):** scrub-hold gate (HARD stops audio, SOFT flows,
  availability cap), tool-bridge routing (all through a fake `ToolExecutor`, MCP
  names, no auto-exec), name-map bijectivity + collisions, voice-confirm state
  machine + reconnect survival, language repin (live vs reconnect, first-turn
  auto), stall/reconnect + bounded sends, factory AP-22 fallback.
- **Characterization:** classic `generate()` byte-identical before/after the
  preamble reuse.
- **Boot budget + CLI coverage** gates stay green.

---

## 12. Follow-up doctrine to write at implementation time

- **Amend ADR-0011** — realtime is a router+responder-fused tier; dispatch
  guarantee comes from the deterministic pre-gate, not a dispatcher prompt.
- **New ADR** — realtime voice mode (protocols, plugin group, audio-hold safety
  gate, cross-family fallback, both surfaces).
- **BUGS.md** — add the "heard-before-scrubbed duplex leak" class and the
  "double-mic in duplex" class.
