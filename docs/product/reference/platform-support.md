---
title: "Platform Support and Requirements"
slug: platform-support
summary: "Check operating-system support, optional hardware and audio capabilities, headless behavior, and graceful feature fallbacks."
section: "Reference"
section_order: 7
order: 6
diataxis: reference
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [platforms, windows, macos, linux, headless, requirements, compatibility]
related: [install-personal-jarvis, permissions, configuration-reference, troubleshooting]
---

Personal Jarvis runs on Windows, macOS, Linux desktops, and headless Linux.
Its browser interface, chats, settings, tasks, and Jarvis-Agent views share one
server across platforms. Hardware-facing features such as local audio, global
shortcuts, overlays, screen capture, and input control depend on the host.

Jarvis checks the operating system, display, dependencies, and permissions
before enabling those features. An unavailable capability should leave the
rest of the app usable and state what is missing.

## Baseline Requirements

| Requirement | Current support | Why it matters |
|---|---|---|
| Operating system | 64-bit Windows, macOS, or glibc-based Linux on x86-64 or Arm64 | The dependency gate covers both processor families; the Linux wheel floor is glibc 2.28 |
| Python | 3.11 through 3.14 | The package requires Python 3.11 or newer and does not yet accept Python 3.15 |
| Git | A working Git command; no minimum version is enforced | The installer fetches Jarvis, and Jarvis-Agent missions use isolated Git worktrees |
| Internet connection | Required for installation; normally required for online providers and downloads | An already prepared local feature can keep working offline, but online models and services cannot |
| Writable user storage | Required | Jarvis needs space for its environment, settings, models, logs, and user data |

AI replies need a provider credential or supported subscription login, added
in **API Keys & Providers** after installation.

The installer can add missing Python or Git through a supported package
manager. It prefers Python versions with broad local-speech coverage; Python
3.14 is accepted, but not every optional native voice package supports it.

No Windows or macOS release number is enforced. Windows 11 is the recorded
live host. The Apple Silicon voice dependency floor supports macOS 13, but no
live macOS desktop sign-off exists. Older releases are unverified.

## Install Profiles

| Profile | Included surface | Intended host |
|---|---|---|
| **Full** | Desktop app, platform-specific desktop components, supported local voice components, telephony support, and chat-channel support | A Windows, macOS, or Linux computer with a graphical desktop |
| **Headless** | The smaller base application, local server, browser interface, API, and WebSocket service | A Linux server, container, or computer without a display |

The one-line installer chooses **Full** on a desktop. Linux switches to
**Headless** when neither X11 nor Wayland is present; `--headless` selects it
explicitly.

Platform markers skip incompatible native packages. A successful full install
therefore does not promise every optional speech engine. Required profile or
desktop-registration failures stop installation.

Headless omits desktop extras, registration, overlays, global shortcuts, and
physical input control. It starts the web server unless launch is disabled.

## Feature Support Matrix

| Feature | Windows desktop | macOS desktop | Linux desktop | Headless Linux |
|---|---|---|---|---|
| Web app, chat, settings, Docs, tasks, outputs | Supported | Supported | Supported | Browser only |
| Registered desktop app | Supported | App bundle | Application-menu entry | Unavailable |
| Local microphone and speakers | Device-dependent | Permission-dependent | Needs PortAudio and device access | Unavailable |
| Browser voice | Browser-dependent | Browser-dependent | Browser-dependent | Supported; remote mic needs HTTPS |
| Wake word and local speech | Capability-dependent | Capability-dependent | Capability-dependent | Use browser voice or a channel |
| Global voice shortcuts | Supported | Permission-dependent | X11 with a compatible backend; Full does not install it | Unavailable |
| Login autostart | Scheduled task or Startup shortcut | LaunchAgent | XDG autostart | Unavailable |
| Jarvis Bar and mascot | Supported | Supported through a companion | Best effort | No on-screen surface |
| Computer Use | Supported | Permission-dependent | X11 and capability-dependent | Unavailable |
| In-app notices | Supported | Supported | Supported | Browser only; no native notification promise |

**Capability-dependent** means a missing dependency, device, display, or
permission can disable that feature on an otherwise supported computer.

## Voice, Audio, and Wake Differences

Desktop voice needs a microphone, speaker, and audio backend. Linux also needs
PortAudio, often packaged as `libportaudio2`, and device access. After hardware
changes, choose **Settings > Audio devices > Rescan devices**.

PyTorch and a graphics processor are not required. Supported local voice paths
run on a CPU; a compatible GPU can accelerate eligible offline speech work.

Windows on ARM and some Python 3.14 cells lack WebRTC or local Whisper wheels.
Jarvis reports the unavailable engine while simpler local detection, browser
voice, or configured online speech remains available where supported.

Browser voice does not use the server's audio devices. Localhost works over
HTTP, but a remote browser microphone needs HTTPS. Jarvis listens on loopback
by default; remote use needs authentication, a non-loopback bind, and a trusted
proxy or tunnel. Never expose the local service directly. See the
[Control API Reference](control-api-reference).

## Desktop and Computer Use Differences

At startup, Jarvis records whether the host has a display, hotkey and terminal
backends, an accessibility tree, an overlay, cursor access, and elevation.
Failed probes report **unavailable** instead of stopping startup.

Windows uses UI Automation and native input. macOS Computer Use needs the
matching Accessibility and Screen Recording permissions. Linux uses AT-SPI
when its packages and desktop bus are available. Without a native interface
tree, screenshots and pixel positions can work but cannot identify controls by
name.

- **Wayland:** global shortcuts, cursor access, window control, and synthetic
  input are restricted. Computer Use refuses unsupported actions; XWayland is
  not a complete control backend.
- **Headless:** there is no display to capture or control. Chat, APIs, browser
  views, missions, and file work still run.

On macOS, grant permissions to **Personal Jarvis**, not Terminal or Python, so
they stay attached to the app bundle. The Bar and mascot use its desktop
companion.

Linux overlays are best effort. With no compatible X11, compositor, and Tk
surface, Jarvis falls back to the tray or no visible surface; chat continues.

## Optional Components

| Component | Required? | What it adds |
|---|---|---|
| Provider credential or subscription login | Required for AI replies, not for installation | Chat and the provider-backed features assigned to that connection |
| Node.js 18 or newer | No | Optional Jarvis-Agent worker command-line tools and some Node-based integrations |
| GPU | No | Faster eligible offline speech processing |
| Linux PortAudio library | Only for local Linux audio | Physical microphone and speaker access through the desktop pipeline |
| Linux `xdotool` and `wmctrl` | Only for full X11 window and input control | Window discovery, focus, movement, and reliable non-ASCII typing; the installer offers these tools when it can |
| Linux GTK WebKit and GObject Introspection packages | Only for the native Linux app window | The desktop WebView; the browser interface remains available without it |
| Linux AT-SPI packages and desktop bus | Only for native Linux UI labels | Named interface elements for more reliable Computer Use on X11 |
| Linux global-hotkey backend | Only for global voice shortcuts on X11 | Shortcut capture; it is not part of the current Linux full-profile dependency set |
| Xcode Command Line Tools on macOS | Required to build the managed desktop launcher | The installer compiles and signs the local app launcher and stops with an installation hint when `clang` is unavailable |
| macOS privacy grants | Only for the feature named by each grant | Microphone, global shortcuts, screen capture, accessibility, and input control |
| Graphical display | Only for desktop surfaces | Desktop window, overlays, screen capture, and physical Computer Use |

## Storage and Network Defaults

The managed installer uses `%USERPROFILE%\.personal-jarvis` on Windows and
`~/.personal-jarvis` on macOS and Linux. Its Python environment lives inside
that folder. Normal runtime data uses `%LOCALAPPDATA%\Jarvis` on Windows when
that location exists and `~/.jarvis` elsewhere. Some managed-install state,
including the default `jarvis.toml`, remains in the install folder. Read the
[Configuration Reference](configuration-reference) before changing a path on a
managed or read-only host.

The local service listens on loopback unless an operator explicitly configures
a different bind address. A non-loopback listener requires a Control key and
still needs a trusted network boundary and HTTPS for remote browser audio.

## Graceful Fallbacks

| Preferred capability is missing | Expected result |
|---|---|
| Display | Use the headless server and browser |
| Local audio | Keep text chat; choose browser voice or a channel |
| Local speech engine | Choose another installed engine, browser voice, or online speech |
| Global shortcut | Start voice in the app or use a working wake word |
| Accessibility tree | Use screenshot and pixel actions when capture and input work |
| Overlay or tray | Continue without an on-screen voice surface |
| Preferred provider | Cross provider families when supported, or report unavailable |

A fallback must not claim success. Jarvis should show an unavailable status,
log, or refusal that names the missing capability.

## Known Platform Limits

These are the open differences recorded by the current platform audit. They
are not implied future support.

| ID | Current limit | What you see |
|---|---|---|
| P-02 | Wayland has no global idle-time backend | The idle-awareness watcher logs that it cannot start |
| P-03 | Wayland hides the foreground window | The window-focus watcher logs that it cannot start |
| P-04 | Linux non-ASCII typing needs `xdotool` | Without it, a warning appears and some characters can be lost |
| P-05 | Some old or unusual Linux SQLite builds lack full-text search version 5 | Wiki search stops with an installation hint instead of returning incomplete results |
| P-07 | macOS and Linux audio selection has no host-API preference table | Automatic selection follows operating-system device order and can be less accurate than on Windows |
| P-10 | macOS cannot reap a mission worker after the orchestrator itself receives an uncatchable kill | Normal cancel and shutdown paths clean up; this narrow crash case can leave a worker for the operating system to adopt |
| P-12 | Frozen Windows-only Computer Use loops remain in the source but are not on the live path | No current user-facing effect |
| P-13 | A read-only wheel layout needs an explicit writable data location for two legacy Wiki paths | Managed installs are unaffected; an advanced read-only deployment must set its writable data directory |
| P-14 | Native macOS and Linux Computer Use depends on optional desktop packages | Missing packages remove named-element access; screenshot and pixel actions remain only when capture and input are available |
| P-15 | Linux has no native saved-file drag source | **Show in folder** and **Open** work, but the saved-file notice is not a drag handle |
| P-16 | Windows cannot immediately prove that a Wiki lock owner has died | After a crash, a stale Wiki lock can remain for up to five minutes |

## Verification Status

Recorded evidence includes Windows desktop checks from 2026-05-30 and a
headless `python:3.11-slim` Linux base-install and import check from 2026-06-20.
It has no dated live macOS or Linux GUI sign-off for hotkeys, accessibility
trees, overlays, or elevation. Implemented and tested code paths do not prove
live desktop behavior. The dated checks also do not prove a later release
candidate.

## How It Fits Together

1. **The installer chooses Full or Headless.** Platform markers skip
   incompatible optional packages.
2. **Startup probes the host.** Jarvis records the display, desktop backends,
   input access, and accessibility interfaces.
3. **Permissions and settings narrow the result.** A package can be installed
   while its device, operating-system grant, or provider is unavailable.
4. **Each feature chooses a working path or refuses honestly.** Chat can stay
   healthy while a shortcut, overlay, speech engine, or screen action is off.

The operating system sets what is possible, permissions set what is allowed,
and settings choose among the available paths.

## Check That It Works

With Personal Jarvis running, check the shared service first:

```text
jarvis --json system status
```

Success confirms that the command reaches Jarvis, not that every desktop
capability works.

On a desktop, choose **Settings > Audio devices > Rescan devices** and confirm
the intended devices appear. Then run the relevant **Test wake word** action,
macOS permission check, or a reversible Computer Use action.

On headless Linux, open the printed local address and test text chat first.
Configure authenticated HTTPS access before using a remote browser microphone.

## Troubleshooting

| What you see | Likely boundary | What to do |
|---|---|---|
| Python is rejected | Outside 3.11 through 3.14 | Let the installer choose, or install a supported version |
| Linux opens a server | No X11 or Wayland display | Use the printed address, or install from a graphical session |
| Linux audio is unavailable | PortAudio, access, or device missing | Install PortAudio, reconnect, then choose **Rescan devices** |
| A macOS shortcut or screen action fails | Privacy grant missing | Review **Settings > Privacy permissions** and restart when asked |
| Linux Computer Use refuses | Wayland or no display | Use X11; text and browser features still work |
| Linux control names are missing | AT-SPI or its bus is unavailable | Install accessibility packages; pixel fallback may work |
| A local voice engine is unavailable | No compatible native package | Choose another local, browser, or online speech path |
| Bar, mascot, or tray is absent | Surface or compositor unsupported | Keep using the app; change appearance if offered |

## Next Steps

- Follow [Install Personal Jarvis](install-personal-jarvis) for the supported
  full and headless installation paths.
- Review [App Permissions](permissions) before enabling microphone, shortcut,
  screen, accessibility, or input access.
- Use the [Configuration Reference](configuration-reference) to choose a
  feature path on managed or headless installations.
- Continue with [Troubleshooting](troubleshooting) when a capability that
  should be available on your host still fails its focused check.
