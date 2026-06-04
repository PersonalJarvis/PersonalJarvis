# Jarvis Board — Security Audit (v1.0)

> Live pen-test against `board-backend:0.1.0` in a Docker container.
> Reproducible via `python tools/board_pentest.py`.
> Date: 2026-04-25.

**Result: 19 / 19 tests PASS.**

---

## Threat model

Phase D processes **incoming federated requests** from foreign
backends. This gives rise to four main attack classes:

1. **Auth bypass** — a request is accepted as legitimately authenticated even though
   the signature does not match or the public key is not registered.
2. **Replay** — old signed requests are re-submitted later
   (e.g. after a backend compromise).
3. **PII leak via push** — the backend accepts fields that contain voice texts
   or tool output.
4. **DoS** — oversized / malformed bodies, brute-force against auth routes.

Plus the structural DB vectors (SQL injection, path traversal).

---

## Test sections

### 1. Admin-token gate (`POST /api/v1/identity/register`)

| Vector | Expected | Result |
|---|---|---|
| `X-Admin-Token: wrong` | 401 | ✅ 401 |
| `X-Admin-Token` empty string | 401 / 422 | ✅ 401 |
| `X-Admin-Token` correct | 200 | ✅ 200 |

**Constant-time comparison** via `hmac.compare_digest` —
a side-channel timing leak is not possible.

### 2. Signed-route auth (`POST /api/v1/sync`)

| Vector | Expected | Result |
|---|---|---|
| Garbage signature (`00 * 64`) | 401 | ✅ 401 |
| Body tampered after signing | 401 | ✅ 401 |
| `ts_ms` 6 min in the past (replay) | 401 | ✅ 401 |
| `ts_ms` 6 min in the future (drift attack) | 401 | ✅ 401 |
| Public key not registered | 401 | ✅ 401 |
| Malformed public-key hex (`ZZ * 32`) | 401 / 422 | ✅ 401 |

The sig-verify path is `Pubkey-Lookup → Sig-Verify → ts_ms-Check` —
cheap checks first, crypto only once the caller is potentially legitimate.

### 3. PII filter (Pydantic `extra='forbid'`)

| Vector | Expected | Result |
|---|---|---|
| Top-level `voice_transcript` in addition to the schema | 422 | ✅ 422 |
| Nested `transcript_leak` in `daily_stats[0]` | 422 | ✅ 422 |

The schema wall applies both at the top level (`SyncPayload`) and on
sub-models (`DailyStatsItem`). A malicious or buggy client
cannot smuggle any voice text through.

### 4. Malformed payloads

| Vector | Expected | Result |
|---|---|---|
| Body is not JSON | 400 / 401 | ✅ 400 |
| Body is a JSON array instead of an object | 400 / 401 / 422 | ✅ 400 |
| Body 1 MB junk | 422 (PII filter) | ✅ 422 |

The 1 MB junk test is relevant for DoS: the backend has to parse the body
before discarding it. With the current implementation the
body is read in full — at extremely high volume a reverse proxy (Caddy) with a
`request_body` size limit should sit in front of uvicorn. The README
recommends this.

### 5. SQL injection / path-traversal regression

| Vector | Expected | Result |
|---|---|---|
| `display_name = "Bobby'); DROP TABLE identity; --"` | 200 (stored parameterized) | ✅ 200 |
| `/healthz` after injection attempt | 200 (DB intact) | ✅ 200 |
| Re-register with the same public key | 200 (identity table exists) | ✅ 200 |

SQLAlchemy uses parameterized queries throughout via `select(Model).
where(...)`. Across the entire `board_backend/` code there is **not a single**
`text()` raw-SQL with user input — verified via grep.

### 6. Forget-me path / signature mismatch

| Vector | Expected | Result |
|---|---|---|
| `DELETE /identity/{pub_a}` with `X-Pubkey: pub_b` and `Sig` from priv_b | 403 | ✅ 403 |

The path public key must match the `X-Pubkey` header — otherwise
one friend could "forget" another friend.

### 7. Rate limit (`POST /api/v1/identity/register`)

| Vector | Expected | Result |
|---|---|---|
| 15 requests from the same IP within 1 min | at least 1× 429 | ✅ 11× 429 after 4× 401 |

In-memory sliding-window limiter with 10 requests / 60 s / IP. After
4 failed auth attempts the limit kicks in, and further attempts
return 429 instead of validating the token.

---

## Known limitations

### Per-worker rate limit

The in-memory limiter is per **uvicorn worker**. The default compose setup
uses 1 worker. With `--workers 4` the limit would quadruple to
4 × 10 / min / IP. The README warns about this; for multi-worker
setups a Redis-backed rate limit or a Caddy-layer limit is recommended.

### `httpx` production dependency bug (fixed before this audit)

On the first container build for this audit, `httpx` was missing from the
production `pyproject.toml` — it was only listed under `[project.optional-
dependencies.dev]`, even though `routes/pair.py` and
`routes/reactions.py` import it. The container failed at startup with
`ModuleNotFoundError: No module named 'httpx'`.

**Fix:** `httpx>=0.27` moved from dev into `dependencies`.
The container build with `docker compose up --build` now builds cleanly.

Lesson: Phase D was tested manually against the Phase-C container; the
Phase-D container build was never run as a full stack. A
CI pipeline step "build + healthz" catches this systematically.

### Reverse proxy for body-size limits recommended

uvicorn has no default body limit. For VPS deployments a Caddy with
`request_body { max_size 64KB }` should sit in front of the backend —
this prevents a 1 GB POST from eating the container memory before the
Pydantic schema throws 422.

---

## Recipe: reproduce the audit

```sh
# 1. Start the backend with the current code
cd board-backend
JARVIS_BOARD_ADMIN_TOKEN=test-token-32chars-aaaabbbb1111ccc \
  docker compose up -d --build

# 2. (optional) view the container logs
docker compose logs -f &

# 3. Run the pen-test
cd ..
ADMIN_TOKEN=test-token-32chars-aaaabbbb1111ccc \
BACKEND_URL=http://localhost:8765 \
  python tools/board_pentest.py

# Expectation: "19/19 PASS, 0 FAIL"

# 4. Clean up
cd board-backend
docker compose down
```

---

## Future hardening (not blocking for v1.0)

- Argon2 hash for the admin token instead of plain string comparison
- mTLS between friend backends (in addition to Ed25519)
- Audit log for every 401/403/429 with a source-IP hash (privacy-preserving)
- Container image as a distroless build instead of python:3.11-slim
