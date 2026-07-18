# Changelog

All notable changes in Personal Jarvis.

Format based on [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
versioning per [SemVer](https://semver.org/lang/de/).

---

## [Unreleased]

### Fixed

- **Realtime voice no longer pauses or stutters mid-reply.** When the live
  provider's output transcription lagged its audio (Gemini Live: routinely
  3-22 s), the voice-scrub gate held the audio back — first as
  multi-second dead stops mid-word, then (after an interim 400 ms bounded
  hold) as rhythmic block-wise stutter. Mid-reply holds are now removed
  entirely: once the reply's opening transcript has been vetted clean,
  audio flows unconditionally and the scrubber acts as a trailing kill
  switch that still cancels the response on a detected leak. The strict
  fail-closed turn opening is unchanged (BUG-080).

- **macOS 15 no longer kills the app when hotkeys arm or Computer-Use types
  ("Personal Jarvis quit unexpectedly", SIGILL).** pynput resolved the
  keyboard layout through HIToolbox TSM calls on background threads, which
  modern macOS aborts with an uncatchable illegal-instruction trap. Two-layer
  fix: global hotkeys now use a TSM-free Quartz event-tap backend, and a
  main-thread keyboard-layout snapshot is captured at boot so any remaining
  pynput keyboard path (e.g. `keyboard.Controller()` in Computer-Use
  actuation) reuses it instead of touching TSM off-main — degrading to the
  pyautogui fallback, never crashing (BUG-077).

- **macOS installer no longer dies with a bare exit code on uv-provisioned
  Pythons.** The app bundle's native launcher is now an in-repo compiled C
  stub linked against the exact Python runtime in use — replacing the py2app
  alias stub, which only worked on framework Pythons and left Intel Macs
  (uv standalone bootstrap) with an unlaunchable bundle. Desktop-integration
  failures now write `data/logs/install-desktop-integration.log` and print
  the actual error instead of discarding it (BUG-076).
- **Voice endpointing degrades honestly without onnxruntime.** The WebRTC VAD
  fallback tier is now actually wired (Silero ONNX → WebRTC VAD → RMS energy);
  previously the middle tier was documented but never imported, so
  onnxruntime-less systems (e.g. Intel Macs) silently fell back to bare
  energy endpointing (BUG-061 follow-up).
- **The installer's speech-model report is honest** — it reflects which
  models/runtimes are actually usable on the machine instead of assuming the
  full stack, and can always be produced without raising.
- **Windows-only dev scripts refuse to run on other platforms** with a
  one-line message instead of an ImportError traceback.

### Added

- **Real macOS menu-bar icon.** The tray icon is hosted on the AppKit main
  thread (pystray `darwin_nsapplication` + `run_detached`), completing the
  BUG-056 follow-up — macOS gets the same tray surface as Windows/Linux.
- **Mascot/orb on macOS.** The mascot/orb overlay now renders in its own
  subprocess host (BUG-057 follow-up), with Aqua-Tk alpha transparency.
- **macOS audio ducking.** Music and Spotify are ducked via AppleScript for
  the duration of a voice session and restored afterwards, with an opt-in
  master-volume fallback.

### Removed

- The stale root-level `install.sh` (legacy clone-then-run script). The one
  advertised one-line installer remains `install/install.sh`.

## [1.0.10] — 2026-07-16

### Fixed

- **The one-line installer works in non-interactive environments again.**
  The welcome gate's terminal probe relied on `test -r/-w`, which passes on
  CI runners and headless automation where `/dev/tty` exists but cannot be
  opened (no controlling terminal) — the piped install aborted before doing
  anything. The gate now probes with a real open and quietly skips the
  question when no terminal is available.

## [1.0.9] — 2026-07-16

### Fixed

- **Headless first boot: onboarding answers again.** The full server's
  security boundary now serves `/api/onboarding/*` without a credential,
  matching the serve-first bootstrap. A fresh install on a headless box
  could otherwise never complete onboarding — the first-boot contract broke
  the moment the full app took over from the bootstrap (caught by the
  fresh-install smoke workflow on the v1.0.8 release commit).

## [1.0.8] — 2026-07-15

### Added

- **macOS permissions surface.** Runtime TCC permission probes (microphone,
  accessibility, screen recording) with a settings panel, an onboarding step,
  REST routes, and a `jarvis` CLI command — degrading to a quiet no-op on
  other platforms. A dedicated macOS desktop CI workflow guards the path.
- **In-app docs system.** Authoring pipeline, docs overview, sidebar and
  full-text search UI, plus a public-docs CI check.
- **Wiki grounding.** Extraction now records bounded, secret-redacted evidence
  excerpts (migrations 0006–0008) with an audit trail and a backfill for
  existing entries.
- **Computer-Use foreground target guard** — actuation tools verify the
  intended window is actually in the foreground before clicking or typing.
- **Brain tool-call recovery** — malformed provider tool calls are repaired
  instead of failing the turn.
- **Persistent realtime voice sessions** stay open between turns, with a
  voice-mode badge in the session UI.

### Changed

- **Unified web-surface security**: one cookie/bearer policy as route-level
  defense in depth, an auth gate in the frontend, and authenticated mission
  WebSockets and terminals.
- **Transactional self-update**: the relauncher applies updates as a guarded
  transaction with rollback on failure.
- Scoped provider credentials, realtime scrub-gate refinements, onboarding /
  socials / settings polish, and WS schema updates.

### Fixed

- **Desktop installs remain discoverable after an in-app update.** Managed
  installs now register and repair the Windows Start-menu and Installed Apps
  entries, the macOS per-user app bundle, or the Linux application-menu entry.
  Installer, updater, first desktop paint, and uninstaller share one guarded
  lifecycle, while headless and developer checkouts remain untouched.

## [1.0.7] — 2026-07-14

### Fixed

- **macOS first boot works.** Three native first-launch aborts ("Python quit
  unexpectedly") fixed in one forensic series: the tray status item, the
  Jarvis bar/orb Tk windows, and the virtual-cursor overlay were created off
  the main thread (AppKit/Aqua-Tk abort natively, BUG-056/057); PortAudio
  re-initialization is now serialized single-flight and the global-hotkey
  event tap preflights the Accessibility grant instead of letting macOS kill
  the process (BUG-058). macOS runs with the desktop window + Dock icon; the
  menu-bar icon and on-screen bar return once main-thread hosting lands.
- **Local speech pack install no longer blames your internet.** A missing
  prebuilt wheel (e.g. Python 3.14 + av) is now diagnosed honestly, pip runs
  wheel-only on end-user machines (never a source build), and the installer
  prefers Python 3.13/3.12 until the native stack ships 3.14 wheels (BUG-059).
- **Grounded wiki answers.** "What is in my wiki" is answered by a new
  deterministic listing tool in one round instead of blind probing; contract
  pages carry a provenance warning; delegated voice turns get a hard
  wall-clock deadline with a forced final answer (BUG-055).
- **Realtime stability.** A benign cancel race no longer ends the call, and
  German (any-language) capability verbs reach connected tools directly
  instead of always spawning an agent.

### Added

- **OAuth token refresh lifecycle** for marketplace plugins (scheduler,
  PKCE/token-store hardening, Gmail/Calendar REST updates).
- **Sessions view rework**: richer session detail and turn cards.

## [1.0.6] — 2026-07-13

### Added

- **Realtime voice engine — first public release** (previously withheld):
  low-latency speech-to-speech conversations with tool delegation, plus the
  tool-model pick and a broad reliability wave.

### Changed

- **A missing Node.js no longer blocks the one-line installer.** Node only
  powers the optional coding-agent worker (Claude Code / Codex) and a few
  Node-based integrations; the installer now notes its absence and continues,
  pointing to the in-app path for adding the worker later — instead of turning
  new users away at the door.

## [1.0.5] — 2026-07-09

### Fixed

- **The wake word now works for non-English speakers.** Wake detection routes to
  a model that matches the language you actually speak — the right-language local
  keyword model, or multilingual Whisper as a fallback — instead of silently
  going deaf on an English-only model. The missing language model is fetched
  automatically on a language switch or boot, wake stays pinned to the CPU, and a
  new **language selector** (with a "Test wake word" readiness check) lets you set
  the spoken language directly in Settings.
- **The Windows taskbar button shows the Jarvis mascot** instead of the generic
  Python logo. The app re-launches through a mascot-branded executable that owns
  its window, and it self-heals a stale Start-Menu shortcut. Best-effort and
  fully guarded: on a read-only or Store-Python install it degrades cleanly with
  no change in behavior.

### Changed

- **Clearer API-keys screen.** NVIDIA NIM is now flagged with a "not recommended"
  caution badge (its free tier is slow), Inworld TTS is no longer mislabeled as a
  realtime provider, and the Pipeline / Realtime voice-engine switch was
  redesigned for clarity.

## [1.0.4] — 2026-07-08

### Fixed

- **Custom wake words work out of the box on a fresh install.** The per-language
  Vosk keyword-spotting model is now provisioned automatically — installer
  prefetch, an off-boot self-heal on first run, and an in-app "Download wake
  model" button — so a freely chosen wake phrase resolves to the reliable
  any-word engine instead of silently degrading to the transcribe-and-match
  path that cannot recognize a hard proper noun. Works for every supported
  language (`en` / `de` / `es`), with no training and no GPU, on any OS
  including Apple Silicon. The word-agnostic openWakeWord backbones now ship in
  the package, an unservable custom phrase degrades **loudly** (with a one-click
  fix) instead of failing silently, and onboarding verifies the microphone
  level and the spoken wake word before marking setup complete.

## [1.0.0] — 2026-07-03

First **public** release of Personal Jarvis — a voice-driven meta-orchestrator
that turns one spoken request into a fleet of self-checking AI agents.

### Highlights

- **Voice-first pipeline** — wake word → speech-to-text → multi-provider Brain →
  text-to-speech, fully streaming, with honest, language-aware readbacks
  (`de` / `en` / `es`).
- **Provider-agnostic by design** — every tier (router, ack, STT, TTS, worker,
  critic) degrades or crosses provider families on a missing or dead key. No
  single provider is load-bearing, and credentials are managed entirely in-app.
- **Cross-platform core** — the base install boots on a headless
  `python:3.11-slim` VPS; Windows-desktop and local-voice features live behind
  optional extras.
- **Jarvis-Agents mission system** — isolated `git worktree` workers with a
  self-healing critic loop.
- **Plugin marketplace** (OAuth + MCP) and a cross-platform control CLI
  (`jarvisctl`).
- **In-app "Update available" button** — managed desktop installs get a one-click
  "Update Now" control in the top bar when a newer version ships.

### Fixed

- **Desktop app could hang forever on "Getting ready to listen".** The startup
  banner and the top-left voice status cleared only when the speech pipeline
  published its one-shot ready signal. If pipeline construction crashed or an
  un-timed model load wedged warm-up, that signal never fired and the UI stayed
  in "starting up" indefinitely — even though typing already worked. Added two
  fail-safes: the pipeline-construction crash handler now publishes an honest
  degraded-ready signal so the UI is released immediately, and a
  pipeline-independent watchdog in the web server force-releases the UI after a
  generous deadline. The banner can no longer stick forever.
- **Local "Faster-Whisper" appeared as a ready STT provider even when not
  installed.** The provider list never checked whether the local-voice extra was
  present, so the card always showed as configured on a base install, and its
  model dropdown listed all Whisper checkpoints regardless of what was
  downloaded. Local Faster-Whisper has been removed as a user-selectable
  speech-to-text provider; cloud STT (Groq / OpenAI / OpenRouter) is the
  supported dictation path. The wake word (which uses its own local Whisper) and
  the key-free STT resilience fallback are unaffected.
- Declared `click` as an explicit dependency so the `jarvisctl` CLI imports on a
  clean install (it no longer arrives transitively via `typer`), restoring a
  green CI.
- Restored `TRADEMARK.md` to the published tree and removed a dead documentation
  link from the README.
- Stopped defaulting the archival store to the removed `chroma` backend.

### Changed

- `pyproject` metadata for the public release: English, cross-platform
  description and a `[project.urls]` block.

---

## [v1.0.0-board] — 2026-04-25

First consolidated release of the **Jarvis Board**: Phase A through D.

### Added — Phase A (Personal Dashboard)

- `jarvis/board/aggregator.py`: `BoardAggregator` parses FlightRecorder
  JSONL from `data/flight_recorder/`, groups it per day, writes
  `daily_stats` + `personal_records` into `data/board/personal.db`.
- `jarvis/board/store.py`: `BoardStore` as a read-only query facade for the API.
- `jarvis/ui/web/board_routes.py`: GET `/api/board/personal/{summary,
  heatmap,tools,records}` + POST `/refresh`.
- Frontend: `BoardView` with `<HeatmapGrid>`, `<ToolBarChart>`,
  `<StatsCard>`, `<PersonalRecordsList>`. React-Query polling 30 s.
- `recharts` as a new frontend dependency.

### Added — Phase B (Achievements + AI-Bio)

- 10 `AchievementSpec`s in `jarvis/board/achievements.py`: 7 Mastery
  (`first_mcp`, `tool_dabbler/journeyman/master`, `triple_combo`,
  `sub_jarvis_summoner`, `ten_x_engineer`) + 3 Reflection (`centennial`,
  `kilo_club`, `one_year_with_jarvis`).
- `AchievementEvaluator` as an `EventBus` subscriber. Idempotent via
  `INSERT OR IGNORE` on `achievements.id`.
- `AchievementUnlocked` event in `jarvis/core/events.py`.
- `BioGenerator` with `BrainLike` protocol. Anti-cliché test gate against
  12 forbidden words. Brain outage → the old bio is kept.
- `BioScheduler`: asyncio tick 60 s, Sunday 18:00 + master-achievement
  trigger. Date guard via `aggregator_meta`.
- API: GET `/api/board/achievements`, GET `/api/board/bio`, POST
  `/api/board/bio/regenerate`.
- Frontend: `<AIProfileCard>`, `<AchievementGrid>` with a live unlock toast
  via `pushToast` in the WebSocket handler.

### Added — Phase C (Federation Backend)

- New subproject `board-backend/` (FastAPI + SQLAlchemy + SQLite +
  PyNaCl).
- Routes: POST `/api/v1/identity/register` (admin-token), POST
  `/api/v1/sync` (signed), GET `/api/v1/me`, GET `/healthz`.
- Ed25519 crypto + canonical JSON in `crypto.py`.
- Replay protection: `ts_ms` ± 5 min. Constant-time token comparison.
- In-memory rate limiter: 10/min/IP on `/identity/register`.
- Pydantic `extra='forbid'` as the central PII wall.
- Multi-arch Dockerfile (amd64 + arm64) + docker-compose + healthcheck.
- README with three deploy scenarios (Localhost, Raspi, Hetzner+Caddy).
- Local: `jarvis/board/sync.py` as a background push client (60 s).
- New jarvis.toml section `[board.federation]` (default `enabled = false`).

### Added — Phase D (Friends + Federation)

- Backend routes: POST `/pair/{initiate,accept}`, GET `/friends`, PATCH
  `/friends/{pubkey}`, POST `/activities`, POST `/stories`, POST
  `/reactions`, GET `/federation/feed?since=...`, POST
  `/federation/reactions/inbound`, DELETE `/federation/identity/{pubkey}`.
- Tables: `friends`, `pair_tokens` (10-min TTL, single-use),
  `activity_items` (visibility: private/friends/public, optional
  `expires_at` for stories), `reactions` (UNIQUE constraint).
- `interesting_score = reactions × exp(-age_h / 24)` — deterministic,
  hardcoded halflife.
- `FederationPuller` as an asyncio task per friend (offline ≠ blocking).
- `StoriesCleanup` 1 h tick, deletes `expires_at < NOW`.
- Frontend: `<FriendsView>` with tabs "Feed" + "Manage", `<PairDialog>`
  with QR code (`qrcode.react`), `<StoryComposer>` (max 280 chars),
  `<ReactionBar>` (🚀 🧠 🔥, owner-only counts), `<FriendsList>` with
  a per-friend pull-interval stepper.
- Settings page: new section "Backend Connection" (Disconnect, URL,
  copy pubkey).
- Local federation proxy (`jarvis/ui/web/federation_proxy_routes.py`)
  with whitelist paths — the browser frontend does not sign itself, the privkey
  stays in the local backend.

### Added — v1.0 Release Prep

- `tools/board_demo.py` — bootstrap 2 backends + 5 identities + 30 days of activity.
- `tools/board_perf.py` — aggregator + federation-pull benchmark.
- `tools/board_pentest.py` — 19-vector pen test against a live container.
- `docs/jarvis-board/ARCHITECTURE.md` — for backend forkers.
- `docs/jarvis-board/FEDERATION_PROTOCOL.md` — wire-format spec v1.
- `docs/jarvis-board/PERFORMANCE_AUDIT.md` — aggregator 2.94 s / 365d,
  federation pull 17 KB / 10 friends.
- `docs/jarvis-board/SECURITY_AUDIT.md` — 19/19 pen test PASS.
- `docs/jarvis-board/MIGRATION_v1.md` — 4-stage migration for existing users.
- README.md extended with a "Jarvis Board" section.

### Fixed

- `httpx` moved from dev-only to a production dependency in `board-backend/
  pyproject.toml` — `routes/pair.py`, `reactions.py`, `background.py`
  import it on every container start. Bug uncovered during the first
  Phase-D container rebuild for the pen test (Phase D had until then run with
  the Phase-C image).

### Security

- Three independent PII filter layers: aggregator whitelist
  (`export_all_for_federation()`), sync-client whitelist
  (`_build_payload`), server `extra='forbid'` Pydantic wall.
- Ed25519 sigs on all federated routes with re-canonicalize
  (reverse-proxy-resilient).
- Constant-time admin-token comparison + per-IP rate limit.
- 19-vector pen test: auth bypass, replay (past + future),
  tampering, PII leak, malformed body, SQL-injection regression,
  forget-me path mismatch — all PASS.

### Not in Release

- **Phase E (Public Aggregator / Strava-style segments)**: deliberately
  not built. Plan §0 requires ≥ 2 months of Phase-D burn-in first,
  so that anti-cheat mechanisms can be designed evidence-based.
  First Phase-E decision: ~ 2026-06-25.
- **Bundle splitting**: frontend JS is 444 KB gzip (Vite warning).
  Functionally uncritical (<500 ms initial load on modern devices),
  but code splitting for `recharts` + `@tanstack/react-query` is
  a follow-up PR.

---

## [Pre-board] — before 2026-04-24

Phases 0–5 (skeleton, speech, plugin system, risk tier, memory,
harness dispatch, vision/computer-use/admin/async/control/telemetry)
are in the repo, documented in `docs/phase{1a,1c,2,4,5}-*.md` and
ADRs `docs/adr/0001-0008`. This CHANGELOG only starts with the v1.0
Board release — pre-board history is reconstructable via `git log`.
