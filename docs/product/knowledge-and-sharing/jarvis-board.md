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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [board, activity, achievements, sharing, privacy]
related: [profile-and-contacts, socials-and-feedback, privacy-and-local-data]
---

Jarvis Board turns recorded activity into a personal overview. It helps you
understand how often you use Jarvis, how much you and Jarvis speak, which kinds
of tools are involved, and how your activity changes over time.

The personal dashboard works locally. Sharing is a separate, deliberate
action: nothing on the Board is published merely because you open or refresh
the view.

> [!info] Jarvis Board is experimental. The current Board view shows local
> activity and creates shareable statistic images. Achievement history,
> pairing, and a shared activity feed are not currently exposed in this view,
> even though parts of those systems already exist behind it.

## Read the Board

Open **Board** from the app navigation. Jarvis builds the view from recorded
session and activity data, then refreshes the visible totals in the background.
Use **Refresh** when you want to request an immediate rebuild.

| What you see | What it means | Important limit |
|---|---|---|
| **You said** and **Jarvis said** | Approximate word totals from recorded voice turns | Words are counted by spacing; typed chats are not guaranteed to appear in these totals. |
| **Day streak** | Consecutive days with recorded activity, plus your longest run | A quiet current day does not immediately erase a streak that was active yesterday. |
| **Active time** | Time covered by recorded conversations and bounded activity spans | It is an estimate, not working time saved or microphone-on time. |
| **Words over time** | Daily voice-word totals for you and Jarvis | Empty days can mean no recorded voice turns, not that Jarvis was never opened. |
| **What you use Jarvis for** | Supported tool calls grouped into broad purposes | It summarizes categories, not the content or outcome of every action. |
| **Activity** | A 365-day calendar of recorded conversations, tools, tasks, and other supported events | A brighter day means more recorded activity; it is not a quality score. |

These numbers are best used as orientation. They are derived summaries rather
than an exact transcript, stopwatch, or productivity rating. A crash, disabled
recording source, old data retention, or an unsupported action can leave a gap.

### Understand achievements

Jarvis also watches selected successful actions, tool use, completed tasks,
Jarvis-Agent work, and long-term activity for achievements. A newly unlocked
achievement can appear as a notification, and the unlock is saved locally only
once.

The current Board screen does not provide the existing achievement list or a
place to reopen earlier unlocks. This is a real limitation of the experimental
surface, not evidence that an unlock was lost. Achievement evidence can include
local counts, tool names, or an identifier for the triggering run; that detail
is not added to the normal share image.

## Share a Snapshot

The **Share** action creates a square Portable Network Graphics (PNG) image in
the app. Its card contains the current voice-word totals, conversation count,
active-time estimate, longest streak, a project link, and an optional social
handle that you enter.

1. Open **Board** and wait for the totals to load.
2. Select **Share** and review the preview. Remove the optional handle if you do
   not want it on the image.
3. Choose **Copy Image** to place the PNG on your clipboard, or **Save as PNG**
   to keep it as a file.
4. Choose **Share on X** only when you intend to send the card to that service.
   A supported device can open its share sheet. On desktop, Jarvis may copy the
   image and open a prepared composer so that you can paste it yourself.
5. Review the final post before publishing it. Jarvis does not control the copy
   retained by a service after you submit it.

Image generation happens in the app rather than on a Jarvis server. The
optional handle is kept in local browser storage for later share cards. Saving
the image creates a normal local output or download; copying it leaves the next
destination up to you.

## Local and Optional Shared Data

The personal Board and the experimental federation are different features.
Federation means connecting a separate, self-hosted sharing service so approved
summary data can be exchanged with paired people. It is disabled by default,
and the current Board view does not offer reader-ready setup, pairing, feed, or
disconnect controls.

| Data path | Stays local | Can leave the device |
|---|---|---|
| Personal Board | Daily totals, word counts, session counts, categories, records, achievement evidence | Nothing merely from viewing or refreshing the Board |
| Share image | The generated PNG and optional handle until you choose a destination | The visible card when you copy it into another app or select a sharing service |
| Experimental federation | Raw transcripts, contact details, Profile fields, run requests, and output files | Approved daily counts, dates, successful tool names, achievement identifiers and tiers, a configured display name, and an optional generated Board bio |

The federation filter deliberately excludes raw conversation text and unknown
fields. Even aggregate data can reveal habits, dates, or the kinds of tools you
use, so connect a sharing service only after you understand and accept that
smaller boundary.

If a sharing backend is unavailable, the local dashboard continues to work and
keeps its saved summaries. An experimental sync attempt can fail and try again
later; it must not block conversations, Jarvis-Agents, or local Board refreshes.

## How It Fits Together

1. **Chats, voice, tools, tasks, and Jarvis-Agents create activity.** Jarvis
   records supported events and voice-session details as part of their normal
   history.
2. **The Board aggregates locally.** It counts words, sessions, activity,
   tool categories, task outcomes, and streaks into a smaller personal data
   store. It does not copy raw transcript text into the Board totals.
3. **Achievements react to selected successes.** They can use Jarvis-Agent and
   tool events, then save an unlock and show a notification. [Jarvis-Agents](jarvis-agents)
   remains the place to watch mission progress and recovery.
4. **Outputs keeps files, while Board keeps summaries.** A mission file remains
   in [Outputs and Files](outputs-and-files); Board never previews or publishes
   that file. A PNG you save from **Share** becomes a separate local file.
5. **Profile and Contacts stay separate.** [Profile and Contacts](profile-and-contacts)
   can personalize conversations and supported actions, but the current Board
   does not place contact records or Profile fields on its dashboard or share
   card.
6. **Social links and feedback stay separate.** [Social Links and Feedback](socials-and-feedback)
   manages public links and app feedback. It does not automatically post Board
   statistics, and Board does not attach feedback reports to a share card.
7. **Offline behavior fails softly.** Local aggregation and saved summaries do
   not require a federation service. If a local source is unavailable, Jarvis
   serves the last usable totals or an empty state instead of stopping the rest
   of the app.

## Check That It Works

1. Complete a short voice conversation that contains several ordinary words.
2. Open **Board** and select **Refresh**.
3. Confirm that the page loads without a connection warning and that at least
   one relevant total or today's activity reflects the recorded conversation.
4. Open **Share**, confirm the preview contains only the statistics you expect,
   then close it without selecting a destination.

Success means the local Board loads and can prepare a preview without
publishing or requiring an external sharing service.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Could not load stats** | The local Board store or aggregator did not finish starting | Wait for app startup, select **Refresh**, and follow the main troubleshooting guide if the error remains. |
| Totals stay at zero | There is no supported recorded activity yet, or the session source is unavailable | Complete and finish a voice conversation, refresh, and compare with Sessions before assuming data was deleted. |
| Recent words are missing | The latest turn has not been recorded or aggregated, or it was a typed chat rather than a voice turn | Finish the voice session, wait briefly, and select **Refresh**. |
| An achievement notification appeared but no list is visible | The unlock system is active, but its history component is not mounted in the current Board view | Treat the notification as the visible confirmation; the app does not currently offer a Board history screen. |
| **Copy Image** saves a file instead | The browser cannot copy PNG images directly | Use the saved PNG and attach it manually in the destination you choose. |
| **Share on X** does not open a composer | The app shell or browser blocked the new window | Save the PNG, open the service yourself, and review the post before attaching the image. |

## Next Steps

- Read [Privacy and Local Data](privacy-and-local-data) to understand the local
  stores behind conversations, activity summaries, and generated files.
- Use [Outputs and Files](outputs-and-files) to review actual Jarvis-Agent
  deliverables instead of their aggregate Board activity.
- Review [Profile and Contacts](profile-and-contacts) to manage personal context
  that remains separate from Board statistics.
- Open [Social Links and Feedback](socials-and-feedback) to manage public links
  or send feedback without assuming that Board data is attached.
