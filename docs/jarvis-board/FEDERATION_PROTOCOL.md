# Jarvis Board — Federation Protocol v1

> Wire-format specification for alternative backend implementations.
> If you rewrite the backend in Go, Rust, or Elixir and want to federate
> with existing Jarvis Boards, this is your RFC.

---

## Versioning

- API paths start with `/api/v1/...`. **v1 is forever-stable.**
- Extensions arrive as `/api/v2/...` with their own schema.
- Within v1: only additive changes (new optional fields, new
  routes). Breaking changes trigger a major bump.

---

## Cryptography

### Algorithm

**Ed25519** for all signatures. No other scheme.

### Key format

- **Pubkey**: 32 bytes raw, transported as 64-character lowercase hex.
- **Signature**: 64 bytes raw, transported as 128-character lowercase hex.

Pydantic constraint in the reference backend:

```python
PubkeyHex   = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SignatureHex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")]
```

Implementations must normalize mixed-case hex to lowercase
**before** comparing (see the `forget_me` path match below).

### Canonical JSON

Before signing, the body is canonicalized:

- **Sorted keys** at every nesting level.
- **No whitespace** between tokens (`","` and `":"` as separators,
  not `", "` or `": "`).
- **UTF-8** encoding, no ASCII escapes for non-ASCII characters.

Reference implementation in Python:

```python
def canonical_json(payload) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
```

JS equivalent (for frontend sigs, in case someone builds that):

```js
function canonicalJson(obj) {
  return canonicalize(obj);   // recursive sort + JSON.stringify without whitespace
}
```

**Critical:** the server re-canonicalizes the parsed body **after**
receipt and verifies the signature against that — this protects against
whitespace manipulation by reverse proxies. Implementations MUST do it
this way, otherwise payloads repacked in transit by Caddy/nginx are rejected.

### Signature

```
sig_bytes = ed25519_sign(privkey_bytes, canonical_json(body))
sig_hex   = sig_bytes.hex()
```

### Verify

```
verify_with_recanonicalize(pubkey_hex, sig_hex, parsed_body):
  body_bytes = canonical_json(parsed_body)
  return ed25519_verify(unhex(pubkey_hex), unhex(sig_hex), body_bytes)
```

---

## HTTP wire format

### Headers

| Header | Routes | Content |
|---|---|---|
| `Content-Type` | all | `application/json` (also for GETs with a body, see below) |
| `X-Admin-Token` | only `POST /api/v1/identity/register` | Plain string, constant-time comparison against the server token |
| `X-Pubkey` | all signed routes | 64-char hex |
| `X-Jarvis-Sig` | all signed routes | 128-char hex |

### GET-with-body pattern

`GET /api/v1/me` and `GET /api/v1/federation/feed` use a
**JSON body** for `ts_ms`. This is HTTP-permitted (RFC 7230 §3.3) but
unconventional. If your reverse proxy drops GET bodies:

- **Caddy**: no problem.
- **old nginx versions**: set `client_body_in_single_buffer on;`.
- **Cloudflare**: GET bodies are stripped. → A backend behind
  Cloudflare is not directly recommended; alternative: move the body into an
  `X-Timestamp` header (would be a breaking change).

### Replay window

Every signed body MUST contain a top-level field `ts_ms`:

```json
{ "ts_ms": 1735000000000, "..." : "..." }
```

`ts_ms` is milliseconds since epoch (UTC). The server checks:

```
abs(server_now_ms - body.ts_ms) <= replay_window_seconds * 1000
```

Default window: **300 seconds** (5 minutes). On drift in either
direction → 401.

---

## Routes — Phase C (Identity / Sync)

### `POST /api/v1/identity/register`

**Auth:** `X-Admin-Token` (constant-time)

**Rate limit:** 10 requests / 60 s / IP (sliding window)

**Body:**

```json
{
  "pubkey": "<64-hex>",
  "display_name": "<1-120 chars>"
}
```

**200:**

```json
{
  "pubkey": "<64-hex>",
  "display_name": "<...>",
  "created_at": "<iso8601>"
}
```

**Idempotent:** re-registering with the same pubkey only updates
`display_name` (re-pairing scenario after a device change).

### `POST /api/v1/sync`

**Auth:** signed (X-Pubkey, X-Jarvis-Sig)

**Body:**

```json
{
  "ts_ms": 1735000000000,
  "display_name": "<1-120 chars>",
  "daily_stats": [DailyStatsItem, ...],
  "achievements": [AchievementItem, ...],
  "bio": "<optional, max 1000 chars>"
}
```

**`DailyStatsItem`:** required fields, `extra='forbid'`:

```json
{
  "date": "YYYY-MM-DD",
  "tasks_completed": 0,
  "tasks_failed": 0,
  "tools_used": ["bash", "..."],
  "unique_tools_count": 0,
  "voice_commands_count": 0,
  "voice_first_try_rate": 0.95,        // null allowed
  "hours_saved_estimate": 0.0
}
```

**`AchievementItem`:**

```json
{
  "id": "tool_master",
  "unlocked_at": "<iso8601>",
  "tier": "mastery"  // | "reflection" | "social"
}
```

**200:**

```json
{
  "accepted": true,
  "daily_stats_count": 1,
  "achievements_count": 1,
  "received_at": "<iso8601>"
}
```

**PII firewall:** the server rejects with 422 if the body contains fields
not present in the schema — whether top-level or nested. Implementations
**must not** silently discard unknown fields.

### `GET /api/v1/me`

**Auth:** signed (body: `{"ts_ms": ...}`)

**200:**

```json
{
  "pubkey": "...",
  "display_name": "...",
  "created_at": "...",
  "last_sync_at": "<iso | null>",
  "push_count": 0
}
```

---

## Routes — Phase D (Pair / Friends / Activity / Reactions)

### `POST /api/v1/pair/initiate`

**Auth:** `X-Admin-Token`

**Body:** `{"note": "<optional>"}`
**200:**

```json
{
  "token": "<32+ hex>",
  "url": "https://owner-backend.tld/api/v1/pair/redeem?token=...",
  "expires_at": "<iso8601, +10 min>",
  "owner_pubkey": "..."
}
```

### `POST /api/v1/pair/accept`

**Auth:** token-based (no X-Pubkey/Sig). The token is a
capability — whoever holds it may create a Friend entry.

**Body:**

```json
{
  "token": "<32+ hex>",
  "friend_pubkey": "<64-hex>",
  "friend_url": "https://friend-backend.tld",
  "friend_display_name": "<1-120 chars>"
}
```

**200:**

```json
{
  "accepted": true,
  "owner_pubkey": "...",
  "owner_url": "https://owner-backend.tld",
  "owner_display_name": "...",
  "paired_at": "<iso>"
}
```

**Token lifecycle:**

- 10-minute default TTL.
- Single-use: a second `accept` with the same token → 401 `"already used"`.
- Self-pair (`friend_pubkey == owner_pubkey`) → 400.

### `GET /api/v1/friends`

**Auth:** signed by owner

**200:**

```json
{
  "friends": [{
    "pubkey": "...",
    "url": "https://...",
    "display_name": "...",
    "paired_at": "...",
    "last_pull_at": "<iso | null>",
    "pull_interval_s": 120
  }]
}
```

### `PATCH /api/v1/friends/{pubkey}`

**Auth:** signed by owner

**Body:** `{"ts_ms": ..., "pull_interval_s": <60..3600>}`

Other Friend fields are **NOT** modifiable via this route. A
new Friend-URL = a new pair roundtrip.

### `POST /api/v1/activities`

**Auth:** signed by owner

**Body:**

```json
{
  "ts_ms": ...,
  "kind": "achievement_unlocked" | "story" | "milestone",
  "payload": { ... },
  "visibility": "private" | "friends" | "public",
  "expires_in_hours": null   // 1..168, null = never
}
```

**200:** `ActivityItemDTO` (see below).

### `POST /api/v1/stories`

**Auth:** signed by owner

**Body:** `{"ts_ms": ..., "text": "<1-280 chars>", "visibility": "..."}`

Server default `expires_in_hours = 24` for all stories. Implementations
MAY change this default, but must set the `expires_at` field in the DTO
correctly.

### `GET /api/v1/federation/feed`

**Auth:** signed by *any* Pubkey (not necessarily registered).

**Query parameters:**

- `sort`: `interesting` (default) or `latest`
- `since`: ISO-8601 timestamp (UTC). Items with `created_at < since`
  are filtered out. Optional, default none.

**Body:** `{"ts_ms": ...}`

**Visibility filter (SQL):**

```
WHERE author_pubkey = owner
  AND (visibility = 'public'
       OR (visibility = 'friends' AND viewer IN friends_of_owner)
       OR (visibility = 'private' AND viewer = owner))
  AND (since IS NULL OR created_at >= since)
  AND (expires_at IS NULL OR expires_at > NOW)
```

**Reaction-counts visibility:**

- If `viewer == author`: `reaction_counts: {rocket: N, brain: N, fire: N}`
- Otherwise: `reaction_counts: null, has_reactions: <bool>`

**200:**

```json
{
  "items": [ActivityItemDTO, ...],
  "sort": "interesting",
  "server_now": "<iso8601>"
}
```

**`ActivityItemDTO`:**

```json
{
  "id": "<32-hex>",
  "author_pubkey": "...",
  "author_display_name": "...",
  "kind": "...",
  "payload": { ... },
  "created_at": "<iso8601>",
  "visibility": "...",
  "expires_at": "<iso | null>",
  "reaction_counts": { "rocket": 1, "brain": 0, "fire": 0 },  // or null
  "has_reactions": true
}
```

### `POST /api/v1/reactions`

**Auth:** signed by owner

**Body:**

```json
{
  "ts_ms": ...,
  "item_id": "<32-hex>",
  "reaction": "rocket" | "brain" | "fire",
  "author_pubkey": "<64-hex>"   // = a known Friend of the reactor
}
```

The Owner backend finds the Friend by `author_pubkey` in its
`friends` table and forwards the **raw signed body** to
`{friend_url}/api/v1/federation/reactions/inbound`.

### `POST /api/v1/federation/reactions/inbound`

**Auth:** signed by the reactor (a Friend of the Owner).

**Body:** identical to `ReactionRequest`.

The receiving backend checks:
1. `(owner_pubkey, viewer_pubkey)` is in `friends` → otherwise 403.
2. `item_id` references an own `activity_item` → otherwise 404.
3. INSERT into `reactions` with `UNIQUE(item_id, reactor_pubkey, reaction)` —
   duplicate reactions are discarded as a no-op at the DB level.

### `DELETE /api/v1/federation/identity/{pubkey}`

**Auth:** signed by **the Friend** (= path pubkey).

The path pubkey MUST == the `X-Pubkey` header. Otherwise 403.

**200:**

```json
{
  "deleted_friendship": true,
  "deleted_activities": 0,
  "deleted_reactions": 5
}
```

`deleted_activities` is always 0 in Phase D — the Friend has no own
Activity items in the Owner backend (they host those themselves).

---

## Interesting algorithm (Plan §D-Spec)

```python
def interesting_score(reactions_total: int, age_hours: float) -> float:
    """ALGORITHM TRANSPARENT BY DESIGN."""
    return reactions_total * math.exp(-age_hours / 24.0)
```

- **Half-life:** 24 hours, hardcoded. Not tunable.
- **Tie-break:** on an equal score, sort by `created_at` desc
  (newer items first).
- **Determinism:** same input → same order. No RNG, no A/B test.

Alternative implementations MUST use this exact formula, otherwise
feed orderings diverge between Friend backends.

---

## Stories cleanup (Plan §D-Spec)

The server SHOULD run a periodic job:

```sql
DELETE FROM activity_items WHERE expires_at IS NOT NULL AND expires_at < NOW();
```

The tick frequency is implementation-dependent. The reference backend ticks
every hour (instead of nightly), so that a 24-h story stays visible for
~25 h at most instead of 48.

---

## What is NOT in the protocol (Plan §0)

- No public like-counts → `reaction_counts` is `null` for non-owners.
- No online indicators → no `is_online` field in the Friend DTO.
- No read receipts → no `seen_at` table.
- No lifetime-score routes → there is no aggregator endpoint.
- No notification-push routes → the server pushes nothing; everything is pull.

An alternative implementation **must not add** these fields
without breaking the Plan §0 contract with existing Jarvis Boards.
If you want that: major-bump to v2 with a clear opt-in.

---

## Test suite for compatibility

If you build an alternative implementation, it should pass the
reference `tools/board_pentest.py` script — all 19 vectors.
Plus the 9 plan-mandated smoke tests from the reference backend (see
`board-backend/tests/`):

- `test_pair_creates_bidirectional_friendship`
- `test_pair_token_expires_after_10min`
- `test_feed_excludes_non_friend_items`
- `test_visibility_private_never_appears_in_feed`
- `test_visibility_public_appears_in_feed_for_anyone`
- `test_story_expires_after_24h`
- `test_reaction_propagates_to_author_backend`
- `test_right_to_be_forgotten_removes_all_traces`
- `test_interesting_algorithm_is_deterministic`

Reference implementation: `https://github.com/<repo>/board-backend/`.
