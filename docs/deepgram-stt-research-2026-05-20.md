# Deepgram STT — Research Dossier (2026-05-20)

**Status:** Research-only. No code written, no plugins built, no tests run.
**Trigger:** User wants Deepgram STT brought to the same level as the Groq integration. Two explicit research questions:
1. Is Deepgram's **token / auth flow** different from Groq's simple static API key?
2. Is Deepgram **agent-friendly** — does it deliver transcript tokens *while* the user is speaking (streaming / interim), not only after the utterance finishes?

Produced by 4 parallel read-only research sub-agents (Auth, Streaming, Repo-State, Decision-Context).

---

## TL;DR — answers to your two questions

1. **Auth flow: YES, different — but not a problem for us.** Deepgram uses `Authorization: Token <key>` (not `Bearer`), and additionally supports short-lived ephemeral JWTs (`POST /v1/auth/grant`, 30 s default, 1 h max). For a *server-side* Python plugin (which is what Jarvis is) the static key with `Token` prefix is essentially identical effort to Groq. The ephemeral-token complexity only matters if you ship the key to a browser.

2. **Agent-friendly streaming: YES, fully.** `interim_results=true` streams partial transcript tokens within ~150 ms while you are still speaking. `is_final`/`speech_final` flags mark preliminary vs committed. The **Flux** model goes further: built-in end-of-turn detection + `EagerEndOfTurn` so the LLM can start *before* you finish talking.

3. **The surprise: Deepgram is NOT half-built in our repo — it does not exist at all.** The three `deepgram*` entry-points in `pyproject.toml` are "ghost registrations" pointing at Python files that are absent from disk. And the speech pipeline is **batch-only** today — it has no path to consume streaming interim results from any provider. So this is a from-scratch build, not a finish-the-stub job.

---

## Question 1 — Token / Auth Flow (vs Groq)

### What Deepgram does
- **Standard API key:** header `Authorization: Token <DEEPGRAM_API_KEY>` — note the literal word `Token`, **not** `Bearer`. Long-lived, project-scoped.
- **Ephemeral / temporary tokens (JWT):**
  - Endpoint: `POST https://api.deepgram.com/v1/auth/grant`
  - Mint auth: `Authorization: Token <master_key>` (needs Member permission)
  - Body: `{ "ttl_seconds": <n> }` — default **30 s**, max **3600 s (1 h)**
  - Response: `{ "access_token": "<JWT>", "expires_in": <s> }`
  - Used as: `Authorization: Bearer <JWT>` (Bearer here, not Token)
  - Scope: `usage:write` only — fine for STT/TTS, not for management APIs
  - The JWT only needs to be valid during the **WebSocket handshake**; the connection survives token expiry afterwards.
- **WebSocket auth** (`wss://api.deepgram.com/v1/listen`):
  - Server-side: `Authorization: Token <key>` header on the upgrade → trivial
  - Browser: `Sec-WebSocket-Protocol: token, <key>` workaround (browsers can't set Authorization on WS) → only relevant if Jarvis ever runs STT client-side
- **Project / key scoping:** per-key scopes (`usage:read`, `usage:write`, `keys:write`). Max 250 temp keys/day per project — Deepgram recommends the `/v1/auth/grant` JWT flow instead.

### Groq vs Deepgram (auth)

| Aspect | Groq (current) | Deepgram |
|---|---|---|
| Header | `Authorization: Bearer <key>` | `Authorization: Token <key>` (key) / `Bearer <JWT>` (temp) |
| Key longevity | static long-lived | static OR 30 s–1 h JWT |
| Temp-token endpoint | none | `POST /v1/auth/grant` |
| WS auth | same bearer | `Token` header OR `Sec-WebSocket-Protocol` (browser) |
| Effort for server-side STT | very low | low (static key) / medium (JWT path) |

**Verdict:** the "different token flow" your gut flagged is real (the `Token`/`Bearer` distinction + the optional JWT mint), but for our server-side use it is a one-line header difference. The ephemeral-JWT path is optional hardening we can skip in v1.

---

## Question 2 — Agent-Friendliness / Streaming Tokens During Speech

**Confirmed from docs:** Deepgram streams transcript tokens during speech.

- **Parameter:** `interim_results=true` on `/v1/listen`.
- **Behaviour:** every ~1 s audio window emits a message with `is_final` + `speech_final` flags. Words can be revised in later messages as more audio arrives.
  - `is_final=false` → preliminary, will be refined
  - `is_final=true, speech_final=false` → segment locked, speaker still talking
  - `is_final=true, speech_final=true` → silence detected, utterance boundary
- **Endpointing:** `endpointing=<ms>` (silence to trigger `speech_final`), `utterance_end_ms` + `UtteranceEnd` events.
- **Latency:** ~150 ms time-to-first-interim-token; < 300 ms target; 200–500 ms end-to-end. Flux end-to-end ~260 ms.
- **Flux** (`wss://.../v2/listen`): a conversational-speech-recognition model with **built-in turn detection**:
  - `EndOfTurn` (speaker done, configurable `eot_threshold` ≈ 0.7)
  - `EagerEndOfTurn` (fires early → LLM can start drafting before user fully stops)
  - `TurnResumed` (user kept talking → cancel the draft)
  - This is exactly the "react while speaking" behaviour you described — Flux would *replace* the current Silero-VAD layer.
- **German / bilingual:** Nova-3 has a dedicated German streaming model AND a multilingual variant with native **DE+EN code-switching**. **Flux is English-only at launch** (German on Nova-3 only for now).

**Verdict:** your "agent-friendly = tokens during speech" requirement is met — by `interim_results` on Nova-3 (DE+EN), or by Flux's eager-EOT (EN only today).

---

## The Repo Reality Check (most important finding)

The sub-agent that read the actual codebase found that **Deepgram has zero source code in the repo**:

- `pyproject.toml` (lines ~109-115) registers `deepgram`, `deepgram-flux`, `deepgram-nova3` (plus `openai-api`) — **all point to Python modules that do not exist on disk.**
- The contract test [`tests/contract/test_stt_protocol.py:44-47`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/tests/contract/test_stt_protocol.py) literally documents them as ghost registrations and excludes them from `EXPECTED_PROVIDERS = {"groq-api"}`.
- The `jarvis.toml` comments (`[turn] provider = "flux_integrated"`, the `deepgram-flux`/`deepgram-nova3` mentions) describe an **intended** architecture that was never built. `flux_integrated` is currently a NoOp plugin.
- The wizard ([`wizard.py:87`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/setup/wizard.py)) and provider-spec UI already know the credential key name `deepgram_api_key` (ENV `DEEPGRAM_API_KEY`) — so the secret plumbing is ready, only the plugin module is missing.

**And the pipeline is batch-only:**
- `_handle_utterance` ([`pipeline.py:~1929`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/speech/pipeline.py)) calls `self._utterance_stt.transcribe_pcm(full_pcm, ...)` — one call, one final transcript, after the mic closes.
- `stream_transcribe` exists in the protocol + Groq's shim but is **never called**.
- There is no code path that reads interim transcripts during speech, regardless of provider.

### Groq = the parity target (what a finished Deepgram plugin needs)
The working [`groq_api.py`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/plugins/stt/groq_api.py) has: 3-tier auth (ctor → ENV → keyring `groq_api_key`), `transcribe`, `stream_transcribe` shim, `transcribe_pcm`, `_ensure_model` no-op, `aclose`, `supports_streaming` class attr, name matching the entry-point, no `jarvis.*` imports, wizard + provider-spec UI entries, and 8 contract tests.

### STT protocol contract ([`protocols.py:220-235`](file:///C:/Users/Administrator/Desktop/Personal%20Jarvis/jarvis/core/protocols.py))
Required: `name: str`, `supports_streaming: bool`, `async transcribe(AsyncIterator[AudioChunk]) -> Transcript`, `async stream_transcribe(...) -> AsyncIterator[Transcript]`. Plus the pipeline-practical `transcribe_pcm(...)` + `_ensure_model()`.

---

## Decision Context (pricing / models / latency)

| Provider / Model | Mode | $/min | $/hr | German |
|---|---|---|---|---|
| Groq Whisper Turbo (current) | batch | ~$0.00067 | $0.040 | yes |
| Deepgram Nova-3 Mono | streaming | $0.0048 | $0.288 | yes (dedicated DE) |
| Deepgram Nova-3 Multi | streaming | $0.0058 | $0.348 | yes (DE+EN code-switch) |
| Deepgram Flux (EN) | streaming CSR | $0.0065 | $0.390 | **no (EN only)** |

- **Groq is ~7× cheaper per minute** — but it's batch (apples-to-oranges).
- **Free tier:** Deepgram $200 credit, no card (~680 h of nova-3 streaming).
- **EU endpoint:** `api.eu.deepgram.com` GA, no waitlist (Whisper models NOT on EU; Nova-3/Flux are).
- **Latency:** Groq returns only after the full utterance + HTTP round-trip; Deepgram shows partials during speech and fires EOT instantly → 300–800 ms lower perceived latency per turn.

**Where Deepgram-streaming wins:** sub-second perceived latency, barge-in/interruption support, bilingual DE+EN code-switch.
**Where Groq-batch is fine:** cost-bound transcription, and *not rebuilding the pipeline* (Groq swap is one line; Deepgram streaming needs pipeline rewiring).

---

## What a future implementation would have to cover (NOT done — for the next decision)

1. Build `jarvis/plugins/stt/deepgram.py` (batch REST, `name="deepgram"`, keyring `deepgram_api_key`, full protocol surface, no `jarvis.*` imports) — the Groq-parity baseline.
2. Build `jarvis/plugins/stt/deepgram_nova3.py` (batch Nova-3).
3. Build `jarvis/plugins/stt/deepgram_flux.py` (WebSocket streaming + EOT) — the hard one, and the one that delivers "tokens while speaking".
4. **Rewire the pipeline** to consume `stream_transcribe` / interim results + publish partial `TranscriptionUpdate` events — without this, streaming STT cannot be used regardless of provider.
5. Contract-test coverage: add deepgram entries to `EXPECTED_PROVIDERS` + mocked-HTTP functional tests mirroring the 8 Groq tests.
6. Config: `[stt]` keys for `model` (nova-3 / flux), `endpointing_ms`, `language` (de / multi), optional EU endpoint toggle.
7. Decide auth mode: static `Token` key (simple, v1) vs ephemeral JWT (`/v1/auth/grant`, hardening).

---

## Sources (verified from official docs)
- Auth: [Authentication ref](https://developers.deepgram.com/reference/authentication), [Token-based auth](https://developers.deepgram.com/guides/fundamentals/token-based-authentication), [/v1/auth/grant](https://developers.deepgram.com/reference/auth/tokens/grant), [Sec-WebSocket-Protocol](https://developers.deepgram.com/docs/using-the-sec-websocket-protocol)
- Streaming: [Live streaming](https://developers.deepgram.com/docs/live-streaming-audio), [Interim results](https://developers.deepgram.com/docs/interim-results), [Endpointing](https://developers.deepgram.com/docs/endpointing), [Flux quickstart](https://developers.deepgram.com/docs/flux/quickstart)
- Models/German: [Nova-3 German](https://deepgram.com/learn/deepgram-expands-nova-3-with-german-dutch-swedish-and-danish-support), [Multilingual code-switching](https://developers.deepgram.com/docs/multilingual-code-switching)
- Pricing/Decision: [Pricing](https://deepgram.com/pricing), [Flux intro](https://deepgram.com/learn/introducing-flux-conversational-speech-recognition), [EU endpoint GA](https://deepgram.com/learn/deepgram-eu-endpoint-now-generally-available)
- Repo evidence: `tests/contract/test_stt_protocol.py:34,44-47`, `pyproject.toml:109-115`, `jarvis/plugins/stt/groq_api.py`, `jarvis/core/protocols.py:220-235`, `jarvis/speech/pipeline.py:~1929`

---

## Summary (non-technical)

Both of your hunches are correct: Deepgram authenticates differently from Groq (a different sign-in keyword plus optional short-lived keys), and it does in fact deliver words while you are still speaking — not just at the end. The big surprise of this research: Deepgram is not present in our project at all, even though the settings file pretends otherwise — there are only empty references to files that were never built. On top of that, our speech system can currently only process fully spoken sentences, no word-by-word live reading — that would first have to be rebuilt for true live listening.
