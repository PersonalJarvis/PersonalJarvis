# OAuth app setup for the Asana, Gmail, Drive and Calendar plugins

These marketplace plugins use browser-login OAuth against an app **you** register
once. This is the providers' security model — no one can do it for you, and there
is no shared Jarvis-owned client: every user connects their *own* Google account
through their *own* OAuth client. After registering, you store the resulting
**Client ID** as a secret (preferred) or paste it into your local
`data/plugin_catalog.json` (gitignored runtime override) and restart.

| Plugin | App to register | Client ID placeholder to replace |
|---|---|---|
| Gmail | one Google Cloud "Desktop" OAuth client | `REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID` |
| Google Drive | **the same** Google client (shared) | `REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID` |
| Google Calendar | **the same** Google client (shared) | `REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID` |
| Asana | an Asana OAuth app | `REPLACE_WITH_JARVIS_ASANA_CLIENT_ID` |

---

## Part A — Google (covers Gmail, Drive AND Calendar with one client)

1. Go to <https://console.cloud.google.com> and create (or select) a project.
2. **APIs & Services → Library** → enable **Gmail API**, **Google Drive API**
   and **Google Calendar API** (enable only the ones whose plugins you want).
3. **APIs & Services → OAuth consent screen** → User type **External** → Create.
   Fill app name, your support email, and developer contact email → Save.
4. **Scopes** → add the ones you need: `.../auth/gmail.readonly`,
   `.../auth/gmail.send`, `.../auth/drive.file`, `.../auth/calendar.events`.
   (Drive's `drive.file` is non-sensitive; the Gmail scopes are
   sensitive/restricted; `calendar.events` is sensitive — see "Keeping it
   connected" below.)
5. **Test users** → add your own Google address. In Testing mode only listed
   users can authorize.
6. **Credentials → Create credentials → OAuth client ID** → Application type
   **Desktop app** → Create. Copy the **Client ID**
   (looks like `1234567890-abc….apps.googleusercontent.com`).
   The Desktop client's "secret" is usually not needed (PKCE protects the flow),
   but if Google rejects the token exchange/refresh with `invalid_client` you can
   also supply it (see below) — it is optional.
7. Give the Client ID to Jarvis. **Preferred: store it as a secret** so it
   survives a catalog re-sync (a plain edit of `data/plugin_catalog.json` is
   overwritten the next time the seed catalog is synced — this is how a working
   client can silently get reset back to the placeholder):

   ```bash
   # env var (simplest; works headless / VPS)
   set GOOGLE_OAUTH_CLIENT_ID=1234567890-abc….apps.googleusercontent.com
   # optional, only if Google demands it:
   set GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-…
   ```

   Or store it permanently in the credential manager (service
   `personal-jarvis`), keys `google_oauth_client_id` /
   `google_oauth_client_secret`. One client covers **Gmail, Drive AND Calendar**
   (the shared Google family). Then restart Jarvis and **connect the plugin** in
   the Plugins view.

   (Editing `data/plugin_catalog.json` directly still works as a fallback, but
   the secret takes precedence and is the durable option.)

### What happens when the connection dies

Jarvis now self-heals: when a Gmail call hits an expired token it refreshes once
and retries automatically. If the refresh can't succeed (revoked token, or the
client_id is still the placeholder), Jarvis flags the connection for re-auth — the
Plugins view shows **Reconnect** and the voice/chat reply says the Gmail
authorization expired and needs reconnecting, instead of a cryptic "expired" or
"timeout". Set a real `google_oauth_client_id` first (above), then reconnect.

### Keeping it connected (the 7-day rule)

Google issues a refresh token that **expires after 7 days** while your app's
publishing status is **Testing** and a sensitive/restricted scope is requested.
To keep a plugin connected permanently you must **publish the app to "In
production"** (Audience → Publish app). Even an *unverified* production app keeps
the refresh token alive for the app owner / test users — you just click through a
one-time "Google hasn't verified this app" notice when connecting:

- For **`drive.file`** (Drive): non-sensitive — publishing needs no verification,
  so Drive is permanent immediately after you publish (or even in testing it is
  unaffected by the 7-day rule because `drive.file` is non-sensitive… still,
  publish to be safe).
- For **`calendar.events`** (Calendar): **sensitive but not restricted** — same
  light path as Gmail send: publishing to production needs OAuth verification for
  *public* use but **no CASA assessment**. For your own account just publish to
  production (unverified is fine) and the connection stops expiring; voice
  commands like "what's on my calendar today" and "schedule a meeting tomorrow at
  3pm" then work indefinitely.
- For **Gmail read** (`gmail.readonly`, restricted): publishing to production
  requires Google's OAuth app **verification + a CASA security assessment**
  (several weeks). Until that completes, Gmail works but reconnects ~weekly.
- For **Gmail send only** (`gmail.send`, sensitive but not restricted): a far
  lighter path — production needs OAuth verification but **no** CASA assessment.

For just Calendar + Drive + Gmail-send, the quick path is: publish the app to
production and connect — no verification needed for your own use. The heavy
verification + CASA path is only required for the restricted Gmail read scope, or
for distributing the app publicly.

---

## Part B — Asana

1. Go to <https://app.asana.com/0/my-apps> → create a new app/project.
2. Under the app's **OAuth** settings, add the redirect URI
   `http://127.0.0.1:3119/oauth/callback`.
3. Copy the **Client ID**.
4. In `data/plugin_catalog.json`, replace `REPLACE_WITH_JARVIS_ASANA_CLIENT_ID`
   (the `asana` entry) with it. Restart Jarvis.

**Loopback caveat:** Asana's docs only document `https` / `oob` redirect URIs. If
Asana rejects the `http://127.0.0.1:3119` loopback at registration, fall back to
a Personal Access Token instead: change the `asana` entry's `auth` block to
`pat_paste` (`token_creation_url: https://app.asana.com/0/my-apps`,
`token_prefix: ""`, `validation_endpoint: https://app.asana.com/api/1.0/users/me`,
default `bearer` scheme) and point `mcp_server` at a community Asana stdio MCP
(the hosted V2 server rejects PATs).

---

## Applying Client IDs

Two ways, in precedence order:

1. **Secret (recommended, durable).** Google: `google_oauth_client_id`
   (+ optional `google_oauth_client_secret`). These override the catalog at
   connect-time *and* refresh-time and survive a catalog re-sync. Set them as env
   vars or in the credential manager (service `personal-jarvis`).
2. **`data/plugin_catalog.json` (fallback).** Your local, gitignored runtime
   override; edit the `gmail`/`google_drive`/`google_calendar`/`asana` entries
   and restart. Note this file is re-synced from the seed, so a real Client ID
   written here can be reset back to the placeholder — prefer the secret for
   Google.

After any change, restart Jarvis and reconnect the plugin. The tracked
`jarvis/marketplace/seed_catalog.json` keeps the placeholders (never commit your
real Client IDs).
