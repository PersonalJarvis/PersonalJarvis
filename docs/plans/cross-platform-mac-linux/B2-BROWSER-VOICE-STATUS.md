# B2 — Browser-microphone/speaker voice bridge — status

Closes the cloud-first gap the deep-dive audit flagged: a browser user on a
headless VPS could previously only **text**-chat; the STT→Brain→TTS core that the
Twilio telephony bridge proves over a socket was never built for the browser.
It now is.

## Done + verified (committed)

| Slice | Commit | What | Verification |
|---|---|---|---|
| 1 — backend core | `6284f2cd` + `2d7146a2` | `jarvis/browser_voice/{session,audio,__init__}.py` — `BrowserVoiceSession` ports `TelephonyCallSession` to browser audio: raw int16 PCM → 16 kHz resample → `EnergyEndpointer` → STT → brain → `scrub_for_voice` → TTS → binary frames back. Stdlib `audioop` only, **never** imports sounddevice. | `tests/unit/browser_voice/test_session.py` (9 tests), import-clean gate, ruff — all green |
| 2 — route + wiring | `2d7146a2` | `jarvis/browser_voice/route.py` — `/ws/audio` WS (binary PCM + JSON control), AP-20 break discipline, shared STT/TTS + per-connection brain, test-factory seam. Mounted in `server.py` `_build_app`, gated by `[browser_voice].enabled` (default on). | `tests/unit/browser_voice/test_route.py` (4 tests), `/ws/audio` registered |
| 3 — frontend | `c43bed42` | `src/hooks/useBrowserVoice.ts` (AudioWorklet capture → WS; Web Audio gapless playback; `tts_cancel` flush) + `src/views/BrowserVoiceView.tsx`. | `tsc --noEmit` clean on both files |

The wire protocol on `/ws/audio`:

- **Browser → server:** binary frames = raw int16 LE PCM at the AudioContext rate
  (server resamples to 16 kHz). JSON control: `audio_start` (`sample_rate`,
  `language`), `barge_in`, `audio_stop`.
- **Server → browser:** binary frames = 24 kHz int16 PCM (TTS). JSON control:
  `audio_ready`, `transcript` (`text`, `is_final`), `tts_start` (`sample_rate`),
  `tts_end`, `tts_cancel` (flush on barge-in), `vad_silence`.

## Open — needs the maintainer (two genuine, non-code blockers)

### 1. Real-browser smoke test (parallel to the Mac/Linux hardware sign-off)
The AudioWorklet, `getUserMedia`, and Web Audio playback **cannot** run in
jsdom/Vitest — they need a real browser on a secure context (`localhost` or
`https`; AudioWorklet refuses an insecure origin). To verify end-to-end: serve
the app, open the (wired) Browser Voice view, grant the mic, speak, and confirm
TTS plays back. Until then the frontend is *compile-verified, runtime-unverified*.

### 2. Sidebar/section wiring (deferred — contested files)
`store/events.ts`, `App.tsx`, `Sidebar.tsx`, and `i18n/locales/*.json` were all
being actively edited by parallel sessions when this landed; committing edits to
them would risk sweeping that in-flight work (and `git add -p` hunk-isolation is
unavailable in this runtime). Apply these **four small edits when those files are
quiet**:

1. **`src/store/events.ts`** — add `"browser-voice"` to the `SectionId` union,
   to `SECTION_IDS`, and a `SECTION_LABELS["browser-voice"]` entry.
2. **`src/App.tsx`** (the view switch) — `import BrowserVoiceView from
   "@/views/BrowserVoiceView";` and add `case "browser-voice": return
   <BrowserVoiceView />;`.
3. **`src/components/layout/Sidebar.tsx`** — add a nav entry for `"browser-voice"`
   (a microphone icon, label key `browser_voice.title`).
4. **`src/i18n/locales/{en,de,es}.json`** — add the `browser_voice` block
   (`BrowserVoiceView` falls back to the English source until these land):

```jsonc
// en.json
"browser_voice": {
  "title": "Browser Voice",
  "subtitle": "Talk to Jarvis using your browser's microphone and speakers — no desktop install. Requires a secure context (localhost or https).",
  "start": "Start voice", "stop": "Stop", "listening": "Listening…", "speaking": "Speaking…"
}
// de.json
"browser_voice": {
  "title": "Browser-Sprache",
  "subtitle": "Sprich mit Jarvis über das Mikrofon und die Lautsprecher deines Browsers — ohne Desktop-Installation. Erfordert einen sicheren Kontext (localhost oder https).",
  "start": "Sprache starten", "stop": "Stopp", "listening": "Höre zu …", "speaking": "Spreche …"
}
// es.json
"browser_voice": {
  "title": "Voz del navegador",
  "subtitle": "Habla con Jarvis usando el micrófono y los altavoces de tu navegador, sin instalación de escritorio. Requiere un contexto seguro (localhost o https).",
  "start": "Iniciar voz", "stop": "Detener", "listening": "Escuchando…", "speaking": "Hablando…"
}
```

## Deferred low-priority follow-ups (from the slice-1 review)

- **`[browser_voice]` config schema** — the route defaults to *enabled* when the
  section is absent, so no schema change is required to ship; add a typed
  `[browser_voice].enabled` to `JarvisConfig` + `jarvis.toml` if an off-switch is
  wanted (the config schema file is itself frequently contested).
- **`audioop` on Python 3.13+** — `jarvis/browser_voice/audio.py` re-exports the
  telephony audio layer, which already handles the `audioop-lts` fallback. On a
  base install (no `[telephony]` extra) on Python ≥3.13, surface a clearer
  message or pull `audioop-lts` into a `[browser_voice]` extra. Not an issue on
  the 3.11/3.12 VPS target (stdlib `audioop`).
