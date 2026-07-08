# Grok Realtime provider (xAI Voice Agent) — design

**Date:** 2026-07-08
**Status:** approved for planning
**Area:** `jarvis.realtime` plugin group + API-Keys Realtime tab

## 1. Goal

Add **xAI Grok** as a third full-duplex realtime voice provider next to the
existing **OpenAI Realtime** and **Gemini Live** adapters, wired the same way as
the other two, without touching the classic pipeline path or the other
providers.

### Name disambiguation (load-bearing)

This is **Grok** (xAI, `wss://api.x.ai/v1/realtime`), NOT **Groq** (GroqCloud
LPU). Groq has no full-duplex realtime speech-to-speech API — only separate
Whisper STT + PlayAI TTS, which belong to the classic pipeline. Only Grok
belongs in the realtime tier. Groq STT already exists as a separate `stt`
provider (`groq-api`) and is out of scope here.

## 2. What we know (deep-dive findings)

- **Endpoint:** `wss://api.x.ai/v1/realtime?model=grok-voice-latest`
- **Auth:** `Authorization: Bearer <xai key>`
- **Audio:** 24 kHz PCM16 in and out (Linear16). The mic is 16 kHz, so we
  upsample 16 kHz → 24 kHz before send — identical to `openai_realtime.py`.
- **Protocol is OpenAI-Realtime-shaped:** a `session.update` message with
  `{voice, instructions, turn_detection}`, and output events named
  `response.output_audio.delta` / `response.output_audio_transcript.delta` /
  `response.done`, matching the OpenAI adapter's event names. (Confirmed by
  independent integrations: LiteLLM, LiveKit, Pipecat all treat it as a
  Realtime API.) Exact input-append event + transcription event names are
  verified against a live connection during implementation, not assumed.
- **Model id:** `grok-voice-latest`.
- **Voices:** `ara` (default), `rex`, `sal`, `eve`, `leo` — the same five as
  the existing `grok-voice` TTS provider.
- **Key already exists:** `grok_api_key` (used today by the `grok-voice` TTS
  provider). `get_provider_secret("grok")` already resolves it
  (`grok_api_key` → `GROK_API_KEY` → `xai_api_key` → `XAI_API_KEY`). **No new
  key slot.** A user who set up Grok Voice gets Grok Realtime for free.

## 3. Chosen approach — own WebSocket client (Option B)

Implement the transport with the already-present `websockets>=13` dependency,
speaking xAI's protocol directly, rather than bending the OpenAI SDK onto a
foreign endpoint (Option A, rejected). Rationale: full control, no dependency
on OpenAI-SDK internals holding against a third-party endpoint across SDK
upgrades (AP-28 spirit), and the protocol is small enough that a direct client
is clear and fully testable.

The Gemini adapter already establishes the precedent that a non-OpenAI realtime
provider is its own self-contained module; this mirrors that shape with a raw
WS client instead of a vendor SDK.

## 4. Components

### 4.1 `jarvis/plugins/realtime/grok_realtime.py` (new)

Structural twin of `openai_realtime.py`, implementing the `RealtimeProvider` /
`RealtimeSession` protocols from `jarvis/realtime/protocol.py`. Must NOT import
`jarvis.*` beyond `get_provider_secret`, `AudioChunk`, and the realtime
protocol types. `import websockets` stays **lazy inside `open_session`**
(AP-26 — nothing heavy at module import).

- `GrokRealtimeProvider`: `name="grok-realtime"`, `supports_realtime=True`,
  `input_sample_rate=24000`, `output_sample_rate=24000`,
  `can_open_duplex_session()` → `bool(get_provider_secret("grok"))`.
- `open_session(cfg)`: open the WS with the Bearer header, send the initial
  `session.update` built from `cfg` (instructions, voice, turn_detection),
  return the session handle.
- `_GrokRealtimeSession`:
  - `send_audio`: resample 16 kHz → 24 kHz if needed (reuse
    `jarvis.telephony.audio.resample_pcm16`, lazy import, exactly like the
    OpenAI adapter), base64-encode, send the input-audio append message.
  - `receive`: async-iterate WS frames, `json.loads`, map xAI event types onto
    the neutral `RealtimeEvent` (`audio_delta`, `output_transcript_delta`,
    `input_transcript`, `speech_started`, `turn_complete`, `error`).
  - `update_session` / `truncate` / `interrupt` / `close`: send the matching
    control messages; any not exposed by xAI degrade to an honest no-op (same
    pattern the Gemini adapter uses for unsupported controls).

### 4.2 `jarvis/realtime/factory.py` (edit)

Add a third family to `_ordered_families`:
`("grok-realtime", "grok", GrokRealtimeProvider)` with a lazy import at the top
of that function alongside the other two. The existing key-aware, cross-family
resolver (AP-22) then picks up Grok automatically — the configured
`[brain.realtime].provider` wins when keyed, otherwise it crosses to whichever
family has a key. No other change; ordering stays shared between the session
builder and the availability check.

### 4.3 `jarvis/ui/web/provider_spec.py` (edit)

Add one `ProviderSpec` in the Realtime section:

```
ProviderSpec(
    id="grok-realtime",
    label="Grok Realtime (xAI)",
    tier="realtime",
    auth_mode="api_key",
    secret_keys=("grok_api_key",),
    dashboard_url="https://console.x.ai/",
    credential_help=(
        "xAI API key (starts with xai-), shared with Grok Voice TTS, to power "
        "Grok's full-duplex realtime voice — one model that listens, thinks and "
        "speaks over a single connection. Default-OFF until the realtime client "
        "is wired in (Phase 2)."
    ),
)
```

The UI renders the card automatically from this spec — no ApiKeysView change
needed. All copy is English (§1).

### 4.4 `jarvis/brain/model_catalog.py` (edit)

- `REALTIME_MODELS["grok-realtime"] = _curated([("grok-voice-latest",
  "Grok Voice (default)")])` — default first, matches the adapter's `_MODEL`.
- `REALTIME_VOICES["grok-realtime"] = _ids(["ara", "rex", "sal", "eve",
  "leo"])` — `ara` first (xAI default).

### 4.5 `pyproject.toml` (edit)

Register the entry-point under `[project.entry-points."jarvis.realtime"]`:
`grok-realtime = "jarvis.plugins.realtime.grok_realtime:GrokRealtimeProvider"`.
After editing entry-points, `pip install -e . --no-deps` is required for the
live interpreter to see it (BUG-006/014).

### 4.6 `scripts/ci/privacy_gate/references/distribution-denylist.txt` (edit)

Add `grok_realtime.py` next to the other withheld realtime modules, keeping the
realtime engine consistently held back from the public snapshot (nothing
deleted, just not shipped — matches the current release posture).

## 5. Data flow

Unchanged from the existing realtime path — Grok is a drop-in third family:

```
browser mic (16 kHz PCM)
  → /ws/audio → RealtimeVoiceSession → GrokRealtimeProvider.open_session
  → _GrokRealtimeSession.send_audio (upsample 24 kHz, b64, WS send)
xAI WS events → receive() → RealtimeEvent → scrub_gate → browser (24 kHz out)
```

`build_realtime_session` still returns `None` (→ classic pipeline) whenever
voice mode ≠ realtime or no realtime key exists in any family, so the pipeline
is never affected.

## 6. Error handling

- No `grok_api_key` → `can_open_duplex_session()` is False and the factory
  crosses to another keyed family, or degrades to the classic pipeline. Never
  bricks (AP-22).
- WS connect/handshake failure inside `build_realtime_session` is already
  caught (`except Exception → None → classic path`). Within the session, a WS
  error frame maps to `RealtimeEvent(type="error")` and a dropped connection
  surfaces as a terminal read error (treat as terminal, do not loop — AP-20).
- Capability-gated throughout: the factory keys on `get_provider_secret("grok")`
  presence, never on a provider name (AP-21).

## 7. Testing

- **Unit** `tests/unit/realtime/test_grok_realtime.py`: a fake WS transport
  (no network) asserts the initial `session.update` payload, mic upsampling,
  and event mapping onto `RealtimeEvent` — mirroring
  `test_openai_realtime.py`.
- **Contract** `tests/contract/test_realtime_provider_contract.py`: extend so
  `GrokRealtimeProvider` is checked against the `RealtimeProvider` protocol
  (sample rates, `supports_realtime`, key-gated availability).
- **Factory** `tests/unit/realtime/test_factory.py`: Grok appears as a third
  family and is resolved when only `grok_api_key` is present; ordering stays
  consistent between resolver and availability check.
- **Model catalog** `tests/unit/brain/test_model_catalog.py`: Grok realtime
  models/voices present, defaults first.
- **Live connection smoke check** (manual, during implementation, gated on a
  real `xai-` key): confirm the WS handshake, the exact input-append and
  transcription event names, and one round-trip before claiming done — so we
  never ship an unverified protocol assumption (§3 definition-of-done).

## 8. Out of scope / non-goals

- The Phase-2 browser audio client (actually talking end-to-end) is a separate,
  already-tracked effort — this task makes Grok *selectable and buildable*, same
  default-OFF posture as OpenAI Realtime and Gemini Live today.
- Groq (GroqCloud) STT/TTS pipeline work — different provider, different tier.
- No change to the classic pipeline, the other realtime providers, or the
  two-mode API-Keys UI shell.
