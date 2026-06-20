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
| 3 — review fixes | `76331f9a` | BLOCKER + 3 HIGH + 3 MEDIUM from the frontend-hook review: generation-guarded start/stop (no leaked mic on a slow permission prompt), playback-flush race, AudioContext resume (else TTS silent in Chrome/Safari), etc. | `tsc` clean |
| 4 — UI wiring | `b1d67b36` | `store/events.ts` (SectionId/SECTION_IDS/SECTION_LABELS) + `MainView.tsx` (view switch) + `Sidebar.tsx` (nav entry, Headphones icon, English label via `fallbackLabel`). | `tsc` 0 errors (whole project) + `npm run build` succeeds (view bundles) |

The wire protocol on `/ws/audio`:

- **Browser → server:** binary frames = raw int16 LE PCM at the AudioContext rate
  (server resamples to 16 kHz). JSON control: `audio_start` (`sample_rate`,
  `language`), `barge_in`, `audio_stop`.
- **Server → browser:** binary frames = 24 kHz int16 PCM (TTS). JSON control:
  `audio_ready`, `transcript` (`text`, `is_final`), `tts_start` (`sample_rate`),
  `tts_end`, `tts_cancel` (flush on barge-in), `vad_silence`.

## Status of the original open items

### Sidebar/section wiring — DONE (`b1d67b36`)
The view is reachable now. `store/events.ts` (SectionId), `MainView.tsx` (the view
switch — the app uses MainView, not a switch in App.tsx), and `Sidebar.tsx` (the
nav entry) were clean by the time the wiring landed and are committed. The sidebar
shows a **Browser Voice** entry (Headphones icon); its English label renders via a
new optional `fallbackLabel` on `NavItem` + `resolveNavLabel()`, which auto-localizes
the moment the locale key below is added. Whole-project `tsc` is clean and
`npm run build` succeeds with the view bundled.

### Localized labels — DONE (`e90102f2`)
The de/es labels are committed: `nav.browser_voice` + the `browser_voice.{title,
subtitle,start,stop,listening,speaking}` block landed in en/de/es. The sidebar +
view now render localized (de "Browser-Sprache", es "Voz del navegador"); the
`fallbackLabel` is now a no-op. Done with the maintainer's explicit authorization —
because hunk-isolation (`git add -p`) is unavailable in this runtime, the commit
also carried the parallel sessions' valid, build-clean in-flight i18n keys
(agent_instructions / apikeys_model / onboarding); no foreign work was lost.

### Real-browser smoke — needs a human at the mic (parallel to the HW sign-off)
The frontend is **build-verified** (the view bundles; `tsc` clean), but AudioWorklet
+ getUserMedia + Web Audio playback only run in a real browser on a secure context
(`localhost`/`https`). A full voice round-trip (speak → STT → TTS playback) needs a
person at the microphone — not autonomously reachable. A live *visual* smoke was
attempted via claude-in-chrome but the browser extension was offline at the time;
re-attempt by opening the running app (`:47821`), hard-reloading, and clicking the
Browser Voice sidebar entry.

The `browser_voice` view-string block to paste into each locale
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
