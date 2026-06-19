# Migration to Jarvis Board v1.0

> For existing Jarvis users who already run Phase 5 and now want
> to activate Board (A-D).

There are **four possible end states**, depending on how much you want
to use. Each tier is additive — if you only want A, leave out the rest.

---

## Tier 0 — Change nothing

Board is **default off**. If you `git pull` and don't touch
`jarvis.toml`, nothing changes for you. The Board tables
are only created the first time you start the Aggregator.

---

## Tier 1 — Personal Dashboard, local only (Phase A+B)

This is the standard variant: your own dashboard without federation.

```sh
git pull
pip install -r requirements.txt   # no new deps for A+B (recharts comes
                                  # into the frontend only, which we build locally)
cd jarvis/ui/web/frontend
npm install                       # pulls recharts + qrcode.react
npm run build
```

**What happens on first start:**
- `python -m jarvis.ui.web.launcher` → the web server starts `BoardAggregator`
  as a background task (every 6 h plus on-startup).
- The Aggregator reads your existing FlightRecorder JSONLs from
  `%LOCALAPPDATA%\Jarvis\logs\` and writes to
  `%LOCALAPPDATA%\Jarvis\data\board\personal.db`.
- UI: **Board** (Sparkles icon) appears in the sidebar.

**Achievement engine + bio**:
- Works immediately — the `AchievementEvaluator` is a bus subscriber.
- The bio is generated automatically every week (Sunday 18:00). The brain
  is auto-detected from your `[brain]` section.

**If you weren't running a FlightRecorder:**
In `jarvis.toml`:

```toml
[telemetry.flight_recorder_v2]
enabled = true
data_dir = "data/flight_recorder"
retention_days = 14
```

Achievements only unlock once the FlightRecorder collects events.

---

## Tier 2 — Self-host the Federation Backend (Phase C)

If you want your backend on your own hardware — without Friends, just
for backups + cross-device sync.

### Deploy the backend

```sh
cd board-backend
cp .env.example .env
# .env: JARVIS_BOARD_ADMIN_TOKEN=<32+ random hex>
docker compose up -d --build
```

Three setup scenarios (Localhost / Raspi / Hetzner+Caddy) in
`board-backend/README.md`.

### Configure the local Jarvis

`jarvis.toml`:

```toml
[board.federation]
enabled = true
backend_url = "http://localhost:8765"   # or your VPS / Pi
sync_interval_s = 60
display_name = "Mein Desktop"
```

### Store the admin token in the Credential Manager

```sh
python -c "import keyring; \
  keyring.set_password('jarvis-board', 'admin_token', '<your-hex-token>')"
```

On the next Jarvis start the `SyncClient` registers itself automatically
with the backend (a one-time admin-token roundtrip), generates an Ed25519
keypair, and pushes every 60 s. The privkey lands in the Credential Manager under
`jarvis-board / sync_privkey_hex`.

**Verify:**

```sh
curl http://localhost:8765/healthz
# {"ok":true,"version":"0.1.0","schema_ok":true}
```

In the UI: **Sidebar → Settings → Backend Connection** shows
your pubkey + status.

---

## Tier 3 — Pair Friends (Phase D)

Requires Tier 2. Both sides need a running backend.

1. You click **Sidebar → Friends → "Pair" → "I'm inviting" → "Generate link"**.
2. The UI shows a QR code + URL. Send the URL via Signal/Threema/SMS.
3. The Friend opens their **Sidebar → Friends → "Pair" → "I'm following a link"**,
   pastes your URL, and clicks **"Run pair"**.
4. Both sides now have a Friend entry. The activity feed is
   pulled every 2 min (configurable per-Friend in the **Manage** tab).

**Send a reaction:** click the 🚀 button on the feed item. The Owner later
sees the reaction counts with numbers; you as the reactor only see icons.

**Post a story:** the **"Story"** button in the feed header → text (max 280) +
visibility (Private/Friends/Public) → Post. Disappears after 24 h.

**Disconnect:** **Sidebar → Settings → Backend Connection →
Disconnect** turns federation off in-memory (local-only mode). For
permanent → `enabled = false` in `jarvis.toml`.

**Forget-Me:** if your Friend has removed you, you can delete your traces
on their side via a signed DELETE. A UI for this is coming in a follow-up PR;
for now: the `curl` recipe in `FEDERATION_PROTOCOL.md`.

---

## What changes technically

### New jarvis.toml section

```toml
[board.federation]
enabled = false            # default
backend_url = ""
sync_interval_s = 60
display_name = ""
```

### New sidebar sections

- **Board** (Sparkles icon) — Personal Dashboard, Achievements, AI-Bio.
- **Friends** (Users icon) — visible, but functionally inactive
  until `[board.federation] enabled = true`.

### New tables

Locally in `personal.db`: `daily_stats`, `personal_records`, `achievements`,
`bio`, `aggregator_meta`. The schema migration is additive via
`CREATE TABLE IF NOT EXISTS`.

### New Credential Manager keys

- `jarvis-board / sync_privkey_hex` (auto-generated on the first push)
- `jarvis-board / admin_token` (set by you for Tier 2+)

### New background tasks in the web server

- `board-aggregator` (every 6 h)
- `bio-scheduler` (asyncio tick 60 s, checks Sunday 18:00 + master achievements)
- `board-sync` (every 60 s, only when federation is enabled)

All three are spun up in `WebServer.start()` and shut down in an
orderly fashion in `WebServer.stop()`.

---

## Known issues

See `PHASE_D_DONE.md §4` for the full list with workarounds.
Highlights:

- **One-sided friendship on a pair crash**: have the Friend pair again.
- **Pubkey loss = identity loss**: export the privkey from the Credential
  Manager and back it up, otherwise Friends can no longer
  identify you.
- **Auto-pull only pulls public items**: for friends items, the UI must pull
  live. The UI does this every 2 min.

---

## Rollback

If you want to get rid of Board again:

```toml
# jarvis.toml
[board.federation]
enabled = false
```

Plus, optionally, delete the keys:

```sh
python -c "import keyring; \
  keyring.delete_password('jarvis-board', 'admin_token'); \
  keyring.delete_password('jarvis-board', 'sync_privkey_hex')"
```

The DBs stay — the Aggregator still runs in the background while the web
server is running. If you really want everything gone:

```sh
rm -rf "%LOCALAPPDATA%\Jarvis\data\board\"
```

On the backend (Tier 2+):

```sh
cd board-backend
docker compose down -v          # -v deletes the db_data volume
```
