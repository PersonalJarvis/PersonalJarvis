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
last_reviewed: 2026-07-15
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
internet connection once the app is running. Online documentation, community
links, and feedback use your browser and therefore need a network connection.

## Browse the Local Library

1. Open **Docs** from the app sidebar. On the first visit, **Preparing your
   documentation** can appear while Jarvis builds the local search index.
2. Start with one of the featured guides, or use **Browse by Topic** to enter a
   reader journey such as everyday use, configuration, safety, or reference.
3. Expand or collapse a section in the documentation sidebar, then select a
   guide by title.
4. Select **Documentation** above the filter, or the **Documentation** item in a
   page breadcrumb, to return to the overview.

The sidebar remembers a short list of recently opened guides on this device.
That list appears when the filter is empty and helps you return to a page
without searching again.

## Choose the Right Search

The documentation has two search tools. They look similar but answer different
questions.

| Tool | Best for | What it checks |
|---|---|---|
| Sidebar **Filter title/tag...** | Finding a guide when you know the feature or topic | Guide titles, summaries, and topic tags |
| **Search docs** | Finding a term, visible error, setting, or explanation inside a guide | Titles, headings, tags, and complete guide text |
| **Browse by Topic** | Learning in a sensible order | Reader journeys and their ordered guides |
| **On this page** | Jumping within the guide you already opened | Level-two and level-three section headings |

To search all guide text:

1. Select the magnifying-glass button beside **Documentation**. You can also
   press **Ctrl+K** on Windows or Linux, or **Command+K** on macOS, while Docs is
   open.
2. Enter one to three distinctive words. A feature name such as `wake word` is
   usually more useful than a complete sentence.
3. Review the guide title, section, and highlighted excerpt in each result.
4. Use the arrow keys to move through results and **Enter** to open one, or
   select a result normally. Press **Escape** to close search.

Search runs against the local library. It does not search your chats, Wiki,
contacts, output files, the public website, or community posts.

## Move Between Related Guides

Each guide is designed as part of a path rather than as an isolated page.

- Use **On this page** on a wide window to jump to a section. On a smaller
  window, scroll through the same headings in the main guide.
- Follow links in **How It Fits Together** when an adjacent feature supplies
  input, receives a result, or controls a permission.
- Use **Related Guides** near the end of a page for the closest follow-on
  topics.
- Use **Previous** and **Next** at the bottom to continue through the complete
  reader journey.
- Check **Last reviewed** to see when the guide was last compared with the
  product.

Internal guide links stay inside Docs. The **Open redesigned online docs**
button on the overview opens the public documentation in your normal browser.
The online library can be useful from another device, but it may describe a
newer release than the version installed on your computer.

## Move From Guidance to Support

Start with the guide for the feature you are using. Its **Troubleshooting**
table covers the most likely visible symptoms and safe first fixes. If several
areas are affected, use the main [Troubleshooting](troubleshooting) guide to
check startup, connections, providers, voice, permissions, and extensions in a
useful order.

Use the [App Command Reference](app-command-reference) when you know the action
you want but need to find how that action is exposed across voice, chat, the
desktop app, or command-line tools. It is a catalog, not a diagnosis guide.

For a product bug, idea, or question, open **Feedback** in the main app
sidebar. The current Feedback view does not submit a form or attach logs from
Jarvis. It opens the project's **#report-a-bug** forum in Discord and offers a
separate **Join Discord first** button. If Discord is not suitable, use the
project's [public issue tracker](https://github.com/PersonalJarvis/PersonalJarvis/issues).

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
   public issue tracker open outside Jarvis. The Docs index does not send your
   search terms or local data to those services.

If the internet is unavailable, the installed guides and their search can
still work. Online docs and feedback links cannot, but that does not stop chat,
voice, or other local app areas.

## Check That It Works

1. Open **Docs** and wait until the overview shows the local guide count.
2. Open full-text search and enter `permissions`.
3. Confirm that results show a title, section, and highlighted excerpt, then
   open one result.
4. Select a heading from **On this page** on a wide window, or use a related
   guide link near the bottom.

Success means the selected guide opens inside Docs and the search result leads
to the matching part of the local documentation. No provider credential is
needed for this check.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Preparing your documentation** | Jarvis is scanning the installed guides and building search for the first time | Wait a moment. The rest of the app can remain usable while this finishes. |
| **Documentation could not be loaded** | The local index did not become ready before the request ended, or its files are unavailable | Select **Try again**. You do not need to restart the app for the first retry. |
| The sidebar filter finds nothing | It checks guide metadata, not every paragraph | Clear the filter, open full-text search, and try one or two feature-specific words. |
| Full-text search shows no results | All entered words may not occur in one guide, or the local index is not ready | Shorten the query, remove generic words, and wait for the overview to finish loading before trying again. |
| A guide says **Could not load doc** | The selected page is missing from the current local index or could not be read | Select **Try again**, return to the overview, and search for the topic in another guide. |
| Online docs or Feedback does not open | The external browser handoff was blocked or the device is offline | Check the network, try the button once more, then open the public site or issue tracker directly in your browser. |
| Local and online instructions differ | The website may cover a newer release than the installed library | Prefer the local guide for the version you are running, and check its **Last reviewed** date before changing settings. |

## Next Steps

- Read [Tour the Desktop App](desktop-app-tour) to see where Docs, Feedback,
  settings, history, and outputs sit in the wider app.
- Keep [Troubleshooting](troubleshooting) nearby for safe checks when a problem
  affects startup, connections, providers, voice, permissions, or extensions.
- Use the [App Command Reference](app-command-reference) when you need the
  canonical action catalog after choosing the right feature guide.
