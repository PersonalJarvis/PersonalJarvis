---
title: "Screen Context"
slug: screen-context
summary: "Let your assistant inspect one requested screen or window with visible capture, privacy filtering, and short-lived memory."
section: "Extend and automate"
section_order: 5
order: 9
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [screen-context, vision, privacy, accessibility, ocr, desktop]
related: [computer-use, permissions, providers-and-api-keys, cli-reference]
---

Screen Context lets your assistant inspect one screenshot when you explicitly
ask. It never monitors continuously. The app uses your configured assistant
name; this guide says “your assistant” so it fits every chosen name.

## Before You Start

- Connect a provider that can inspect images.
- Open **Settings > Permissions** and grant the operating-system permissions
  listed for your platform.
- Move private information away or add a privacy rule before testing. Redaction
  cannot recognize every secret in arbitrary pixels.

> [!warning]
> For password managers, banking, or private browsing, add the app or window
> title to the denylist so Screen Context refuses the entire capture.

## Ask for One Look

Make the request explicit: “Look at this error on my screen” or “What does this
dialog say?”

A screen request captures the monitor under the pointer at trigger time. If the
pointer is unreadable, it uses the bar's monitor, then the primary monitor.

A request naming the current window, tab, dialog, app, or document prefers the
focused window. If it cannot be measured, the receipt reports a monitor
fallback. Available app and window facts accompany either capture.

“What is that?” is ambiguous. Your assistant asks first and captures nothing
until you confirm. A refusal or unrelated reply closes that short confirmation.

A visible border precedes the shutter; a completion notice reports size and
redactions. If the indicator fails, the receipt records that limitation.

## Understand What It Can Do

Screen Context is read-only. A captured look turn receives no tools, and text
inside the image is untrusted evidence, never an instruction.

[Computer Use](computer-use) separately clicks, types, scrolls, or opens apps.
Inspect first, verify the answer, then make a clear action request. A capture
never authorizes a click or keystroke.

Screen Context reads text in this order:

1. The accessibility layer supplies exact visible labels and values when
   available; password fields are excluded.
2. Optional OCR runs only when that text is sparse, you enabled it, and a local
   OCR engine is installed.
3. Without either, the image remains usable and the receipt names the limit.

OCR is slower and less exact, so it only fills a real gap.

## Configure Privacy and Retention

In **Settings > Screen Context**, the status line shows readiness and monitors.

1. Use the main switch to enable or disable explicit screen looks.
2. Under **Never capture these apps or window titles**, add one app-name or
   title fragment per line. Matching is case-insensitive and blocks before
   capture. A monitor look also refuses when its visible windows cannot be
   checked safely against this list.
3. Keep built-in patterns enabled for common cards, IBANs, keys, authorization
   headers, password assignments, and private-key headers.
4. Add custom `label:regular expression` rules one per line. Broad expressions
   can hide ordinary text too.
5. Enable OCR only with a working local engine when accessibility text is sparse.
6. Set retention from 1 to 600 seconds, then choose **Save privacy rules**.

Sensitive accessibility regions are blacked out before encoding. Matching text
becomes a marker such as `[redacted:card]`; the receipt reports it.

Pixels and extracted text stay in memory until the capture is consumed or
expires. To interpret the screen, the selected model receives the filtered
image, extracted accessibility or OCR text, and relevant active-app and window
facts. A remote vision provider therefore receives that filtered context; a
capable local provider keeps the model step on hardware you control. An API
capture creates a once-only handle that otherwise expires. Screen Context does
not write captures to disk. **Discard held captures now** clears all handles;
changing settings also clears captures made under old rules.

## Platform and Permission Support

| Platform | Screen image | Accessibility text |
|---|---|---|
| Windows desktop | Supported without a separate screen-recording grant | Supported through Windows UI Automation |
| macOS desktop | Requires **Screen Recording** permission | Requires **Accessibility** permission; the image can still work without it |
| Linux with X11 | Supported when the desktop capture dependency is available | Uses an active AT-SPI session |
| Linux with Wayland | Unavailable until a supported desktop-portal capture backend is installed | Does not make global capture available |
| Headless server | Unavailable because there is no addressable display | Not applicable |

Missing support produces a refusal, not a blank screenshot. Reopen the macOS
app if the system requests it after a permission change.

## How It Fits Together

| Related feature | Relationship to Screen Context |
|---|---|
| [Computer Use](computer-use) | Screen Context reads once without action tools. Computer Use performs desktop actions separately. |
| [Permissions](permissions) | Shows and requests macOS Screen Recording and Accessibility access; Screen Context checks the current state on every capture. |
| [Providers and API Keys](providers-and-api-keys) | A vision-capable provider interprets the filtered image. |
| [CLI Reference](cli-reference) | The same authenticated local API is available through the `screen-context` group: check readiness, classify wording without capture, request one capture, consume or discard it, and manage settings. |

A CLI or API capture is itself explicit, but still obeys permissions, privacy
rules, the announcement, single use, and expiry.

## Check That It Works

1. Open a harmless window containing a short, non-sensitive message and place
   the pointer on its monitor.
2. Ask, “What does this window say?”
3. Confirm that the indicator appears before an answer about the focused window.
4. Add part of that window title to the denylist, save, and ask again. A refusal
   naming the privacy rule confirms that the policy runs before capture.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Your assistant asks whether it should look | The wording was ambiguous | Confirm on the same conversation, or repeat with “screen” or “this window” |
| The wrong monitor was captured | The pointer was on another monitor when the request triggered | Place the pointer on the intended monitor and ask again, or name the focused window |
| Capture is unavailable on macOS | Screen Recording permission is missing or was revoked | Grant it under **Privacy & Security**, then reopen the app if macOS requires it |
| The image works but visible text is missing | Accessibility permission, AT-SPI, or usable accessibility nodes are absent | Fix that platform support; optionally install and enable OCR |
| A privacy rule blocks a safe window | A denylist fragment or custom pattern is too broad | Narrow the entry, save, and retry only after checking what it matches |
| A capture disappears before API consumption | It was already consumed, discarded, or its retention time expired | Make a fresh explicit request; single-use captures cannot be recovered |

## Next Steps

- Read [Computer Use](computer-use) before allowing clicks, typing, or other
  desktop changes.
- Review [Permissions](permissions) for platform-specific access and recovery.
- Configure a vision-capable option in [Providers and API Keys](providers-and-api-keys).
- Use the [CLI Reference](cli-reference) for authenticated status and automation
  checks without exposing the local API publicly.
