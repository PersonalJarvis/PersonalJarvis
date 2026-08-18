# YouTube Music plugin setup

The YouTube Music plugin logs in with the **same Google OAuth client** as
Gmail, Google Drive and Google Calendar. If you already connected one of those,
this takes two minutes: enable one more API in that Cloud project and press
Connect. If you have not, follow Part A of
[`google-oauth-setup.md`](google-oauth-setup.md) once — every Google plugin
shares that client — and come back here.

There is no way around the Google Cloud console: Google publishes **no YouTube
Music API** at all, and the official API that does exist (the YouTube Data API
v3) is only reachable through a Cloud project you own. The community "YouTube
Music APIs" are reverse-engineered private clients that break with every
redesign and sit outside YouTube's terms, so this project does not ship one.

## What you get — and what YouTube does not offer

| You say | What happens |
|---|---|
| "play Radiohead" · "play Karma Police" · "play the album OK Computer" · "play my running playlist" · "play my liked songs" | Jarvis finds it and opens it in **YouTube Music in your browser** — your own account, your Premium, your history. A song opens as its own radio, so music keeps coming. |
| "pause" · "carry on" · "skip this" · "go back" | Sent to the browser through the system's media session — the same channel your keyboard's play/pause key uses. |
| "what song is this?" | Read from that media session: track, artist, album, and which app it comes out of. |
| "like this song" · "add this to my running playlist" · "create a playlist called Late night" · "what is in my chill playlist?" | Written to your YouTube library through the official API. |
| "turn it up" | **Not available.** YouTube exposes no volume control; use the system or app volume. |
| "queue this next" | **Not available.** There is no queue API; Jarvis offers to play it now or add it to a playlist instead. |

Playback happens **in YouTube Music, not in Jarvis** — exactly like the Spotify
plugin, where the music comes out of Spotify. That is deliberate: it keeps your
Premium (no ads), your recommendations and your history in your own account.

## Part A — the Google Cloud project (once)

If you have not set up the shared Google client yet, do
[Part A of the Google setup](google-oauth-setup.md) first. Then, in the same
project:

1. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
2. **APIs & Services → OAuth consent screen → Scopes** → add
   `https://www.googleapis.com/auth/youtube` (it is a *sensitive* scope; for
   personal use in Testing mode no verification is needed).
3. That is all — the OAuth client itself is the one you already have. Nothing
   to register: the loopback redirect works out of the box for a Google
   **Desktop app** client.

## Part B — connect it in Jarvis

1. Open **Plugins**, find the YouTube Music card, click **Connect**.
2. If Jarvis does not have your Google Client ID yet, expand **"Use your own
   OAuth client (advanced)"** and paste it. It is stored as
   `google_oauth_client_id` — one client for Gmail, Drive, Calendar and
   YouTube Music.
3. Your browser opens Google's approval page. Approve the **YouTube** permission.
4. The tab reports `Connected.` and can be closed.

## Part C — playback control on your machine

Reading "what is playing" and pause/skip use the OS media session:

| OS | Out of the box | If not |
|---|---|---|
| **Windows** | Yes — reads and steers through WinRT (part of the `[desktop]` extras). | Without the extras, pause/skip still work as blind media keys; "what is playing" says it cannot read. `pip install "personal-jarvis[desktop]"` fixes it. |
| **Linux** | Needs `playerctl` (`sudo apt install playerctl`). | Without it, the answer names that command; playing and the library actions work regardless. |
| **macOS** | Needs `nowplaying-cli` (`brew install nowplaying-cli`). Apple ships no public now-playing API. | Without it, control YouTube Music directly; playing and the library actions work regardless. |
| **Headless / VPS** | No player exists there. | "Play" returns the link so you can open it on a device with YouTube Music. |

## Keeping it connected

Google's rules for the shared client apply: while your OAuth app is in
**Testing** mode a grant expires after seven days, so publish the app to
**In production** (see the Google doc's "Keeping it connected"). Jarvis renews
the hourly access token itself and only asks to reconnect when the grant is
truly gone.

## Limits worth knowing

- **100 searches a day.** Google gives every Cloud project 100 `search.list`
  calls a day, separate from the 10,000-unit pool the other calls use. Every
  "play <name>" is one search (repeats within a session are cached; your own
  playlists and liked songs cost none). Enough for a household, not for a
  party. When they are gone, Jarvis says so; pausing, skipping and your own
  playlists keep working. The counter resets at midnight Pacific time.
- **First autoplay.** A browser blocks autoplay on a site until you have used
  it there. If Jarvis reports "opened, but playback not confirmed", press play
  once — after that it starts on its own.
- **A song is a video.** YouTube's top hit is sometimes the music video or a
  live version. Jarvis says what it actually started so you notice at once.
- **Two tabs.** Each "play" opens a new YouTube Music tab; Jarvis pauses the
  old one first, so only one plays. Close the extras whenever you like.

## When something does not work

| What you see | What it means |
|---|---|
| "The YouTube Data API v3 is not enabled" | Part A step 1 was skipped in the Cloud project behind your client. |
| "The stored Google authorization does not include YouTube" | The scope was not approved — reconnect and tick the YouTube permission. |
| "100 YouTube searches a day … used up" | Google's daily search bucket. Resets at midnight Pacific. |
| "opened, but playback not confirmed" | The browser withheld autoplay; press play once. |
| "Nothing is registered as playing" | No app on this machine has a media session — open YouTube Music (say "open YouTube Music") or play something first. |
| "This machine cannot control playback" | Linux without `playerctl` / macOS without `nowplaying-cli`; the message names the install command. |
| The card asks to reconnect after a week | The Testing-mode seven-day rule; publish the app. |

## Why not an off-the-shelf MCP server?

Google publishes no YouTube Music MCP server, and every community one wraps the
same unofficial private client (browser cookies or a reverse-engineered OAuth
flow) — a fragile, terms-of-service-grey base this project will not put under
a store card. The official Data API plus the OS media session covers playing,
control, "what is playing" and the library, honestly and durably.
