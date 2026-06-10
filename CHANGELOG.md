# Changelog

All notable changes in Personal Jarvis.

Format based on [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
versioning per [SemVer](https://semver.org/lang/de/).

---

## [0.4.0] - 2026-06-10

### Added
- Hold-to-abort for running sub-agent missions: RUNNING cards in the Outputs view carry a stop ring you hold for 1.2s (no confirm dialog, no accidental one-click kill). Cancelling kills the in-flight orchestrator run and its worker process tree, emits the canonical `MissionCancelled` event (spoken announcement + recovery reconciliation), and the card flips to a distinct amber "cancelled" badge instead of "error".
- Outputs API now exposes each card's full mission id; the cancel endpoint reports whether a live worker was actually killed (`worker_killed`).
- Live reasoning trace in chat: a thinking card with real step-by-step progress (tool calls, worker delegation, computer-use phases) plus a "Thought for Xs" disclosure on finished replies.
- Profile view reimagined as "The Ledger": a butler-style house book with a generative knowledge seal, acquaintance tiers, and "still unwritten" voice prompts.
- Dynamic context-aware spawn announcements: the voice ACK for background workers names the actual task instead of a canned phrase.
- Connected CLI tools (gcloud, gh, ...) are first-class router capabilities with object-required matching and an evidence gate against hallucinated results.

### Changed
- Sub-agent missions use a lean standalone workspace when the task does not need the repository, instead of cloning the full repo for every step.
- The injection scanner now scans only worker-authored output, so a mission is no longer killed after delivering clean work just because the worker read security documentation.

### Fixed
- "Listens forever" after an 8s forced utterance cut: the buffered sentence is now flushed at the next voice-activity endpoint.
- An explicit "spawn a sub-agent" command beats a coincidental skill match, and a muted beheaded turn now ends audibly.

## [0.3.0] - 2026-06-09

### Added
- Local Control API (`/api/control/*`): authenticated self-configuration for local agents (per-user key, fail-closed bind, hot-reload of `brain.reply_language`), plus the "Jarvis API" settings panel and a built-in `control-api` documentation skill.
- Cross-platform login autostart (Windows shortcut / macOS LaunchAgent / Linux desktop entry), enabled by default, with the new "App settings" panel.
- Configurable voice keybinds with two-key chord capture, an on-screen keyboard map, and live re-apply without restart.
- Discord and Telegram channel connectors with a shared channel runtime wired into the worker chat path.
- Shareable stats card on the Board (PNG export, copy to clipboard, share on X).
- Wiki conversation curator (wave 1): durable candidate journal, secret/PII write guard, vault cleanup, FTS5 boot indexing.
- Skill system rebuild: instruction-skill model with `when_to_use` metadata, bulk delete, drag reorder, and on/off toggles.

### Changed
- Voice fallback phrases now follow the speaker's language (full Whisper language names like "german" are recognized); the playback watchdog no longer aborts a desktop-automation turn that is still actively working.
- Desktop control utterances (open app / navigate / screenshot / window ops / drag) route deterministically to computer-use, independent of the active brain provider.
- Codex can back the conversational brain via an OpenAI API key or the ChatGPT-login CLI path; missions on non-Claude providers fall back legibly to Claude instead of failing.
- Mission deadline raised to 70 minutes and worker timeouts no longer discard delivered work (structured `timed_out` signal).
- Sidebar reorganized into grouped sections; Telephony folded into API Keys; Languages folded into Settings.

### Removed
- Standalone Telephony and Languages screens (their section ids remain valid and land on the merged views).

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
