# OAuth app setup for the Asana, Gmail, Drive and Calendar plugins

These marketplace plugins use browser-login OAuth against an app **you** register
once. This is the providers' security model — no one can do it for you, and there
is no shared Jarvis-owned client: every user connects their *own* Google account
through their *own* OAuth client.

The simplest way to hand the resulting **Client ID** to Jarvis is right in the
app: click **Connect** on the plugin, expand **"Use your own OAuth client
(advanced)"**, and paste your Client ID (and secret, if needed) there — no env
vars, no file edits, no restart. Jarvis stores it as a secret for you. (You can
still set it as an env var / credential-manager secret or edit
`data/plugin_catalog.json`; see "Applying Client IDs" below.)

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
   `.../auth/gmail.send`, `.../auth/drive.file`, `.../auth/calendar`.
   (Drive's `drive.file` is non-sensitive; the Gmail scopes are
   sensitive/restricted; the full `calendar` scope is sensitive — it lets Jarvis
   read events across ALL your calendars, not just the primary one, so a lesson
   on a secondary "School" calendar isn't missed. See "Keeping it connected".)
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
client_id is still the placeholder), Jarvis flags the connection for re-auth and
makes it impossible to miss:

- the plugin **stays in the Plugins "Installed" tab** with an amber
  **"Reconnect needed"** badge and a one-click **Reconnect** button (it does NOT
  silently drop back to "Browse"),
- the **sidebar shows an amber dot** on the "Skills & Tools" row, so a dead
  connection is visible from anywhere in the app,
- and the voice/chat reply says the authorization expired and needs reconnecting,
  instead of a cryptic "expired" or "timeout".

Click **Reconnect**, sign in again, and you're back. If you never set a real
client, do that in the same dialog first (above), then reconnect.

### Keeping it connected (the 7-day rule)

Google authorizations issued while an external app's publishing status is
**Testing** expire seven days after consent whenever the request includes any
scope beyond basic identity (`openid`, email, and profile). That includes
`drive.file`, `calendar`, and the Gmail scopes. Jarvis cannot extend that
provider-enforced lifetime.

For durable offline refresh, open **Google Auth Platform → Audience** and choose
**Publish app** so the project is **In production**. A Google Workspace project
used only inside its organization can instead use an **Internal** audience. Then
reconnect each Google plugin once so Google issues a grant under the new audience
configuration.

- **Drive `drive.file`** is non-sensitive, but it is still outside the basic
  identity-only exception to Testing's seven-day lifetime.
- **Calendar `calendar`** and **Gmail `gmail.send`** are sensitive scopes.
- **Gmail `gmail.readonly`** is restricted. Public distribution can require
  Google's restricted-scope verification, and systems that transmit or store
  restricted data through a third-party server can require a security assessment.
  Personal use by a small number of known users may qualify for Google's
  verification exception, subject to an unverified-app warning and user cap.

Production status removes the scheduled seven-day Testing expiry; it does not
make a grant irrevocable. Google can still require authorization again after a
user revokes access, an account or administrator changes policy, credentials are
rotated, or another provider security condition invalidates the refresh token.
Jarvis handles ordinary access-token expiry automatically and only asks for
reconnection when the refresh grant itself is no longer usable.

---

## Part B — Asana

1. Go to <https://app.asana.com/0/my-apps> → create a new app/project.
2. Under the app's **OAuth** settings, add the redirect URI
   `http://127.0.0.1:3119/oauth/callback`.
3. Copy the **Client ID**.
4. Hand it to Jarvis the same way as Google — easiest is the in-app **Connect**
   dialog ("Use your own OAuth client"), which stores the `asana_oauth_client_id`
   secret for you. (Setting that secret yourself, or editing
   `data/plugin_catalog.json` + restart, also works.)

**Loopback caveat:** Asana's docs only document `https` / `oob` redirect URIs. If
Asana rejects the `http://127.0.0.1:3119` loopback at registration, fall back to
a Personal Access Token instead: change the `asana` entry's `auth` block to
`pat_paste` (`token_creation_url: https://app.asana.com/0/my-apps`,
`token_prefix: ""`, `validation_endpoint: https://app.asana.com/api/1.0/users/me`,
default `bearer` scheme) and point `mcp_server` at a community Asana stdio MCP
(the hosted V2 server rejects PATs).

---

## Applying Client IDs

Three ways, in precedence order:

1. **In-app, in the Connect dialog (recommended).** Click **Connect** (or
   **Reconnect**) on the plugin → expand **"Use your own OAuth client
   (advanced)"** → paste the Client ID (+ secret if needed). Jarvis writes it to
   the right secret for you (Google: `google_oauth_client_id`; Asana:
   `asana_oauth_client_id`; Slack: `slack_oauth_client_id`) — no env vars, no file
   edits, no restart. This is the same durable secret as option 2, just entered
   from the UI.
2. **Secret directly (headless / scripted).** Set the credential-manager secret
   or env var yourself — Google: `google_oauth_client_id`
   (+ optional `google_oauth_client_secret`); Asana: `asana_oauth_client_id`;
   Slack: `slack_oauth_client_id`. These override the catalog at connect-time
   *and* refresh-time and survive a catalog re-sync. On a headless host with no OS
   keyring they fall back to `.env` / a local file automatically.
3. **`data/plugin_catalog.json` (fallback).** Your local, gitignored runtime
   override; edit the `gmail`/`google_drive`/`google_calendar`/`asana` entries
   and restart. Note this file is re-synced from the seed, so a real Client ID
   written here can be reset back to the placeholder — prefer a secret.

After any change, restart Jarvis and reconnect the plugin. The tracked
`jarvis/marketplace/seed_catalog.json` keeps the placeholders (never commit your
real Client IDs).
