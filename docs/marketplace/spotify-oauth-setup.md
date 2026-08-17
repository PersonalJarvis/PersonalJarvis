# Spotify plugin setup

The Spotify plugin logs in through an app **you** register once, and there is no
way around that: since 11 February 2026 a Spotify Development Mode app allows at
most **five authorized users**, so no shared Jarvis-owned client could ever serve
everyone who downloads this project. Every install registers its own.

The good news is that Spotify's version of this is the shortest in the catalog.
It takes about five minutes, needs **no client secret**, and — unlike Google —
has no consent-screen review and no verification queue.

## Before you start

**Controlling playback requires Spotify Premium.** Play, pause, skip and volume
all answer `403` for a free account. That is Spotify's rule, not a limitation of
this app. Reading what is currently playing works on any account, so a free
listener still gets a useful plugin, just not a remote control.

Since February 2026 the *owner* of a Development Mode app also has to hold an
active Premium subscription. If yours lapses, the app stops working.

## Part A — register the app

1. Sign in at <https://developer.spotify.com/dashboard> and click **Create app**.
2. Fill in:
   - **App name** — anything, e.g. `Personal Jarvis`
   - **App description** — anything, e.g. `Personal voice assistant`
   - **Redirect URI** — exactly this, then click **Add**:

     ```
     http://127.0.0.1:3125/callback
     ```

   - **Which API/SDKs are you planning to use?** — tick **Web API**
3. Accept the terms and click **Save**.
4. Open the app's **Settings** and copy the **Client ID**.

Two mistakes to avoid on step 2, both of which produce
`INVALID_CLIENT: Invalid redirect URI` at login:

- **`localhost` does not work.** Spotify explicitly rejects it and requires the
  loopback IP literal. It must read `127.0.0.1`.
- **The URI must match character for character**, including the port and the
  `/callback` path. No trailing slash.

You do **not** need the client secret. Jarvis uses PKCE, which secures the flow
without one.

## Part B — connect it in Jarvis

1. Open **Plugins**, find the Spotify card, click **Connect**.
2. Expand **"Use your own OAuth client (advanced)"** and paste the Client ID.
   Jarvis stores it in the credential manager as `spotify_oauth_client_id`;
   leave the secret field empty.
3. Continue. Your browser opens Spotify's approval page, which lists what Jarvis
   is asking for. Approve it.
4. The tab reports `Connected.` and can be closed.

Prefer the in-app field over editing `data/plugin_catalog.json` by hand: a
catalog re-sync overwrites that file and would silently reset a working client
back to the placeholder. The environment variable `SPOTIFY_OAUTH_CLIENT_ID`
works too.

## Keeping it connected

Spotify caps a refresh token at **six months**. Jarvis renews the hourly access
token in the background without asking, but twice a year the card will ask you
to reconnect. Nothing can extend that — it is a fixed ceiling on Spotify's side,
which is why the card says "provider limited" rather than promising forever.

**Disconnecting** removes the local credential. Spotify publishes no revocation
endpoint, so to withdraw the authorization at Spotify as well, go to
**Account → Apps** on spotify.com and remove it there.

## What you can say once it is connected

- "play Radiohead" · "play my running playlist" · "play the new Bonobo album"
- "skip this one" · "go back" · "pause the music" · "carry on"
- "what song is this?"
- "turn the music down"
- "queue up Bohemian Rhapsody"

## When something does not work

| What you see | What it means |
|---|---|
| `INVALID_CLIENT: Invalid redirect URI` | The redirect URI in the dashboard is not exactly `http://127.0.0.1:3125/callback` — most often `localhost` instead of `127.0.0.1`. |
| "Spotify only allows remote control with Premium" | The account is on the free tier. Reading still works; controlling does not. |
| "Spotify is not open anywhere" | The Web API commands Spotify Connect, it does not play audio itself. Open Spotify on any device — phone, desktop, speaker — and it becomes controllable. |
| "Spotify is idle on several devices" | More than one client is open and none is active. Say which one, or press play once on the device you mean. |
| The card asks to reconnect after months | The six-month refresh-token ceiling. Reconnect once. |

## Why not an off-the-shelf MCP server?

Spotify publishes no MCP server and no agent integration of any kind — only the
Web API. The community servers that exist want a plaintext `spotify-config.json`
holding a client **secret** plus a terminal `npm run auth` step, which would
replace this repo's keyring-backed Connect button with a worse and less safe
flow. Hence a native tool, matching Gmail, Drive, Calendar and Home Assistant.
