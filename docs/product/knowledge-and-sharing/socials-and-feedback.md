---
title: "Social Links and Feedback"
slug: socials-and-feedback
summary: "Find official community links and share useful feedback without accidentally including private information."
section: "Knowledge and sharing"
section_order: 4
order: 4
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [socials, feedback, community, privacy]
related: [profile-and-contacts, jarvis-board, privacy-and-local-data]
---

**Socials** collects the project's public community and profile links in one
place. **Feedback** takes you to the official Discord forum where you can report
a bug, suggest an idea, or ask a question.

Both features open a service outside Jarvis. You decide whether to follow a
link and what to post; Jarvis does not automatically publish your profile,
conversation, files, or Board activity.

## Before You Start

- Connect to the internet and make sure your default browser can open external
  links.
- To post feedback, use a Discord account and join the Personal Jarvis server.
- Prepare a report that contains enough detail to reproduce the problem but no
  credentials, contact records, private conversation text, or personal paths.

> [!warning] Treat a community post and every attachment as shared with other
> people. Crop screenshots carefully, and never include API keys, passwords,
> recovery codes, private messages, or account details.

## Browse Social Links

1. Open **Socials** from the app navigation. The page shows enabled links,
   grouped by service.
2. Select a service with one link to open it directly in your browser.
3. Select a service with several links to open its detail page, then choose the
   exact destination. Select **Back** to return to the service grid.
4. Check the destination before signing in or sharing information. A link
   leaving Jarvis is governed by that service's own account and privacy rules.

The list starts with official Personal Jarvis community and project links and
is stored on your installation. It is separate from your Profile and Contacts;
opening Socials does not expose either one.

> [!note] The current Socials page is read-only. It has no **Add**, **Edit**,
> **Hide**, or **Delete** controls, even though the supported command-line
> interface can change the local list. If you administer an installation, use
> `jarvis socials --help` and the [CLI Reference](cli-reference) rather than
> editing a data file by hand.

## Send Useful Feedback

1. Open **Feedback** from the app navigation.
2. If you have not joined the community, select **Join Discord first** and
   complete Discord's onboarding.
3. Return to **Feedback** and select **Open #report-a-bug**. Jarvis asks your
   operating system or browser to open the forum outside the app.
4. Choose the appropriate forum option and write a short, descriptive title.
5. Explain what you tried, what you expected, what happened instead, and the
   smallest repeatable set of steps. Add the app version and operating system
   only when they help explain the problem.
6. Review the post and any screenshot, then submit it in Discord.

The current Feedback page does not contain a form, upload a screenshot, attach
logs, or send system details automatically. Selecting a button only opens
Discord; your report is not sent until you submit it there.

## How It Fits Together

1. **You start from the app.** The Socials and Feedback entries are neighboring
   shortcuts in the main navigation.
2. **Socials reads a local directory of public links.** Selecting a link hands
   the destination to a browser. It does not read or publish data from
   [Profile and Contacts](profile-and-contacts).
3. **Feedback opens a community destination.** Jarvis does not turn the active
   chat, voice session, or output file into a report. You choose what to copy
   into Discord.
4. **Jarvis Board remains separate.** A share card from
   [Jarvis Board](jarvis-board) is not posted through Socials or attached to
   Feedback. You must review and share it yourself.
5. **Privacy follows the destination.** Local data stays local until you paste,
   attach, or publish it. [Privacy and Local Data](privacy-and-local-data)
   explains the boundary before information leaves your device.
6. **External failures stay contained.** If a social platform, Discord, or your
   browser is unavailable, these links can fail without stopping chats, voice,
   tasks, or local files.

## Check That It Works

1. Open **Socials** and select a service that shows more than one link.
2. Confirm that its detail page lists the separate destinations and that
   **Back** returns to the grid.
3. Open **Feedback** and confirm that both **Open #report-a-bug** and **Join
   Discord first** are available.

Success means the local social directory loads, grouped links can be explored,
and Feedback presents the two Discord paths. You do not need to publish a post
to complete this check.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Socials keeps loading or shows an error | The local web service is still starting or did not answer | Wait for startup to finish, leave and reopen **Socials**, then use the main troubleshooting guide if the error remains. |
| **No social links are available** appears, but there is no add button | The saved list is empty, disabled, or unreadable; the current page cannot repair it | An administrator can inspect `jarvis socials list` and use the supported CLI to add or enable a valid `http` or `https` link. |
| A Socials tile does not open | The operating system or browser could not open the external destination | Check the default-browser setting and internet connection, then try again. The app keeps the destination visible in the link so you can also copy it into a browser. |
| Discord opens a welcome page or denies the forum | The account has not joined the server or completed onboarding | Return to **Feedback**, select **Join Discord first**, finish onboarding, then use **Open #report-a-bug** again. |
| You selected Feedback but nothing was submitted | The page only opens Discord; it does not send an in-app form | Finish and submit the post inside Discord. If Discord is unavailable, open the GitHub repository from **Socials** and use its Issues page. |

## Next Steps

- Review [Profile and Contacts](profile-and-contacts) to understand which
  personal details stay separate from the public Socials directory.
- Read [Jarvis Board](jarvis-board) before creating a statistics card or
  choosing an external sharing destination.
- Use [Privacy and Local Data](privacy-and-local-data) to decide what is safe to
  include in a community post, screenshot, or issue report.
