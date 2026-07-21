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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [socials, feedback, community, privacy]
related: [profile-and-contacts, jarvis-board, privacy-and-local-data]
---

**Socials** gives you a local directory of project and community links.
**Feedback** opens the project's Discord server, where you can report a bug,
suggest an idea, or ask a question.

Neither view posts anything for you. Jarvis opens the destination in an
external browser, and you decide whether to sign in or share information.

## Before You Start

- You need an internet connection to visit a destination. The Socials directory
  itself is stored locally and can still load while you are offline.
- On a desktop installation, make sure the operating system can open web links.
  In a browser connected to a headless server, allow the Jarvis page to open a
  new tab.
- To post feedback, use a Discord account and join the Personal Jarvis server.
- Prepare a report that contains enough detail to reproduce the problem but no
  credentials, contact records, private conversation text, or personal paths.

> [!warning] Treat a community post and every attachment as shared with other
> people. Crop screenshots carefully, and never include API keys, passwords,
> recovery codes, private messages, or account details.

## Browse Social Links

1. Open **Socials** from the app navigation. The page shows enabled links,
   grouped by platform.
2. On a fresh installation, look for Discord, GitHub, X, and Instagram. GitHub
   contains separate **GitHub (Repo)** and **GitHub (Profile)** destinations.
3. Select a platform with one link to open it in an external browser. Select a
   platform that shows several links to open its detail page first.
4. Choose the destination from the detail page. Use the arrow button labelled
   **Back** to return to the platform grid.
5. Check the address in the browser before signing in or sharing information.
   The destination's account, cookie, and privacy rules apply after it opens.

Jarvis writes the initial directory only when the local Socials store does not
exist. An existing installation keeps its saved entries, including edits and
older destinations, when project defaults change. Disabled entries are hidden.

> [!note] The current Socials page is read-only. It has no **Add**, **Edit**,
> **Hide**, or **Delete** controls, even though the supported command-line
> interface can change the local list. If you administer an installation, use
> `jarvis socials --help` and the [CLI Reference](cli-reference) rather than
> editing a data file by hand. The CLI accepts absolute `http` and `https`
> addresses; prefer `https` when it is available.

## Send Useful Feedback

1. Open **Feedback** from the app navigation.
2. If you have not joined the community, select **Join Discord first** and
   complete Discord's onboarding.
3. Return to **Feedback** and select **Open #report-a-bug**. Jarvis asks the
   operating system or your current browser to open the forum outside the app.
4. Sign in if Discord asks, then choose the appropriate forum option and write
   a short, descriptive title.
5. Explain what you tried, what you expected, what happened instead, and the
   smallest repeatable set of steps. Add the app version and operating system
   only when they help explain the problem.
6. Review the post and any screenshot, then submit it in Discord.

The current Feedback view has no report form. Its two buttons pass only the
chosen Discord address to Jarvis's external-link opener. The view does not send
your report text, screenshots, logs, conversations, or system details, and it
does not queue a report while you are offline. Discord receives what your
browser normally sends when it loads the site and anything you submit there.

## How It Fits Together

1. **Socials reads its local directory.** This does not require a brain provider
   or an account on any listed platform.
2. **You choose a destination.** Jarvis passes that web address to its local
   external-link opener. It does not add Profile, Contacts, chat, or Board data
   to the link.
3. **The browser takes over.** A desktop host tries an installed browser. A
   headless host reports that it cannot open one, so the Jarvis page tries a new
   tab in the browser you are already using. Browser popup rules can block that
   fallback.
4. **Feedback follows the same opening path.** Jarvis does not turn the active
   chat, a voice session, or an output file into a Discord report.
5. **Sharing remains separate.** A card from [Jarvis Board](jarvis-board) is not
   attached to Feedback. Review anything you paste or upload, using
   [Privacy and Local Data](privacy-and-local-data) as a guide.
6. **A failed external link does not stop Jarvis.** Chats, voice, tasks, and
   local files continue to work if a platform or browser is unavailable.

## Check That It Works

1. Open **Socials**. On an unchanged fresh install, select GitHub and confirm
   that **GitHub (Repo)** and **GitHub (Profile)** appear.
2. Use **Back**, then select any single-link platform. Confirm that an external
   browser or browser tab opens the selected site.
3. Return to Jarvis, open **Feedback**, and confirm that **Open #report-a-bug**
   and **Join Discord first** are both present.

You do not need to sign in or publish a post to complete this check. If your
saved Socials directory differs from the fresh-install list, use any group with
two or more links for the detail-page check.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Socials keeps loading or shows an error | The local web service is still starting or did not answer | Wait for startup to finish, leave and reopen **Socials**, then use the main troubleshooting guide if the error remains. |
| **No social links are available** appears, but there is no add button | The saved list is empty, all entries are disabled, or the store could not be read | Run `jarvis socials list`. Use the supported CLI to add an entry or enable one; the read-only page cannot repair the list. |
| A saved destination is old | The initial list was written on first use and is not replaced when defaults change | Compare the entry with the current project destination, then update it with `jarvis socials edit` if you administer the installation. |
| A Socials tile or Feedback button does not open anything | The host has no working browser, a popup was blocked, or the destination is offline | Check the internet connection and desktop browser settings. On a headless installation, allow popups for the Jarvis page and try again. |
| Discord opens a welcome page or denies the forum | The account has not joined the server or completed onboarding | Return to **Feedback**, select **Join Discord first**, finish onboarding, then use **Open #report-a-bug** again. |
| You selected Feedback but nothing was submitted | The page only opens Discord; it does not send an in-app form | Finish and submit the post inside Discord. If Discord is unavailable, use the project's [GitHub Issues](https://github.com/PersonalJarvis/PersonalJarvis/issues) page. |

## Next Steps

- Review [Profile and Contacts](profile-and-contacts) to understand which
  personal details stay separate from the public Socials directory.
- Read [Jarvis Board](jarvis-board) before creating a statistics card or
  choosing an external sharing destination.
- Use [Privacy and Local Data](privacy-and-local-data) to decide what is safe to
  include in a community post, screenshot, or issue report.
