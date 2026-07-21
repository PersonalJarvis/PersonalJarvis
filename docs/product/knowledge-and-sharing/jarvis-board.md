---
title: "Jarvis Board"
slug: jarvis-board
summary: "Explore personal activity, achievements, and optional sharing, including what data leaves the device."
section: "Knowledge and sharing"
section_order: 4
order: 3
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [board, activity, achievements, sharing, privacy]
related: [profile-and-contacts, socials-and-feedback, privacy-and-local-data]
---

Jarvis Board gives you a local overview of recorded voice activity, tool use,
and streaks. The current desktop view is a statistics dashboard with an image
exporter. Several other Board services exist, but they do not yet have a
complete end-user interface.

Opening or refreshing **Board** does not publish anything. Some separate Board
services can contact a configured brain provider or a self-hosted sharing
service, as explained below.

> [!info] Jarvis Board is experimental. The current view shows local statistics
> and creates shareable images. Achievement history, the AI profile, personal
> records, detailed tool charts, pairing, and a shared feed are not displayed.

## Read the Board

1. Open **Board** from the app navigation. The visible values load from the
   Jarvis server running on your device.
2. Wait for the loading indicators to clear. Jarvis checks for newer recorded
   activity automatically while the view remains open.
3. Select **Refresh** to rebuild the local summaries immediately. The button
   stops spinning when the rebuild finishes.

| What you see | What it means | Important limit |
|---|---|---|
| **You said** and **Jarvis said** | All-time word totals from recorded voice turns | Words are counted by spaces. Typed chats are not a reliable source for these totals. |
| **Day streak** | Consecutive days with recorded activity, plus the longest streak | If today is still quiet, the streak that ended yesterday remains visible. |
| **Active time** | Estimated time from recorded voice sessions and bounded conversation event spans | It is not microphone-on time, focused work time, or time saved. |
| **Words over time** | Daily word totals for you and Jarvis over the latest 45 days | A blank day means no supported recorded turn was found. |
| **What you use Jarvis for** | Recorded tool calls grouped into six broad purposes across all available history | It counts tool calls, not whether each action produced a useful result. |
| **Activity** | A 26-week calendar of supported conversations, tools, tasks, and app events | Brighter cells mean more recorded events relative to the busiest day shown. They are not a quality score. |

A short line below the main totals also shows average user words per recorded
conversation, the ratio between Jarvis words and user words, and the first day
in the local summary store.

The chart and calendar are primarily visual. They do not currently provide a
keyboard-readable list of every point. Use `jarvis board summary` or
`jarvis board heatmap` when you need the same information as structured text.

These values are estimates, not a transcript or productivity rating. Missing
session data, a disabled recorder, unsupported activity, or a failed local
aggregation can leave gaps.

### Understand achievements

Jarvis evaluates a fixed local achievement catalog when selected tools, tasks,
Model Context Protocol connections, and Jarvis-Agent runs succeed. Each unlock
is saved once in the local Board database. If the app is connected to its local
event stream, a new unlock also appears as an **Achievement:** notification.

The current Board view does not show the achievement grid or earlier unlocks.
Run `jarvis board achievements` while Jarvis is running to see locked and
unlocked entries, including saved unlock times and evidence.

## Share a Snapshot

The **Share** action creates a square Portable Network Graphics (PNG) image in
the app. The card contains your all-time voice-word totals, conversation count,
active-time estimate, longest streak, the project link, and an optional X
handle.

1. Open **Board** and wait for the totals to load.
2. Select **Share** and review the preview. Clear **Your X handle (optional)**
   if you do not want the handle on the card.
3. Select **Copy Image** to copy the PNG. If image copying is unavailable,
   Jarvis saves the file instead and tells you what happened.
4. Select **Save as PNG** when you want a local file.
5. Select **Share on X** only when you want to contact that service. A capable
   device opens its share sheet with the image. The desktop fallback copies the
   image and opens a prepared composer so you can paste it yourself.
6. Review the final post before publishing it.

The image is rendered on your device. The optional handle stays in the app's
local browser storage until you clear the field. Card labels follow the chosen
app language, but the prepared X post text is currently English.

## Current Limits

Some Board code is reachable without being visible in the current Board view.
This distinction matters when you compare the app with older screenshots or
development notes.

| Part | What works now | What is missing from Board |
|---|---|---|
| Achievements | Local evaluation, one-time persistence, notifications, API, and `jarvis board achievements` | The achievement grid and history view |
| AI profile | Local history, scheduled generation, API, and the `jarvis board bio` commands | The profile card and feedback buttons; generated text currently uses a German prompt regardless of the app language |
| Records and tools | Local records and detailed tool endpoints, plus `jarvis board records` | Personal-record cards and the detailed per-tool chart |
| Federation | A separate self-hosted backend, signed proxy routes, and unfinished interface components exist | Supported setup, automatic sync startup, pairing, friends, feed, stories, reactions, and a durable disconnect control |

Federation means exchanging selected Board data through a separate service.
It is disabled by default. More importantly, the standard Jarvis server does
not currently start the automatic Board sync client. Changing the federation
setting alone therefore does not create a working shared Board.

The federation proxy routes are mounted for development and return an error
when their optional backend package or backend address is unavailable. Treat
them as an incomplete developer surface, not as a supported sharing workflow.

## Local Data and Network Behavior

| Action or service | Data used | Can it leave your device? |
|---|---|---|
| Open or refresh **Board** | Daily counts, dates, word totals, session totals, tool categories, and streaks from the local Board database | No external request is required. The app calls only its local Jarvis API. |
| Preview, copy, or save a share card | The statistics shown on the card and the optional handle | No, unless you paste, attach, or upload the result elsewhere. |
| Select **Share on X** | The visible card, prepared post text, and project link | Yes. The action opens the device share sheet or X composer. |
| Generate an AI profile | Aggregate Board facts and, when available, summary data from awareness, missions, self-modification history, earlier bios, and feedback | Possibly. A cloud brain provider receives the generated facts prompt; a local provider keeps the call local. |
| Use the unfinished federation routes or start the sync client manually | A signed, filtered federation payload and a public identity | Yes. The configured self-hosted service receives the request. |

AI profile generation runs separately from the visible dashboard. It can be
triggered after the first eligible activity history, on its weekly schedule,
or after a mastery achievement. If no suitable brain provider is available,
the old bio remains and the statistics dashboard continues to work.

The local Board database stores aggregates, achievement evidence, and generated
bios. It does not copy raw voice-turn text into daily statistics. The source
session database still contains the recorded turns according to its own
retention settings.

If a developer starts federation sync, its filter permits daily dates, task
counts, successful tool names, unique-tool counts, voice-command counts,
first-try rates, estimated time saved, achievement identifiers and tiers, a
display name, and an optional generated bio. It excludes raw transcripts,
contacts, Profile fields, mission requests, and output files.

Federation uses a public key as its identity. A manually started sync client
with no display name falls back to the device hostname, so it should not be
treated as anonymous. Its private signing key is stored through the system
credential backend when that backend is available; otherwise the key may last
only for the current process.

## Use Board from the Command Line

The curated `jarvis board` commands call the same local API as the app. Jarvis
must be running and reachable.

- `jarvis board summary` shows totals and streaks.
- `jarvis board heatmap` returns the daily activity cells.
- `jarvis board records` shows personal records that are hidden in the view.
- `jarvis board achievements` shows locked and unlocked achievements.
- `jarvis board bio` shows the latest generated AI profile.
- `jarvis board bio-regenerate` asks the configured brain provider for a new
  profile and can therefore send the facts prompt outside your device.

The mounted Board API also includes category totals, a detailed tool histogram,
manual refresh, achievements, and AI profile operations. Share-image generation
is part of the desktop interface and has no curated Board CLI command.

## How It Fits Together

1. **Recorded voice sessions and app events supply the data.** Jarvis keeps the
   source history in its normal local stores.
2. **The Board creates smaller daily summaries.** It counts words, sessions,
   activity, tool categories, task outcomes, and streaks in a local database.
   Raw voice-turn text is not copied into those daily rows.
3. **The desktop view reads the local summaries.** Automatic polling and
   **Refresh** use mounted local API routes. A failed refresh leaves previously
   saved rows intact.
4. **Achievements and AI profiles run beside the dashboard.** Achievements
   react to selected successful events. AI profile generation can use a brain
   provider, but neither history surface is mounted in the current Board view.
5. **Files and personal context remain separate.** A Jarvis-Agent deliverable
   stays in [Outputs and Files](outputs-and-files). Contact records and Profile
   fields stay in [Profile and Contacts](profile-and-contacts).
6. **Sharing requires a deliberate action.** The PNG leaves only when you send
   it. Federation remains an incomplete developer feature and does not start
   automatically with the current server.

## Check That It Works

1. Complete and end a short voice conversation with several ordinary words.
2. Open **Board** and select **Refresh**.
3. Confirm that **You said** or **Jarvis said** increases and that today's
   activity cell is no longer empty.
4. Open **Share**, confirm that the preview contains only the information you
   expect, then close the dialog.

Success means the local statistics update and the preview opens without
publishing anything or requiring a federation service.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Could not load stats** | The local Board store did not finish starting | Wait for Jarvis startup to finish, select **Refresh**, then use the main troubleshooting guide if the message remains. |
| Totals stay at zero or recent words are missing | No supported voice turn was recorded, the session is still open, or the recorder is unavailable | End the voice conversation, check that it appears in the Sessions view, then refresh Board. |
| An achievement notification appeared but no history is visible | The evaluator works, but its grid is not mounted in Board | Run `jarvis board achievements` to inspect the saved list. |
| **Copy Image** saves a file instead | The browser cannot copy PNG images directly | Use the saved PNG and attach it in the destination you choose. |
| **Share on X** does not open a composer | The app shell or browser blocked the new window | Save the PNG, open the service yourself, and review the post before attaching it. |

## Next Steps

- Read [Privacy and Local Data](privacy-and-local-data) to understand the local
  stores behind conversations, activity summaries, and generated files.
- Use [Outputs and Files](outputs-and-files) to review actual Jarvis-Agent
  deliverables instead of aggregate Board activity.
- Review [Profile and Contacts](profile-and-contacts) to manage personal context
  that remains separate from Board statistics.
- Open [Social Links and Feedback](socials-and-feedback) to manage public links
  or send feedback without attaching Board data.
