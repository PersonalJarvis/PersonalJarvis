# Google OAuth verification — removing the "unverified app" screen

Every Google Connect shows **"Google hasn't verified this app"** because the
Google Cloud project that owns Jarvis' Google OAuth client is unverified. The
screen disappears only when Google approves the project in **Google Auth
Platform → Verification Center**. Nothing in Jarvis, `gcloud` or any API can
submit or complete that review; it is a Cloud Console process owned by the
project owner.

This page is the submission kit: what Google requires, who does each step,
and draft texts ready to paste.

## Two tiers

| Plugin | Scope | Google class | Review |
|---|---|---|---|
| Google Calendar | `.../auth/calendar` | sensitive | free, days to weeks |
| YouTube Music | `.../auth/youtube` | sensitive | free |
| YouTube Studio | `.../auth/youtube.force-ssl`, `.../auth/youtube.upload`, `.../auth/yt-analytics.readonly` | sensitive | free |
| Google Cloud | `.../auth/cloud-platform.read-only`, `.../auth/bigquery` | sensitive | free |
| Gmail | `https://mail.google.com/` | restricted | free review **plus** an annual third-party security assessment (CASA), paid |
| Google Drive | `.../auth/drive` | restricted | same as Gmail |

Every Gmail read scope is restricted, so Gmail cannot avoid the assessment.
Drive could drop to the non-sensitive `drive.file` scope, but then Jarvis only
sees files it created or the user opened through it — a product change, not
a configuration change.

**Recommended path: verify the sensitive tier first.** It costs nothing and
removes the screen for Calendar, YouTube and Google Cloud. Gmail and Drive
keep the screen until a separate decision about the paid assessment. For the
submission, list only the sensitive scopes under **Data Access**; a consent
that requests an unlisted or unverified scope keeps showing the screen for
that request only.

## Checklist

| # | Step | Who | Notes |
|---|---|---|---|
| 1 | Verify ownership of `personaljarvis.ai` in Google Search Console with the same Google account that owns the Cloud project | owner (DNS TXT record at Cloudflare) | Google rejects a submission whose domains are not verified |
| 2 | Publish an app privacy policy on `personaljarvis.ai` (draft below) | owner reviews, then deploy | The current `/privacy/` page covers only the website |
| 3 | Branding: app name, logo, home page `https://personaljarvis.ai/`, privacy link, authorized domain `personaljarvis.ai` | Cloud Console | The home page must describe what the app does with Google data |
| 4 | Data Access: list exactly the scopes being verified, each with a justification (drafts below) | Cloud Console | |
| 5 | Record an unlisted YouTube demo video (script below) | owner | Must show the consent screen with the app name and the client ID in the address bar |
| 6 | Submit in Verification Center, answer Google's follow-up mails | owner | Replies go to the developer contact address |

## Draft: app privacy section (for `personaljarvis.ai/privacy/`)

> ### The Personal Jarvis app and your Google data
>
> The Personal Jarvis desktop app can connect to your Google account (Gmail,
> Google Drive, Google Calendar, YouTube and Google Cloud) only when you click
> Connect and approve access on Google's own sign-in page.
>
> **What the app accesses.** Only the Google services you connect, and only
> to carry out what you ask Jarvis to do — for example reading your calendar
> to answer "what is on today", or creating a YouTube playlist you asked for.
>
> **Where the data goes.** The app runs on your own computer. Access and
> refresh tokens are stored in your operating system's credential store on
> that computer. Google data is fetched directly from Google's APIs by the app
> on your computer; we (the developer) never receive, store or see it. When
> you ask Jarvis a question about your data, the relevant excerpt is sent to
> the AI model provider you configured in the app, solely to answer that
> request.
>
> **What we never do.** We do not sell Google user data, use it for
> advertising, transfer it to data brokers, or use it to train or improve
> generalized AI or machine-learning models.
>
> **Limited Use.** Personal Jarvis' use and transfer of information received
> from Google APIs adheres to the
> [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
> including the Limited Use requirements.
>
> **Revoking access.** Disconnect the plugin in Jarvis, or remove Personal
> Jarvis at <https://myaccount.google.com/connections>. Disconnecting deletes
> the stored tokens from your computer.

## Draft: scope justifications

- **`calendar`** — Jarvis answers spoken and typed questions about the user's
  schedule across all of their calendars and creates or changes events on
  request. Read-only would prevent creating events the user dictates.
- **`youtube`** — YouTube Music integration: search, the user's playlists and
  likes, and adding songs to playlists on request.
- **`youtube.force-ssl`, `youtube.upload`** — YouTube Studio integration:
  the channel owner asks Jarvis to upload a video (private by default) and to
  edit titles and descriptions.
- **`yt-analytics.readonly`** — reports channel statistics the owner asks
  for; never modified.
- **`cloud-platform.read-only`** — lists the user's Cloud projects, services
  and spend for questions the user asks; no write access.
- **`bigquery`** — runs the user's own billing-export queries; the user sets
  a byte limit per query.

## Draft: demo video script (2–3 minutes, unlisted)

1. Show `personaljarvis.ai` (home page and privacy policy).
2. Open the Personal Jarvis app → Plugins → Google Calendar → **Connect**.
3. In the browser, zoom into the address bar so the OAuth `client_id` is
   readable; show the account chooser and the consent screen with the app
   name and every requested scope.
4. Approve; show the plugin as Connected in the app.
5. For each scope, perform one real action and show its result: ask for
   today's events; create a test event; search YouTube Music and add a song
   to a playlist; upload a private test video in YouTube Studio; ask for
   channel statistics; list Cloud projects.
6. Disconnect the plugin and show that the stored connection is gone.

## After approval

Grants issued before approval keep working; no reconnect is needed for the
screen to disappear on the next Connect. Re-verification is needed when
scopes are added or the app name, logo or domains change.

See also [`google-oauth-setup.md`](google-oauth-setup.md).
