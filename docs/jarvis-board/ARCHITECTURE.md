# Jarvis Board — Architecture

> For contributors who fork the backend, build alternative frontends,
> or want to understand how the four phases interlock.

---

## TL;DR

```
┌───────────────────────────────────────────────────────────────────┐
│  Local Jarvis (Desktop, personal machine)                         │
│                                                                   │
│   FlightRecorder ──► JSONL ──► BoardAggregator ──► personal.db    │
│                                       │                  │        │
│   Live EventBus ──► AchievementEvaluator (subscriber) ───┘        │
│                                       │                           │
│   BrainManager ──► BioGenerator ──── achievements / bio tables    │
│                                                                   │
│   FastAPI (port 47821)                                            │
│     ├─ /api/board/personal/*       (read-only)                    │
│     ├─ /api/board/achievements,bio (read + regen)                 │
│     └─ /api/board/federation/*     (proxy + sig)                  │
│                              │                                    │
└──────────────────────────────┼────────────────────────────────────┘
                               │ signed via Ed25519 + ts_ms
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Federation Backend (Container, Self-Hosted)                     │
│                                                                  │
│   FastAPI + SQLAlchemy + SQLite (board.db)                       │
│   Tables: identity, push_log, friends, pair_tokens,              │
│           activity_items, reactions                              │
│                                                                  │
│   Routes:                                                        │
│     ├─ POST /api/v1/identity/register          (admin-token)     │
│     ├─ POST /api/v1/sync                       (signed)          │
│     ├─ POST /api/v1/pair/{initiate,accept}                       │
│     ├─ GET  /api/v1/friends   PATCH /api/v1/friends/{pubkey}     │
│     ├─ POST /api/v1/{activities,stories,reactions}               │
│     └─ /api/v1/federation/{feed, reactions/inbound,              │
│                            identity/{pk} DELETE}                 │
│                                                                  │
│   Background:                                                    │
│     ├─ FederationPuller — polls each Friend-URL (2 min default)  │
│     └─ StoriesCleanup    — 1h tick, deletes expires_at < now     │
└──────────────────────────────────────────────────────────────────┘
                               ▲
                               │ POST /api/v1/federation/reactions/inbound
                               │ GET  /api/v1/federation/feed
                               │
┌──────────────────────────────┴───────────────────────────────────┐
│  Friend's Federation Backend (same software, different machine)  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Four phases, four layers

| Phase | Code | Persistence | Responsibility |
|---|---|---|---|
| **A — Personal Dashboard** | `jarvis/board/aggregator.py`, `store.py` | `data/board/personal.db` (SQLite, sync) | FlightRecorder-JSONL → daily_stats / personal_records. Read-only API. |
| **B — Achievements + AI-Bio** | `jarvis/board/achievements.py`, `evaluator.py`, `profile.py`, `scheduler.py` | same `personal.db` (additive) | Live-EventBus subscriber unlocks achievements. Weekly bio via BrainManager. |
| **C — Federation Backend** | `board-backend/` (separate subproject) | `/data/board.db` (in the container) | Standalone service. Receives signed pushes from the local Jarvis. |
| **D — Friends** | extends `board-backend/` + `jarvis/ui/web/federation_proxy_routes.py` | same `board.db` (additive) | Pair, Activity, Reactions, Stories, Federation-Pull. |

Each phase is separately deployable — if you only want Phase A, leave
out `board-backend/`. If you only want Phase C+D, there is no
Local-Layer lock-in.

---

## Component model per phase

### Phase A — `jarvis/board/`

```
aggregator.py
  ├── BoardAggregator              # JSONL parsing, day grouping, upsert
  ├── DailyStats                   # dataclass mirror of daily_stats row
  └── PersonalRecord               # dataclass for personal_records row

store.py
  └── BoardStore                   # read-only query facade for FastAPI routes

schema.sql                         # additive CREATE TABLE IF NOT EXISTS
```

### Phase B — `jarvis/board/`

```
achievements.py
  ├── AchievementSpec(id, title, ..., evaluator: callable)
  └── ACHIEVEMENTS = [10 specs]    # first_mcp .. one_year_with_jarvis

evaluator.py
  ├── AchievementEvaluator         # subscribed_all on EventBus
  └── _LiveContext                 # in-memory LRU + persisted counters

profile.py
  ├── BrainLike (Protocol)         # minimal { async generate(text) -> str }
  ├── BioGenerator
  └── BioStore                     # append-only bio table

scheduler.py
  └── BioScheduler                 # asyncio tick loop, cron-like
                                   # weekly + post-master-achievement

prompts.py                          # bio prompt template (Plan §B)
```

### Phase C+D — `board-backend/board_backend/`

```
main.py                             # create_app() + lifespan hooks
config.py                           # pydantic-settings, ADMIN_TOKEN required
db.py                               # SQLAlchemy engine + sessionmaker (sync)
models.py                           # Identity, PushLog (C) + Friend, PairToken,
                                    #   ActivityItem, Reaction (D)
schemas.py                          # Pydantic with extra='forbid'
crypto.py                           # Ed25519 sign / verify + canonical_json
auth.py                             # admin_token-dep, signed_request-dep,
                                    #   federation_signed-dep
rate_limit.py                       # in-memory sliding window per IP
background.py                       # StoriesCleanup + FederationPuller

routes/
  ├── health.py                    # GET /healthz
  ├── identity.py                  # POST /api/v1/identity/register
  ├── sync.py                      # POST /api/v1/sync
  ├── me.py                        # GET  /api/v1/me
  ├── pair.py                      # POST /pair/{initiate,accept},
                                   #   GET friends, PATCH friends/{pk}
  ├── activity.py                  # /activities, /stories, federation/feed
  ├── reactions.py                 # /reactions, federation/reactions/inbound
  └── forget_me.py                 # DELETE /federation/identity/{pk}
```

---

## Key data flows

### A.1 — Aggregator run (Phase A)

```
FlightRecorder.attach(bus)
  → bus.subscribe_all(_on_event)
  → JSONL-Append in data/flight_recorder/YYYY-MM-DD.jsonl

BoardAggregator.run_forever(interval_s=21600)   # 6h ticks
  → asyncio.to_thread(self.run)                 # batch in background thread
  → _iter_event_records()                       # streams JSONL
  → _aggregate_events()                         # group by ISO date
  → _upsert_daily_stats(rows)                   # INSERT OR REPLACE
  → _refresh_personal_records()                 # MAX(...) per metric
```

### B.1 — Achievement unlock (Phase B)

```
EventBus.publish(ActionExecuted(success=True, tool_name="bash"))
  → AchievementEvaluator._on_event(event)
  → ctx.record_event(event)         # update LRU + counters in memory
  → for spec in ACHIEVEMENTS:
       if spec.evaluator(event, ctx) is not None:
         _write_unlock(spec, decision)         # INSERT OR IGNORE
         if rowcount > 0:
           bus.publish(AchievementUnlocked(...))
```

### C.1 — Sync push (Phase C)

```
Local Jarvis: SyncClient.tick()
  → BoardAggregator.export_all_for_federation()  # safe-fields whitelist
  → canonical_json(payload)
  → Ed25519.sign(privkey, body)
  → POST /api/v1/sync
       Headers: X-Pubkey, X-Jarvis-Sig
       Body:    {ts_ms, display_name, daily_stats, achievements}

Backend: routes/sync.py
  → require_signed_request           # pubkey lookup → sig verify → ts_ms check
  → SyncPayload.model_validate       # extra='forbid' guards PII
  → INSERT INTO push_log (audit-only, no payload-content)
  → UPDATE identity SET last_sync_at = NOW()
```

### D.1 — Federation feed pull (Phase D)

```
Friend's Jarvis Frontend
  → GET /api/board/federation/get?path=/api/v1/federation/feed&sort=interesting

Local Jarvis Backend (proxy)
  → load_privkey_from_keyring()
  → canonical_json + sign + forward to friend_url
  → response passed back 1:1

Friend's Federation Backend
  → require_federation_signed
  → SQL: WHERE author=owner AND (visibility=public OR
                                  (visibility=friends AND viewer in friends) OR
                                  (visibility=private AND viewer=owner))
  → filter out expired (expires_at < NOW)
  → _sort_items(sort)
  → JSON {items, sort, server_now}
```

### D.2 — Reaction forwarding (Phase D)

```
Owner UI clicks the Rocket button on the Friend item
  → POST /api/board/federation/post
       { path: "/api/v1/reactions",
         body: { item_id, reaction: "rocket", author_pubkey: friend_pk } }

Local Jarvis proxy
  → sign + POST /api/v1/reactions to OWN backend

Own backend (Owner)
  → require_signed_request (sig from Owner)
  → friend = friends.get((owner_pubkey, body.author_pubkey))
  → forward the raw body (with Owner-Sig header) to
       friend.friend_url + /api/v1/federation/reactions/inbound

Friend backend
  → require_federation_signed (sig from Owner)
  → friends.get((friend_owner, owner_pubkey)) — Reactor IS friend
  → INSERT INTO reactions (UNIQUE constraint = idempotent)
```

---

## DB schemas

### `personal.db` (Local, Phase A+B)

```sql
daily_stats(date PK, tasks_completed, tasks_failed, tools_used JSON,
            unique_tools_count, voice_commands_count,
            voice_first_try_rate, hours_saved_estimate, created_at)

personal_records(metric PK, value, achieved_on, context JSON)

achievements(id PK, unlocked_at, evidence JSON)

bio(generated_at PK, text, model_used, triggered_by)

aggregator_meta(key PK, value)        -- Counters + last-run-tracking
```

### `board.db` (Federation Backend, Phase C+D)

```sql
identity(pubkey PK, display_name, bio, created_at, last_sync_at)

push_log(id PK, pubkey, received_at,
         daily_stats_count, achievements_count, payload_ts_ms)
         -- Audit only. NO payload-content stored.

friends(owner_pubkey + friend_pubkey PK,
        friend_url, friend_display_name, paired_at,
        last_pull_at, pull_interval_s)

pair_tokens(token PK, owner_pubkey, created_at, expires_at, used_at)

activity_items(id PK, author_pubkey, kind, payload TEXT,
               created_at, visibility, expires_at)

reactions(id PK, item_id, reactor_pubkey, reaction
          UNIQUE(item_id, reactor_pubkey, reaction))
```

Both DBs use **additive migrations** (`CREATE TABLE IF NOT EXISTS`).
No Alembic, no version table. On startup the backend calls
`models.Base.metadata.create_all(engine)` — this adds missing tables
but does **not** alter existing ones.

---

## Async conventions

- **`jarvis/board/`**: Aggregator + BioGenerator + Scheduler run in the
  Jarvis web server's asyncio loop. The Aggregator uses
  `asyncio.to_thread` for the SQLite batch writes — the event loop
  stays free for UI requests.

- **`board-backend/`**: synchronous SQLAlchemy + threadpool (FastAPI default).
  No async DB layer. The background tasks (Cleanup, Puller) run as
  asyncio tasks and use `httpx.AsyncClient` for the federation calls.

- **Tests**: `pytest-asyncio` with `mode=auto`. Live backend via
  `httpx.ASGITransport` — no TCP roundtrip needed.

---

## Plug points for forkers

| What you want to swap | Where |
|---|---|
| A different DB engine in the backend (Postgres) | `db.py:make_engine` — URL string + connect_args |
| A different sig algorithm (RSA / ed448) | `crypto.py` is fully replaceable; adjust the `Pubkey` and `Signature` schema-constraint regexes |
| A different brain provider for the BioGenerator | `BioGenerator(brain=...)` argument; a `BrainLike` protocol stub is enough |
| Different achievements | `achievements.py:ACHIEVEMENTS` — your own specs with `evaluator(event, ctx) -> UnlockDecision \| None` |
| A different cleanup trigger | `background.py:StoriesCleanup` as an external cron instead of an asyncio tick |
| A different federation pull | replace `background.py:FederationPuller` with your own mechanism — the other routes stay |

---

## What deliberately does NOT exist

(Plan §0 — if you fork this, keep the avoidance list.)

- Public like-counts (the Owner sees counts, others get `null` + bool).
- An online indicator per Friend.
- Read receipts for Activity items or Reactions.
- A notification-permission prompt in the browser.
- A pull-to-refresh slot-machine animation.
- A lifetime score across all achievements.
- Auto-submission of achievements to public aggregators.
- Algorithmic friend suggestions.

---

## Cross-references

- Federation wire format: `FEDERATION_PROTOCOL.md`
- Performance targets + reproducer: `PERFORMANCE_AUDIT.md`
- Security pen-test + threat model: `SECURITY_AUDIT.md`
- Per-phase status reports: `PHASE_{A,B,C,D}_DONE.md`
- Plan-of-record: `Aufgaben/Sozalmidea/JARVIS_BOARD_PLAN.md`
