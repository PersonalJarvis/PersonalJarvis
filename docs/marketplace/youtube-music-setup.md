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
| "play Radiohead" · "play Karma Police" · "play the album OK Computer" · "play my running playlist" · "play my liked songs" | Jarvis finds it and starts it in **YouTube Music** — in the background player (default) or your browser — on your own account, with your Premium and history. A song opens as its own radio, so music keeps coming. |
| "pause" · "carry on" · "skip this" · "go back" | Steers the background player directly; in browser mode it goes through the system's media session — the same channel your keyboard's play/pause key uses. |
| "what song is this?" | Read from the player (or the media session): track, artist, album, and which app it comes out of. |
| "like this song" · "add this to my running playlist" · "create a playlist called Late night" · "what is in my chill playlist?" | Written to your YouTube library through the official API. |
| "turn it up" · "volume 30" | Works in the background player (Jarvis drives YouTube Music's own volume slider). In browser mode YouTube exposes no volume control — use the system or app volume. |
| "queue this next" | **Not available.** There is no queue API; Jarvis offers to play it now or add it to a playlist instead. |
| "open YouTube Music" · "hide the player" | Brings the background player forward (login, cookie choice, the queue) or minimizes it again. |

Playback happens **in YouTube Music, not in Jarvis's own audio** — exactly
like the Spotify plugin, where the music comes out of Spotify. By default it
runs in a **background player**: a small window of its own that starts
minimized (a taskbar entry, no browser tab, no focus steal) and simply loads
the next song when you ask for one. Say "open YouTube Music" to bring it
forward — that is where you log in once (for your Premium, no ads) and make
YouTube's one-time cookie choice. Settings → Music can switch playback to the
system browser instead, and the browser is also the automatic fallback where
the player cannot run (no display, no desktop extras).

## Part A — the Google Cloud project (once)

If you have not set up the shared Google client yet, do
[Part A of the Google setup](google-oauth-setup.md) first. Then, in the same
project:

1. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
2. **APIs & Services → OAuth consent screen → Scopes** → add
   `https://www.googleapis.com/auth/youtube` (it is a *sensitive* scope; for
   personal use in Testing mode no verification is needed).
3. **Test users.** While the OAuth app is in *Testing* mode, Google lets ONLY
   the accounts you list as test users sign in. Open **Google Auth Platform →
   Audience → Test users → Add users** and add your own Google address (the one
   you will connect with). Without this, Connect ends on Google's page
   **"Access blocked: <app> has not completed the Google verification process
   / Error 403: access_denied"** — that is this missing entry, nothing else.
   Alternatively **publish the app** (*Audience → Publish app*, In production):
   then any account can sign in after clicking through Google's "unverified
   app" warning (Advanced → continue), and the seven-day expiry below goes
   away too.
4. That is all — the OAuth client itself is the one you already have. Nothing
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

## Part C — playback on your machine

**Background player (default).** Music plays in Jarvis's own small player
window, which needs a display and the desktop extras (pywebview — the same
`[desktop]` extra the desktop app uses). It starts minimized and keeps playing
minimized; "open YouTube Music" brings it forward. First run: YouTube shows its
cookie choice once, and you can log in there once so playback uses your account
and Premium — both are remembered in a profile of their own under
`data/music_player/`. Pause, skip, "what is playing" and volume all go through
that window directly, on every OS the window can open on. Where it cannot
(headless, Linux without a GTK/Qt WebKit backend), Jarvis falls back to the
browser and says why.

**Browser mode** (Settings → Music → Where YouTube Music plays → Browser).
Playing opens a YouTube Music tab; reading "what is playing" and pause/skip
then use the OS media session:

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
- **Two tabs (browser mode only).** Each "play" opens a new YouTube Music tab;
  Jarvis pauses the old one first, so only one plays. The background player has
  no such pile-up — it navigates one window.
- **Spotify and YouTube Music both connected?** A request that names a service
  ("on Spotify", "on YouTube Music") goes there. One that names none goes to
  the service chosen under **Settings → Music → Preferred service** — or, on
  *Automatic*, to the only connected one (Spotify if both are).

## When something does not work

| What you see | What it means |
|---|---|
| Google: "Access blocked: … has not completed the Google verification process — Error 403: access_denied" | Your account is not on the OAuth app's **Test users** list (Part A step 3), or publish the app. |
| "The YouTube Data API v3 is not enabled" | Part A step 1 was skipped in the Cloud project behind your client. |
| "YouTube asks for your cookie choice once" | First run of the background player: pick your cookie choice in the window that opened; it is remembered. |
| "The player opened the song but did not start — press play once" | The player window came forward; one click on play, then it starts on its own. |
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
