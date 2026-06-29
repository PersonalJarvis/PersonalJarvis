# Twilio Telephony Voice Agent — Design Spec

**Date:** 2026-05-24
**Status:** Approved (autonomous mode — user mandated no-questions execution)
**Goal:** A user calls a fixed Twilio phone number from any phone and talks to Jarvis as a real-time voice agent, using the **same** STT → Brain → TTS stack and the **same Charon voice** as the "Hey Jarvis" mic path. Plus a dedicated **Telephony section** in the desktop/setup app that lists Twilio credentials, status, scripts, and recent calls.

This spec is binding for the two-agent implementation team (backend "Tech" + frontend "UI", both Opus 4.7). The orchestrator validates and integrates.

---

## 1. Locked architecture decisions

- **AD-T1 — Twilio Media Streams, not ConversationRelay.** The binding requirement (Jarvis must speak in its own consistent Charon voice + use its own STT) is only satisfiable with raw audio over the wire. ConversationRelay forces Twilio's TTS voices and is therefore disqualified for the default path. *(ConversationRelay may be kept behind a config flag as a future degraded fallback — out of scope for v1, but `TwilioConfig.fallback_mode` is reserved.)*
- **AD-T2 — New `jarvis/telephony/` package; do NOT reuse `SpeechPipeline`.** `pipeline.py` hard-imports `sounddevice`/mic/speaker (`pipeline.py:32-35`, `audio/player.py:17`) → reusing it breaks the cloud-first €5-VPS doctrine. Instead compose the three decoupled seams directly:
  - STT: `build_stt_from_config(cfg.stt)` → `transcribe_pcm(pcm, sample_rate=16000)` (requires 16 kHz mono int16, `fwhisper.py:79/106`).
  - Brain: a **per-call** `BrainManager` via `build_default_brain(bus=..., tier="router")` → `generate_stream(text)` (`manager.py:2295`). Inherits force-spawn / `ROUTER_TOOLS` / Jarvis-Agents routing for free.
  - TTS: `build_tts_from_config(cfg.tts)` → `synthesize(text, language_code=...)` → `AsyncIterator[AudioChunk]` (24 kHz int16 PCM Charon).
- **AD-T3 — `scrub_for_voice` before every TTS call** (`jarvis/brain/output_filter.py`, regex-only, AP-11). Same as the mic path.
- **AD-T4 — Per-call BrainManager instance.** `BrainManager._history` is per-instance, not per-session (`manager.py:511`). A shared brain would interleave phone + desktop conversations. Each `TelephonyCallSession` owns its own brain instance, reused across turns within that call.
- **AD-T5 — Audio transcode via stdlib `audioop`** (Python 3.11 here) with `audioop-lts` declared only for `python_version>='3.13'`. μ-law 8 kHz ↔ linear PCM ↔ 16 kHz (STT) / 24 kHz (TTS). Persist `ratecv` state per stream+direction (no clicks). Pace outbound at ~20 ms / 160-byte frames.
- **AD-T6 — VAD endpointing reuse.** Use `from jarvis.audio.vad import SileroEndpointer` (headless-safe; `vad.py` does not import sounddevice). If it cannot run headless in the target env, fall back to an energy+silence-timeout endpointer. Barge-in: caller speech during TTS → send Twilio `{"event":"clear"}` + cancel the TTS task. Reuse "auflegen" hangup semantics from the mic path (regex hangup → end call).
- **AD-T7 — Five-layer enum for call status** (`docs/anti-drift-three-layer.md`): `CallStatus` single source in `jarvis/telephony/constants.py`, asserted in the Pydantic model, mirrored in `store/events.ts`, surfaced in UI labels. Values: `ringing | in_progress | completed | failed | no_audio`.
- **AD-T8 — `[telephony]` optional extras group** in `pyproject.toml` (`twilio`, `audioop-lts;python_version>='3.13'`). Routes degrade gracefully (feature-disabled JSON, no crash) when `twilio` is not importable — cloud-first "graceful degradation with a clear English message".
- **AD-T9 — Security.** Validate `X-Twilio-Signature` on the HTTP webhook against the **public** URL via `twilio.request_validator.RequestValidator`. The Media-Streams WS carries a per-call random secret (generated in the signed webhook, embedded as a `<Parameter>` / in the `wss` path) that the WS handler validates and binds to `CallSid`.
- **AD-T10 — No new hard runtime dep on the base install; no Anthropic hardcode** (AP-6 — brain runs through `cfg.brain.primary`; `generate_stream` already honors this).

---

## 2. Module layout (backend agent owns all of these)

```
jarvis/telephony/
  __init__.py          # feature flag / lazy twilio import guard, public API
  constants.py         # CallStatus enum (single source of truth, AD-T7)
  audio.py             # ulaw<->pcm, resample (ratecv state), 20ms frame chunking, pacing helpers
  session.py           # TelephonyCallSession: per-call STT->Brain->TTS turn loop, VAD endpoint, barge-in, hangup
  twiml.py             # build TwiML <Connect><Stream> + per-call secret
  security.py          # RequestValidator wrapper (public-URL aware) + WS secret check
  provisioning.py      # twilio.rest.Client: list available numbers, buy, set/inspect voice webhook, verify creds
  status.py            # TelephonyManager: runtime registry (active calls, last call, recent-call ring buffer, reachability cache)
  events.py            # frozen bus events: TelephonyCallStarted / TelephonyCallTurn / TelephonyCallEnded
jarvis/ui/web/telephony_routes.py   # FastAPI: webhook + media WS + REST status/config/credentials/test/calls
```

Plus edits to: `jarvis/core/config.py` (TwilioConfig + IntegrationsConfig.twilio + CHANNEL_SECRET_CANDIDATES), `jarvis/setup/wizard.py` (SecretSpec), `jarvis/ui/web/server.py` (mount router + `app.state.telephony_manager`), `pyproject.toml` / `requirements.txt`, `scripts/`, `docs/telephony.md`, `tests/`.

---

## 3. Config & secrets

`TwilioConfig` (sibling of `TelegramConfig` under `IntegrationsConfig`, `config.py:732`):
```python
class TwilioConfig(BaseModel):
    enabled: bool = False
    account_sid: str = ""           # AC... (account identifier, not a secret → ok in config)
    phone_number: str = ""          # E.164, e.g. +49...
    public_base_url: str = ""       # https://jarvis.example.com (no trailing slash); the URL Twilio reaches
    greeting: str = ""              # optional spoken welcome; empty = Jarvis butler default
    language_code: str = "de-DE"    # default TTS/STT language hint
    fallback_mode: str = "media"    # reserved: "media" (v1) | "conversationrelay" (future)
    max_call_seconds: int = 600     # safety cap to end runaway calls
```
- Secret: `twilio_auth_token` in Credential Manager (`CHANNEL_SECRET_CANDIDATES["twilio"] = (("twilio_auth_token","TWILIO_AUTH_TOKEN"),)`). Read via `get_secret("twilio_auth_token", "TWILIO_AUTH_TOKEN")`.
- Wizard: `SecretSpec(key="twilio_auth_token", env_fallback="TWILIO_AUTH_TOKEN", label="Twilio Auth Token", help_url="https://console.twilio.com", required_for=["telephony"], optional=True)`.
- Config mutations from the UI go through `jarvis/core/config_writer.py` only (AP-7). Never write `jarvis.toml` directly. Never accept the auth token over voice/chat (AP-2); the web admin POST that sets it is localhost-only and stores via `set_secret`.

---

## 4. SHARED REST API CONTRACT (both agents code to this — verbatim)

Base path `/api/telephony`. All JSON. Backend implements; frontend consumes.

### Twilio-facing (not consumed by UI)
- `POST /api/telephony/voice` — Twilio Voice webhook. Validates signature. Returns `text/xml` TwiML `<Connect><Stream url="wss://{public}/api/telephony/media"><Parameter name="secret" .../></Stream></Connect>`.
- `WS /api/telephony/media` — Twilio Media Streams socket. Handles `connected`/`start`/`media`/`mark`/`stop`, sends outbound `media`/`mark`/`clear`.

### UI-facing
- `GET /api/telephony/status` →
  ```json
  {
    "available": true,                 // twilio lib importable
    "configured": true,                // account_sid + phone_number + auth_token all present
    "enabled": true,
    "account_sid_masked": "AC••••••1234",
    "phone_number": "+49301234567",
    "public_base_url": "https://jarvis.example.com",
    "webhook_url": "https://jarvis.example.com/api/telephony/voice",
    "auth_token_set": true,
    "twilio_reachable": true,
    "twilio_error": null,
    "tts_provider": "gemini-flash-tts",
    "tts_voice": "Charon",
    "active_calls": 0,
    "max_call_seconds": 600
  }
  ```
- `GET /api/telephony/config` → the non-secret `TwilioConfig` fields + `"auth_token_set": bool`.
- `POST /api/telephony/config` — body: `{enabled, phone_number, public_base_url, greeting, language_code, max_call_seconds}` (partial allowed). Writes via config_writer. Returns the updated `GET /config` shape. Validates E.164 / URL; 422 on bad input.
- `POST /api/telephony/credentials` — body `{account_sid, auth_token}`. Stores `auth_token` via `set_secret`, `account_sid` via config_writer. Returns `{ok:true, configured:bool}`. (account_sid optional in body if already set.)
- `POST /api/telephony/test` — verifies Twilio creds by calling the REST API (`client.api.accounts(sid).fetch()` or `client.incoming_phone_numbers.list(limit=1)`). Returns `{ok:bool, reachable:bool, account_status?:string, error?:string}`.
- `POST /api/telephony/selftest` — runs a bundled recorded utterance WAV through STT→Brain→TTS (NO real call). Returns `{ok:bool, transcript:string, response_text:string, audio_bytes:int, error?:string}`. Powers the UI "Self-test voice" button and proves the chain + no text truncation.
- `GET /api/telephony/calls?limit=20` → `{calls: [{call_sid, from, to, started_at, ended_at, duration_s, status, turns}]}` from the in-memory ring buffer.
- `GET /api/telephony/scripts` → `{scripts: [{name, path, description, command}]}` — the setup helpers (cloudflared tunnel, provisioning script, Caddy snippet) for the UI to list with copy buttons.

When `available=false` (twilio not installed) or not `configured`, endpoints return `200` with the status flags set accordingly (no 500s); mutating endpoints may return `409` with `{error}` if prerequisites missing.

---

## 5. Frontend (UI agent owns all of these)

- `jarvis/ui/web/frontend/src/views/TelephonyView.tsx` — the section. Cards:
  1. **Status** — configured/enabled toggle, masked SID, phone number, public URL, webhook URL (copy button), Twilio-reachable badge (uses `CallStatus`/health colours), TTS voice shown so user sees it's Charon.
  2. **Credentials & config** — masked Auth-Token input, Account SID, phone number, public base URL, greeting, language → Save (POST /config + /credentials). "Test connection" → POST /test. "Self-test voice" → POST /selftest, render `transcript` + `response_text` (this is also the truncation check).
  3. **Setup scripts** — list from `GET /scripts`, each with description + copyable command (cloudflared, provisioning, Caddy).
  4. **Recent calls** — table from `GET /calls`.
- Register the section (anti-drift, all required):
  - `src/components/layout/MainView.tsx` — import + `case "telephony": return <TelephonyView />;`.
  - `src/store/events.ts` — add `"telephony"` to BOTH the `SectionId` union (~`:6`) and the `SECTION_IDS` array (~`:26`); add a `SECTION_LABELS` entry (~`:51`).
  - `src/components/layout/Sidebar.tsx` — `NAV_ITEMS` entry `{ id:"telephony", labelKey:"nav.telephony", icon: Phone }` (import `Phone` from `lucide-react`).
  - `src/i18n/locales/{de,en,es}.json` — `nav.telephony` + a `telephony.*` string block in all three (i18n key + English source; German/Spanish translations fine for user-facing locale values, but keys English).
- `src/views/TelephonyView.test.tsx` — vitest mirroring `ProfileView.test.tsx` (renders, shows status from a mocked fetch, save handler fires).
- **No text truncation** (explicit user requirement; cf. commit 44c955329): long phone numbers / SIDs / URLs must wrap or ellipsis-with-title, never get clipped. Test with a 40-char URL and a full SID.
- Follow existing view styling (mirror `ApiKeysView.tsx` / `SettingsView.tsx`); do not invent a new design language.

---

## 6. Public reachability + scripts (backend agent)

- `scripts/telephony-tunnel.ps1` — start a cloudflared (or ngrok) tunnel to the local FastAPI port, print the public URL to paste into `public_base_url`. Windows-dev tool (PowerShell ok per doctrine).
- `scripts/probe_telephony_e2e.py` — drives the WS handler with a synthetic Twilio call (recorded WAV → μ-law frames) and prints transcript + response + outbound-frame count. Cross-platform.
- `scripts/telephony_provision.py` — thin CLI over `provisioning.py` (list/buy/set-webhook). `sys.stdout.reconfigure(utf-8)` (Windows Unicode rule).
- `docs/telephony.md` — setup guide: VPS path (Caddy + Let's Encrypt, cloud-first default) FIRST, then home-PC tunnel path; trial-account caveats; German +49 regulatory-bundle note; wizard step.

---

## 7. Conventions to respect

`NO_WINDOW_CREATIONFLAGS` on any subprocess (AP-1); frozen `@dataclass` bus events with `trace_id`/`timestamp_ns` (AP-18, never propagate subscriber errors); five-layer enum for `CallStatus` (AP-4); `config_writer` for config writes (AP-7); `ToolExecutor` not `Tool.execute` (AP-3); no Anthropic hardcode (AP-6); no `sounddevice`/`SpeechPipeline` import on the telephony path (cloud-first); `scrub_for_voice` regex-only (AP-11); TTS voice-consistency mandate (Charon + seed/temperature from `cfg.tts`, do not re-fix via provider switch). All artifacts in **English** (Output Language Policy).

---

## 8. Definition of done

1. `pip install twilio` + `pip install -e . --no-deps`; new code imports clean.
2. `pytest tests/unit/telephony tests/integration/test_telephony*` green — covers: audio round-trip (μ-law↔PCM↔resample), TwiML generation, signature validation, the simulated-call session loop (STT→Brain→TTS with fakes), graceful-degradation when twilio absent.
3. `python scripts/probe_telephony_e2e.py` prints a transcript + a Jarvis response + N>0 outbound μ-law frames.
4. `npm run test` (vitest incl. TelephonyView.test) + `npm run build` green (no TS errors, no truncation).
5. Headless launcher boots; `GET /api/telephony/status` returns the contract JSON; `POST /api/telephony/voice` (with a valid signature, or signature check disabled in test) returns valid `<Connect><Stream>` TwiML.
6. TelephonyView renders in the desktop app without console errors; long values wrap (visual check by orchestrator).
7. `ruff check` + `mypy` clean on new modules. Wizard lists Twilio; config round-trips through config_writer.
8. `docs/telephony.md` + scripts present. The ONLY remaining user action is external account setup (create Twilio account, buy number, run wizard, start tunnel/deploy) — which is exactly what the Telephony section documents.

Real PSTN call testing requires a live Twilio account + public tunnel (user-side); the simulated-call E2E is the in-repo proof of correctness.
