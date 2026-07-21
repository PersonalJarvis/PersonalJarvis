---
title: "Find Help in the App"
slug: find-help-in-the-app
summary: "Search the built-in documentation, use related-page links, and move between overview, guides, troubleshooting, and reference material."
section: "Knowledge and sharing"
section_order: 4
order: 5
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [documentation, help, search, troubleshooting, support]
related: [desktop-app-tour, troubleshooting, app-command-reference]
---

The **Docs** area gives you a searchable guide to Personal Jarvis without
requiring a separate website. Use it to learn a feature, follow a setup path,
understand how two parts work together, or find the safest first response to a
problem.

The local library is included with your installation and works without an
internet connection while your browser can reach the running Jarvis server.
Online documentation, community links, and feedback need an internet
connection. The Docs controls follow your selected interface language, but the
guide pages are currently written in English.

## Browse the Local Library

1. Open **Docs** from the app sidebar. On the first visit, **Preparing your
   documentation** can appear while Jarvis builds the local search index.
2. Start with one of the featured guides, or choose a card under **Browse by
   Topic**. A topic card opens the first guide in that ordered section.
3. Expand or collapse a section in the documentation sidebar, then select a
   guide by title. With a keyboard, use **Tab** to reach controls and **Enter**
   or **Space** to activate buttons.
4. Select **Documentation** above the filter, or the **Documentation** item in a
   page breadcrumb, to return to the overview.

The sidebar keeps up to five recently opened guides in the current browser
profile. The list appears when the filter is empty. It is not stored in your
Jarvis account or sent to a documentation service.

Keyboard users can also use the **Skip to documentation content** link. Press
**Tab** after opening Docs to reveal it, then press **Enter**.

## Choose the Right Search

The documentation offers several ways to find a guide or section.

| Tool | Best for | What it checks |
|---|---|---|
| Sidebar **Filter title/tag…** | Finding a guide when you know the feature or topic | Guide titles, summaries, slugs, and topic tags |
| **Search docs** | Finding a term, visible error, setting, or explanation inside a guide | Titles, headings, tags, and complete guide text |
| **Browse by Topic** | Starting with a subject area | The first guide in each ordered documentation section |
| **On this page** | Jumping within the open guide on a wide window | Level-two and level-three section headings |

To search all guide text:

1. Select the **Full-text search** button beside **Documentation**. It has a
   magnifying-glass icon. You can also press **Ctrl+K** on Windows or Linux, or
   **Command+K** on macOS, while Docs is open.
2. Enter one to three distinctive words. A feature name such as `wake word` is
   usually more useful than a complete sentence. Every entered word must match
   the same guide.
3. Review the guide title, section, and excerpt in each result. Matching words
   in the excerpt are highlighted.
4. Use the arrow keys to move through results and **Enter** to open one, or
   select a result normally. Press **Escape** to close search.

Search returns up to 20 results from the local library. It opens a selected
guide at the top, not at the matching sentence. It does not search your chats,
Wiki, contacts, output files, the public website, or community posts.

## Move Between Related Guides

Each guide is designed as part of a path rather than as an isolated page.

- Use **On this page** on a wide window to jump to a section. On a smaller
  window, scroll through the same headings in the main guide.
- Follow links in **How It Fits Together** when an adjacent feature supplies
  input, receives a result, or controls a permission.
- Use **Related Guides** near the end of a page for the closest follow-on
  topics.
- Use **Previous** and **Next** at the bottom to open adjacent guides in the
  library order.
- Check **Last reviewed** to see when the guide was last compared with the
  product.

Links to guides in the installed index stay inside Docs. The **Open redesigned
online docs** button on the overview opens the public documentation in your
browser. The online library can be useful from another device, but it may not
match the version installed on your computer.

## Move From Guidance to Support

Start with the guide for the feature you are using. Its **Troubleshooting**
table covers the most likely visible symptoms and safe first fixes. If several
areas are affected, use the main [Troubleshooting](troubleshooting) guide to
check startup, connections, providers, voice, permissions, and extensions in a
useful order.

Use the [App Command Reference](app-command-reference) when you know the action
you want but need to find how that action is exposed across voice, chat, the
desktop app, or command-line tools. It is a catalog, not a diagnosis guide.

There is no separate **Support** or **Diagnostics** view in the sidebar. The
diagnostic API is a developer tool; Docs and Feedback do not run it or collect
its output automatically.

For a product bug, idea, or question, open **Feedback** in the main app
sidebar. The current Feedback view does not submit an in-app form, run
diagnostics, or attach logs. It opens the project's **#report-a-bug** forum in
Discord and offers a separate **Join Discord first** button. If Discord is not
suitable, use the project's [public issue tracker](https://github.com/PersonalJarvis/PersonalJarvis/issues).

Before posting anywhere public, describe what you expected, what happened,
and the shortest steps that reproduce it. Remove credentials, recovery codes,
private conversation text, contact details, personal file paths, and unrelated
screens from anything you share.

> [!warning] Never paste an API key, token, password, or recovery code into a
> guide search, chat message, feedback post, screenshot, or public issue.

## How It Fits Together

1. **Docs starts with the installed reader library.** Jarvis reads the guides
   shipped with your version and builds a local index for navigation and
   full-text search.
2. **A guide leads to the relevant feature.** Setup and how-to pages explain
   the visible controls; relationship links show which settings, providers,
   permissions, histories, or outputs affect the same task.
3. **Troubleshooting narrows a failure.** A feature page handles common local
   symptoms first. The main troubleshooting guide connects problems that span
   several parts of the app.
4. **Reference pages provide exact catalogs.** Use them after you understand
   the goal and need a command, setting, or supported action without another
   tutorial.
5. **External help is a deliberate handoff.** Online docs, Discord, and the
   public issue tracker open outside Jarvis. The local Docs search does not send
   your query or guide data to those services.

On a headless installation, open the Jarvis web interface in your own browser.
The server has no desktop window and cannot open a browser for you, so external
links fall back to a new tab in that browser. If the internet is unavailable,
the installed guides and search can still work, but online docs and Feedback
cannot. Chat and voice availability then depends on your configured providers
and local capabilities.

## Check That It Works

1. Open **Docs** and wait until the overview shows the local guide count.
2. Open full-text search and enter `permissions`.
3. Confirm that results show a title, section, and excerpt, then open one
   result.
4. Select a heading from **On this page** on a wide window, or use a related
   guide link near the bottom.

Success means the selected guide opens inside Docs. Scroll or use **On this
page** to reach the relevant section. No provider credential is needed for
this check.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Preparing your documentation** | Jarvis is scanning the installed guides and building search for the first time | Wait a moment. The rest of the app can remain usable while this finishes. |
| **Documentation could not be loaded** or **Could not load doc.** | The local index or selected file was unavailable, or the request timed out | Select **Try again**. This repeats the request; it is not a manual re-index command. Return to the overview if one guide still fails. |
| The sidebar filter finds nothing | It checks guide metadata, not every paragraph | Clear the filter, open full-text search, and try one or two feature-specific words. |
| Full-text search shows no results | All entered words may not occur in one guide, or the local index is not ready | Shorten the query, remove generic words, and wait for the overview to finish loading before trying again. |
| Online docs or Feedback does not open | The external browser handoff was blocked or the device is offline | Check the network, try the button once more, then open the public site or issue tracker directly in your browser. |

## Next Steps

- Read [Tour the Desktop App](desktop-app-tour) to see where Docs, Feedback,
  settings, history, and outputs sit in the wider app.
- Keep [Troubleshooting](troubleshooting) nearby for safe checks when a problem
  affects startup, connections, providers, voice, permissions, or extensions.
- Use the [App Command Reference](app-command-reference) when you need the
  canonical action catalog after choosing the right feature guide.
