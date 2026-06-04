# Phase C — Status Report

> Commits: `ac02749f` (skeleton), `b2c5b976` (routes+auth),
> `b3b02661` (docker), `b4abe131` (sync-client), `750821fc` (readme).
> Branch: `router-permanent-vision`.

---

## 1. `docker compose up -d --build` — first 30 lines

```
#21 exporting to image
#21 exporting layers 3.5s done
#21 exporting manifest sha256:aa54b0090c25...e81d 0.0s done
#21 exporting config sha256:ec34472bea20...edc29 0.0s done
#21 exporting attestation manifest sha256:59fde18c26b6...c5cb4 0.0s done
#21 exporting manifest list sha256:cbbe008f61d2...37294 0.0s done
#21 naming to docker.io/library/jarvis-board-backend:0.1.0 done
#21 unpacking to docker.io/library/jarvis-board-backend:0.1.0 1.5s done
#21 DONE 5.3s

 Image jarvis-board-backend:0.1.0 Built
 Network board-backend_default Creating
 Network board-backend_default Created
 Volume board-backend_db_data Creating
 Volume board-backend_db_data Created
 Container jarvis-board-backend Creating
 Container jarvis-board-backend Created
 Container jarvis-board-backend Starting
 Container jarvis-board-backend Started

# danach:
$ docker compose ps
NAME                   STATUS                    PORTS
jarvis-board-backend   Up 51 seconds (healthy)   0.0.0.0:8765->8765/tcp

$ docker compose logs --tail 5 backend
INFO:     Started server process [1]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8765 (Press CTRL+C to quit)
INFO:     127.0.0.1:56838 - "GET /healthz HTTP/1.1" 200 OK
```

`(healthy)` status after the 10 s start_period — the healthcheck polls
`/healthz` and accepts only 200s. The DB schema is built at app start
by `init_schema(engine)`.

---

## 2. Real signed-sync roundtrip against the live container

Script:

```python
priv, pub = generate_keypair()
print(f"pubkey={pub}")

httpx.post("http://localhost:8765/api/v1/identity/register",
           json={"pubkey": pub, "display_name": "PhaseC-Smoke"},
           headers={"X-Admin-Token": "test-token-32chars-aaaabbbb1111ccc"})

payload = {"ts_ms": int(time.time()*1000), "display_name": "PhaseC-Smoke",
           "daily_stats": [{...}], "achievements": [{...}]}
sig = sign(payload, privkey_hex=priv)
httpx.post("http://localhost:8765/api/v1/sync",
           content=canonical_json(payload),
           headers={"X-Pubkey": pub, "X-Jarvis-Sig": sig,
                    "Content-Type": "application/json"})

httpx.request("GET", "http://localhost:8765/api/v1/me",
              content=canonical_json({"ts_ms": int(time.time()*1000)}),
              headers={"X-Pubkey": pub, "X-Jarvis-Sig": me_sig,
                       "Content-Type": "application/json"})
```

Output:

```
pubkey=a050f7fdf922fc5f00d643f547bc02bcc216abf5c8b26341697459f64041989d

REGISTER -> 200 :: {
  "pubkey": "a050f7fdf922fc5f00d643f547bc02bcc216abf5c8b26341697459f64041989d",
  "display_name": "PhaseC-Smoke",
  "created_at": "2026-04-25T09:05:14.584974"
}

SYNC -> 200 :: {
  "accepted": true,
  "daily_stats_count": 1,
  "achievements_count": 1,
  "received_at": "2026-04-25T09:05:15.040344Z"
}

ME -> 200 :: {
  "pubkey": "a050f7fdf922fc5f00d643f547bc02bcc216abf5c8b26341697459f64041989d",
  "display_name": "PhaseC-Smoke",
  "created_at": "2026-04-25T09:05:14.584974",
  "last_sync_at": "2026-04-25T09:05:15.040344",
  "push_count": 1
}
```

Container log of the roundtrip:

```
INFO: 172.21.0.1:53972 - "POST /api/v1/identity/register HTTP/1.1" 200 OK
INFO: 172.21.0.1:56842 - "POST /api/v1/identity/register HTTP/1.1" 200 OK
INFO: 172.21.0.1:56854 - "POST /api/v1/sync             HTTP/1.1" 200 OK
INFO: 172.21.0.1:56858 - "GET  /api/v1/me               HTTP/1.1" 200 OK
```

---

## 3. File list `board-backend/`

```
board-backend/
├── pyproject.toml             # standalone deps, no jarvis coupling
├── Dockerfile                 # multi-stage, multi-arch (amd64+arm64)
├── docker-compose.yml         # single-service, named volume db_data, healthcheck
├── .dockerignore              # tests/, .git/, *.db excluded
├── .env.example               # ADMIN_TOKEN template
├── README.md                  # 3 deploy scenarios (localhost, raspi, hetzner+caddy)
│
├── board_backend/
│   ├── __init__.py            # __version__
│   ├── main.py                # create_app() factory + lazy module-level app
│   ├── config.py              # pydantic-settings Settings, ADMIN_TOKEN required
│   ├── db.py                  # SQLAlchemy engine + WAL pragmas + sessionmaker
│   ├── models.py              # Identity (PK=pubkey) + PushLog (audit-only)
│   ├── schemas.py             # Pydantic with extra='forbid' as PII wall
│   ├── crypto.py              # Ed25519 sign/verify + canonical_json
│   ├── auth.py                # admin-token-dep + signed-request-dep
│   ├── rate_limit.py          # in-memory sliding-window limiter
│   └── routes/
│       ├── __init__.py
│       ├── health.py          # GET /healthz (public)
│       ├── identity.py        # POST /api/v1/identity/register (admin)
│       ├── sync.py            # POST /api/v1/sync (signed)
│       └── me.py              # GET  /api/v1/me   (signed)
│
└── tests/
    ├── __init__.py
    ├── conftest.py            # Settings + TestClient fixture (tmp_path DB)
    ├── test_crypto.py         # 10 tests: canonical, sign/verify, tampering
    ├── test_skeleton.py       # 2 tests: healthz, ADMIN_TOKEN required
    └── test_routes.py         # 16 tests: register/sync/me + all plan sec gates
```

Plus on the local Jarvis side (commit `b4abe131`):

```
jarvis/
├── board/
│   └── sync.py                # SyncClient with Ed25519 push, keyring priv storage
├── core/
│   └── config.py              # +BoardFederationConfig + BoardConfig
└── ...

jarvis.toml                    # +[board.federation] with enabled=false default
tests/board/test_sync.py       # 5 tests against ASGI in-memory backend
```

---

## 4. Test status — 74 green, no skips

```
$ pytest tests/board/                            # local-side
46 passed in 2.24s

$ cd board-backend && pytest tests/              # backend-side
28 passed in 0.59s
```

Key sec tests from PLAN §C:

- `test_signed_sync_accepts_valid_signature` ✓
- `test_signed_sync_rejects_tampered_payload` ✓
- `test_signed_sync_rejects_old_timestamp` ✓
- `test_signed_sync_rejects_future_timestamp` ✓
- `test_signed_sync_rejects_unregistered_pubkey` ✓
- `test_register_rate_limit_enforced` (11th request → 429) ✓
- `test_no_pii_in_sync_payload` (top-level extra-field rejection) ✓
- `test_no_pii_in_daily_stats_extra_field` (nested extra-field rejection) ✓
- `test_pushlog_contains_no_payload_inhalte` (audit-only persistence) ✓
- `test_signature_replay_with_changed_body_rejected` ✓

---

## 5. Most fragile spots — what will cause problems in Phase D?

Ordered by likelihood of breaking:

### 5.1 Canonical-JSON drift between Python client and JS frontend

Phase D needs the **frontend** as a sig generator (e.g. reactions from
the browser). The Python server side canonicalizes with
`json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=False)` —
JS has no built-in canonical JSON encoder. If the frontend uses
naive `JSON.stringify`, it emits whitespace and unsorted
keys, and the sig won't match.

**Fix proposal for Phase D:** Write a `canonicalJson(obj)` function in
TypeScript that recursively produces sorted keys. A
cross-stack smoke test (Python signs, JS verifies and vice versa)
is mandatory — otherwise you debug sig mismatches for days.

### 5.2 Replay window vs. clock drift on friend backends

A 5-minute window sounds generous, but is tight if a VPS Pi
has 3 minutes of drift (NTP not active) and the local Jarvis is another 2
minutes off. Federation pushes between two Pi backends
could then fail **unpredictably**.

**Fix proposal:** Phase D should exchange the server time once during
pairing and persist the clock offset. Plus
extend `/healthz` with `server_time_ms`.

### 5.3 Pubkey swap on device change

Currently pubkey == identity. If a user loses their privkey
(new laptop, no backup), they can only re-register with a **new**
pubkey — and all existing friends see this as a
foreign identity. Phase D needs either:
- a recovery token (signed during pairing), or
- an "identity rotate" procedure in which the old identity signs the
  new one.

### 5.4 Rate limiter is in-memory per worker

Currently `uvicorn` runs with one worker. As soon as someone scales to
`uvicorn --workers 4` (not documented anywhere in the README, but Hetzner
users could do it), the rate limit breaks apart per worker — 4×10/min.
Real multi-worker deployments need Redis or similar.

**Mitigation for Phase D:** A README section "Scaling > 1 worker" with
an explicit warning. Medium-term: `slowapi` with a Redis backend.

### 5.5 SQLite + many friends

Phase D will add `friends_activity_items` and `reactions` tables.
With 100 friends polling per minute, that's ~6000 reads/h
against SQLite — still absolutely fine, but the WAL file grows wild if no
periodic checkpoints are made. A `PRAGMA wal_checkpoint` cron
should be planned for Phase D.

### 5.6 Privkey storage on Linux/macOS

The `_KeyringBackend` in `jarvis/board/sync.py` falls back to the
`keyring` package. On Windows with Credential Manager: stable.
On Linux without `secret-service` (headless server): keyring fails,
the privkey ends up **in-memory only** — on restart a
new keypair is generated, and the identity is "gone".

**Fix proposal for Phase D:** An optional file backend (encrypted
with the `JARVIS_BOARD_KEY_PASSPHRASE` env var) as a fallback.

### 5.7 GET /api/v1/me with body

A GET request with a JSON body is RFC-allowed but unconventional — some
reverse proxies (old nginx versions) strip the body. Caddy does
it right, **but** if Phase D needs further signed GETs (e.g.
`GET /federation/feed`), the pattern should either:
- consistently switch to POST, or
- move the sig into a header (e.g. a separate signed `X-Timestamp`
  header).

Currently it is an awkward special case in exactly one route.

---

## 6. Decisions I adopted from the user

All three plan decisions from Phase C confirmed and implemented:

| Decision | Where? |
|---|---|
| **Push** instead of pull | `jarvis/board/sync.py::SyncClient._push` — the local client goes to the backend, not the other way around. |
| **Pubkey-only identity, display_name per push** | `models.Identity.pubkey` is PK; `routes/sync.py` updates `ident.display_name = body.display_name` on every sync. |
| **Admin token only for initial pairing** | `routes/identity.py` is the only route with `Depends(require_admin_token)`. All other routes use `require_signed_request`. |

No deviations → no ADR needed.

---

## 7. Open items for Phase D

1. JS canonicalJson + sig generation in the frontend.
2. Friend pairing token + URL/QR UI.
3. `friends` table + federation pull loop in the backend.
4. `/api/v1/federation/feed` + reactions + stories.
5. Right-to-be-forgotten via signed DELETE.
6. UI routes `/board/friends` + `/board/friends/manage`.

Phase C is the foundation — pubkey identity, signed auth, and the
PII-safe sync pipeline are running. Phase D can build on top of it without
changing the auth model.

---

_Phase C: delivered._
