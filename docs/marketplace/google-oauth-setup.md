# OAuth app setup for the Asana, Gmail, Drive, Calendar and YouTube Music plugins

> **Google: nothing to set up.** Since 2026-10-05 the app ships the project's
> shared "Personal Jarvis" Google Desktop client for Gmail, Google Drive,
> Google Calendar, YouTube Music, YouTube Studio and Google Cloud. Click
> **Connect**, sign in at Google in the browser, done. Everything below in
> Part A is the **expert** path for people who want their own Google client.
> Asana (Part B) still needs its publisher registration (tracked in
> `plugin-auth-audit.md`).

### How the shared Google client works without shipping a secret

Google's token endpoint refuses a code exchange or a refresh for a Desktop
client unless the request carries the client secret, even with PKCE
(`invalid_request: "client_secret is missing."`). A secret inside an
installable app is not a secret, so the app never holds it. Instead, the two
token calls for the shared client go to the project's token broker at
`https://token.personaljarvis.ai/oauth/google/token`. That small service adds
the client id and secret, forwards the call to Google, and hands Google's
answer back unchanged. It accepts only:

- a code exchange with a PKCE `code_verifier` and a loopback `redirect_uri`
  (`http://127.0.0.1:<port>/…`), and
- a refresh.

It stores nothing and logs nothing. Its source is `workers/token-broker` in
the website repository.

What goes where:

| Step | Shared client | Your own client (expert) |
|---|---|---|
| Browser sign-in | `accounts.google.com`, loopback redirect | same |
| Code exchange + refresh | token broker | `oauth2.googleapis.com/token` with your secret |
| Disconnect (revocation) | `oauth2.googleapis.com/revoke` directly | same |

The routing follows the grant, not today's settings: a grant made with the
shared client keeps refreshing through the broker even if you later add your
own client, and vice versa. It works the same for every install type
(desktop app, `pip`, CLI) and on every OS. It needs network access to
`token.personaljarvis.ai` at connect and refresh time.

**Status:** the broker is live and was checked with made-up codes: Google
answers `invalid_grant`, which shows the secret is attached. A full sign-in
with a real account through the app has **not** been verified end to end yet
(see `plugin-auth-audit.md`).

### Your own Google client (expert)

These plugins can also use OAuth against an app **you** register once, for
example to own the consent screen or to lift the shared client's limits.

The simplest way to hand the resulting **Client ID** to Jarvis is right in the
app: click **Connect** on the plugin, expand **"Use your own OAuth client
(advanced)"**, and paste your Client ID and secret there — no env vars, no
file edits, no restart. Jarvis stores it as a secret for you, and your own
client always wins over the shared one. (You can still set it as an env var /
credential-manager secret or edit `data/plugin_catalog.json`; see "Applying
Client IDs" below.)

| Plugin | App to register | Client ID placeholder to replace |
|---|---|---|
| Gmail | one Google Cloud "Desktop" OAuth client | `REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID` |
| Google Drive | **the same** Google client (shared) | `REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID` |
| Google Calendar | **the same** Google client (shared) | `REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID` |
| YouTube Music | **the same** Google client (shared) — see [`youtube-music-setup.md`](youtube-music-setup.md) | `REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID` |
| Asana | an Asana OAuth app | `REPLACE_WITH_JARVIS_ASANA_CLIENT_ID` |

---

## Part A — Google (covers Gmail, Drive, Calendar AND YouTube Music with one client)

1. Go to <https://console.cloud.google.com> and create (or select) a project.
2. **APIs & Services → Library** → enable **Gmail API**, **Google Drive API**,
   **Google Calendar API** and **YouTube Data API v3** (enable only the ones
   whose plugins you want).
3. **APIs & Services → OAuth consent screen** → User type **External** → Create.
   Fill app name, your support email, and developer contact email → Save.
4. **Scopes** → add the ones the plugins you want request (these are the exact
   scopes in the shipped catalog):

   | Plugin | Scope | Google class |
   |---|---|---|
   | Gmail | `https://mail.google.com/` | restricted |
   | Google Drive | `.../auth/drive` | restricted |
   | Google Calendar | `.../auth/calendar` | sensitive |
   | YouTube Music | `.../auth/youtube` | sensitive |
   | YouTube Studio | `.../auth/youtube.force-ssl`, `.../auth/youtube.upload`, `.../auth/yt-analytics.readonly` | sensitive |
   | Google Cloud | `.../auth/cloud-platform.read-only`, `.../auth/bigquery` | sensitive |

   The full `calendar` scope lets Jarvis read events across ALL your calendars,
   not just the primary one, so a lesson on a secondary "School" calendar isn't
   missed. See "Keeping it connected" for what the classes mean.
5. **Test users** → add your own Google address. In Testing mode only listed
   users can authorize — an unlisted account ends Connect on Google's
   **"Access blocked: <app> has not completed the Google verification process
   / Error 403: access_denied"** page. That page means exactly this missing
   entry (or: publish the app, see "Keeping it connected").
6. **Credentials → Create credentials → OAuth client ID** → Application type
   **Desktop app** → Create. Copy the **Client ID**
   (looks like `1234567890-abc….apps.googleusercontent.com`).
   Copy the **Client secret** as well: Google refuses the token exchange and
   refresh for a Desktop client without it, even with PKCE
   (`invalid_request: "client_secret is missing."`).
6b. **Redirect addresses.** A **Desktop app** client has no redirect URI
    setting: Google accepts any `http://127.0.0.1:<port>` loopback address for
    it, so there is nothing to register. Only if you created a **Web
    application** client instead must you list exactly the addresses Jarvis
    listens on (a missing entry ends Connect with `redirect_uri_mismatch`):

    ```
    http://127.0.0.1:3120                  (Drive)
    http://127.0.0.1:3121                  (Gmail)
    http://127.0.0.1:3122                  (Calendar)
    http://127.0.0.1:3123                  (YouTube Music)
    http://127.0.0.1:43891/oauth/callback  (Google Cloud, YouTube Studio)
    ```
7. Give the Client ID to Jarvis. **Preferred: store it as a secret** so it
   survives a catalog re-sync (a plain edit of `data/plugin_catalog.json` is
   overwritten the next time the seed catalog is synced — this is how a working
   client can silently get reset back to the placeholder):

   ```bash
   # env var (simplest; works headless / VPS)
   set GOOGLE_OAUTH_CLIENT_ID=1234567890-abc….apps.googleusercontent.com
   # required for a Desktop client:
   set GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-…
   ```

   Or store it permanently in the credential manager (service
   `personal-jarvis`), keys `google_oauth_client_id` /
   `google_oauth_client_secret`. One client covers **Gmail, Drive, Calendar AND
   YouTube Music** (the shared Google family). Then restart Jarvis and
   **connect the plugin** in the Plugins view.

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

The card also says **why** it broke and when — "The provider withdrew the
authorization · 10 days ago" — because the four causes need different responses
and used to be indistinguishable once the log line rotated away:

| What the card says | What it means | What fixes it |
|---|---|---|
| The provider withdrew the authorization | The grant is gone: a Testing-mode expiry, a revoke, or an account policy change | Reconnect; for the Testing-mode case, publish the app first (below) |
| The provider no longer accepts this app's OAuth client | The OAuth client itself was refused or has been dropped | Check the client still exists, then reconnect |
| Connected before Jarvis stored the OAuth client | An old token that predates the client_id being persisted | Reconnect once; it will not recur |
| A renewed token could not be saved, so it was retired | The provider rotated the token but the write failed | Reconnect (this is the one case Jarvis will not retry — see below) |

Jarvis also retries a flagged connection **once a day** on its own, so one
provider outage or DNS blip no longer strands a grant whose refresh token is
still good — it comes back without you doing anything. The single exception is
the last row: replaying a token the provider has already retired is what some
providers treat as theft and answer by revoking every token on the account, so
that one waits for a deliberate reconnect.

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

- **Calendar, YouTube and Google Cloud scopes** are sensitive.
- **Gmail `https://mail.google.com/`** and **Drive `drive`** are restricted.
  Public distribution requires Google's verification, and restricted scopes
  can additionally require a security assessment. Personal use by a small
  number of known users may qualify for Google's verification exception: an
  **unverified** production app still works, but every consent shows the
  "Google hasn't verified this app" warning and the app has a lifetime cap of
  100 users. Verification is a Google Cloud Console process; nothing in Jarvis
  can submit or complete it.

**How to tell whether a grant is time-limited.** When Google caps a grant
(Testing status, or access the user granted for a limited time), its token
response carries `refresh_token_expires_in`. Jarvis records the end in the
connection (`refresh_expires_at`, served by `GET /api/marketplace/plugins`) and
logs at connect time: `the provider limits this sign-in to 7.0 days`. A
production grant carries no such field and no such log line.

Production status removes the scheduled seven-day Testing expiry; it does not
make a grant irrevocable. Google also ends a refresh token when:

- the user removes the app under their Google Account's third-party access,
  or an administrator's policy does;
- **any** token of the same Google Cloud project is revoked. Google's
  revocation is project-wide: it ends every grant of every client in that
  project. Jarvis therefore only revokes at Google when the LAST connected
  Google plugin is disconnected (the disconnect response then says
  `"revocation": "shared"` for the earlier ones). Another tool that uses an
  OAuth client from the same project (for example a command-line Google
  client) ends all Jarvis grants when it logs out or revokes its token — give
  such tools their own project;
- the password changes and the grant contains Gmail scopes;
- the account holds more than 100 live refresh tokens for the same client
  (every Connect or Reconnect issues one; Google drops the oldest silently);
- a refresh token goes unused for six months (Jarvis refreshes every
  connection at least every 12 hours, so this does not happen while Jarvis
  runs).

Jarvis handles ordinary access-token expiry automatically and only asks for
reconnection when the refresh grant itself is no longer usable. A deleted or
disabled OAuth client is reported as "The provider no longer accepts this
app's OAuth client" instead of being retried forever.

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
   (+ `google_oauth_client_secret`); Asana: `asana_oauth_client_id`;
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
