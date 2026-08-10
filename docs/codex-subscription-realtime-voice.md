# ChatGPT-plan realtime voice through Codex — REMOVED

Status: removed 2026-08-10 (adapter, card, catalogs, probes, and tests)
Previous status: experimental, opt-in, pinned to audited Codex CLI
0.147.0/0.146.0 builds; card withdrawn from the settings UI earlier the
same day.

## What was removed and why

The `codex-subscription-realtime` adapter carried live speech-to-speech
voice over `codex app-server`'s experimental `thread/realtime` surface —
the only legitimate subscription-routed voice transport OpenAI exposes.
After the 2026-08-02/03 client-side fix wave (45 s handshake budget, warm
transport, startup pre-roll, verbatim PCM forwarding, correct multi-part
turn boundaries), every remaining defect was a property of the wire, not
of this client:

- **No turn-boundary events.** A live protocol dump (2026-08-06/07)
  confirmed the complete notification vocabulary is `started`, `sdp`,
  `transcript/delta`, and `transcript/done`. There are no `response.*`
  items, so the 1.2 s audible-quiescence backstop IS the turn boundary —
  a structural per-turn latency floor no client can remove.
- **Upstream media gaps.** Sessions with a measured 0 ms local hold still
  carried embedded silent PCM spans of 0.4–5.9 s and provider arrival gaps
  of 577/781 ms already present in (or absent from) the Codex media
  stream.
- **Cold starts of 15–25 s** for the pinned app-server spawn, live account
  verification, and WebRTC negotiation — maskable by warming, never
  removable.
- **No native tool calling.** The RPC surface
  (`start/appendAudio/appendText/appendSpeech/stop`) refuses custom
  fields; every action cost one extra supervisor brain turn.
- **No alternative subscription path exists** (verified 2026-08-10):
  the GPT-Live developer API is unreleased, ChatGPT Voice in Desktop is
  app-only and not reachable via any API or third-party integration, and
  Codex 0.147/0.148-alpha did not extend the realtime protocol.

The maintainer decision followed: subscription voice must not ship on a
transport that cannot hold a dependable call.

## What remains

- **The stable subscription composition** (`voice.profile =
  "codex-subscription-voice"`): classic SpeechPipeline capture/STT/TTS
  with conversational turns generated over the audited Codex app-server
  TEXT protocol on the dedicated, isolated ChatGPT voice login. This is
  the supported way to run voice on the ChatGPT plan. See
  `jarvis/voice/subscription_profile.py`.
- **API realtime voice** (`openai-realtime`, `gemini-live`,
  `local-realtime`): unchanged, metered or self-hosted.
- **The isolated `codex-subscription-voice` login profile**, its
  provider routes (`/api/providers/codex/subscription-voice/*`), status
  payloads, and the hardened app-server process containment in
  `jarvis/codex_app_server.py` — all shared with the stable composition.
  The unused `thread/realtime` RPC helpers inside `codex_app_server.py`
  remain as inert protocol-client surface; excising them is bounded
  follow-up work, not a runtime risk.

## Migration

`load_config` routes a config still pinning the removed provider onto the
stable composition (`migrate_removed_codex_realtime_provider`): the pin is
cleared from every explicit `[brain.realtime]` slot, and a PRIMARY pin
additionally lands on `voice.profile = "codex-subscription-voice"` with
Pipeline mode, keeping voice on the same ChatGPT login instead of booting
into a dead Realtime mode. Other providers' selections are never touched.

## History

The complete engineering record of the adapter — runtime design, failure
assessments with measurements, the client-managed handoff contract, the
credential/process boundaries, and the live-confirmed v3 protocol dump —
lives in this file's git history (see the version before this removal)
and in `docs/BUGS.md`. The generation-side lessons remain valid for any
future external-login realtime provider:

- A status surface must never convert a transient `busy` into "ready" or
  "broken"; only the session opener judges live.
- Audio without a matching transcript fails closed through the scrub
  gate; recovery transcription is the escape hatch, not trust.
- Desktop half-duplex plus a wrong turn boundary makes the microphone
  genuinely deaf — turn boundaries must come from terminal events or
  audible quiescence, never per-part transcript markers.
